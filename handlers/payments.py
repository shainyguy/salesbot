from __future__ import annotations
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from database import get_user, log_event
from keyboards import MenuCB, PlanCB, plans_kb, back_kb
from payment_service import create_yookassa_payment

router = Router()


@router.callback_query(MenuCB.filter(F.action == "subscription"))
async def show_plans(callback: CallbackQuery, db_user, **kwargs):
    now = datetime.utcnow()
    current = db_user.plan
    expires = db_user.subscription_expires

    status = "🆓 Free"
    if expires and expires > now:
        days = (expires - now).days
        status = f"💎 {current.capitalize()} (ещё {days} дн.)"
    elif db_user.trial_ends and db_user.trial_ends > now:
        days = (db_user.trial_ends - now).days
        status = f"🎁 Триал (ещё {days} дн.)"

    text = (
        f"💎 <b>Подписка</b>\n\n"
        f"Текущий статус: {status}\n\n"
        f"<b>Доступные тарифы:</b>\n\n"
        f"🟢 <b>Pro — 990 ₽/мес</b>\n"
        f"  • до 100 лидов\n"
        f"  • 5 авто-дожимов\n"
        f"  • Аналитика + AI\n"
        f"  • Запись клиентов\n\n"
        f"🔵 <b>Business — 2 990 ₽/мес</b>\n"
        f"  • до 500 лидов\n"
        f"  • 20 авто-дожимов\n"
        f"  • Расширенная аналитика\n"
        f"  • Всё из Pro\n\n"
        f"🟣 <b>Enterprise — 9 990 ₽/мес</b>\n"
        f"  • Безлимит\n"
        f"  • Приоритетная поддержка\n"
        f"  • Всё из Business\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=plans_kb())
    await callback.answer()


@router.callback_query(PlanCB.filter())
async def process_plan_selection(callback: CallbackQuery, callback_data: PlanCB, db_user, **kwargs):
    plan = callback_data.name
    plan_info = config.PLANS.get(plan)
    if not plan_info:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    await callback.message.edit_text("⏳ Создаю платёж...")

    result = await create_yookassa_payment(
        user_telegram_id=callback.from_user.id,
        user_db_id=db_user.id,
        plan=plan,
    )

    if not result:
        await callback.message.edit_text(
            "❌ Ошибка создания платежа. Попробуйте позже.",
            reply_markup=back_kb(),
        )
        return

    await log_event(db_user.id, "payment_created", {"plan": plan, "amount": plan_info["price"]})

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=result["url"])],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_pay:{result['payment_id']}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCB(action="subscription").pack())],
    ])

    await callback.message.edit_text(
        f"💳 <b>Оплата: {plan_info['title']}</b>\n\n"
        f"Сумма: <b>{plan_info['price']} ₽</b>\n\n"
        f"Нажмите кнопку «Оплатить» для перехода на страницу оплаты.\n"
        f"После оплаты нажмите «Я оплатил».",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_pay:"))
async def check_payment(callback: CallbackQuery, db_user, **kwargs):
    from payment_service import check_payment_status
    from database import get_payment_by_yookassa, update_payment, update_user

    yookassa_id = callback.data.split(":")[1]
    status = await check_payment_status(yookassa_id)

    if status == "succeeded":
        payment = await get_payment_by_yookassa(yookassa_id)
        if payment and payment.status != "succeeded":
            from datetime import timedelta
            await update_payment(payment.id, status="succeeded", completed_at=datetime.utcnow())
            await update_user(
                callback.from_user.id,
                plan=payment.plan,
                subscription_expires=datetime.utcnow() + timedelta(days=30),
            )
        await callback.message.edit_text(
            "✅ <b>Оплата прошла успешно!</b>\n\n"
            "Ваша подписка активирована. Приятного использования! 🎉",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        await log_event(db_user.id, "payment_succeeded", {"yookassa_id": yookassa_id})
    elif status == "pending":
        await callback.answer("⏳ Платёж ещё обрабатывается. Подождите немного.", show_alert=True)
    else:
        await callback.answer(f"❌ Статус платежа: {status}", show_alert=True)