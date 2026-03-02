from __future__ import annotations
import csv
import io
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, BufferedInputFile

from config import config
from database import (
    count_users, get_revenue, count_active_subscriptions,
    get_all_users, get_lead_funnel, get_vip_leads, get_leaked_leads,
    get_events_count,
)
from keyboards import AdminCB, admin_kb, back_kb, MenuCB

router = Router()


def _admin_only(db_user) -> bool:
    return db_user and db_user.role == "admin"


@router.callback_query(MenuCB.filter(F.action == "admin"))
async def admin_panel(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("👑 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "stats"))
async def admin_stats(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    total = await count_users()
    quiz_completions = await get_events_count("quiz_complete", 30)
    diagnostics = await get_events_count("diagnostics", 30)
    ai_chats = await get_events_count("ai_chat", 30)
    starts = await get_events_count("start", 7)

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🆕 Новых за 7 дней: <b>{starts}</b>\n\n"
        f"📋 Квизов за 30 дней: <b>{quiz_completions}</b>\n"
        f"🔍 Диагностик: <b>{diagnostics}</b>\n"
        f"🤖 AI-чатов: <b>{ai_chats}</b>\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "revenue"))
async def admin_revenue(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    r_7 = await get_revenue(7)
    r_30 = await get_revenue(30)
    r_90 = await get_revenue(90)
    r_all = await get_revenue(3650)

    text = (
        "💰 <b>Доход</b>\n\n"
        f"📅 7 дней: <b>{r_7:,.0f} ₽</b>\n"
        f"📅 30 дней: <b>{r_30:,.0f} ₽</b>\n"
        f"📅 90 дней: <b>{r_90:,.0f} ₽</b>\n"
        f"📅 Всего: <b>{r_all:,.0f} ₽</b>\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "subs"))
async def admin_subs(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    subs = await count_active_subscriptions()
    text = "📋 <b>Подписки</b>\n\n"
    total_revenue_monthly = 0
    for plan, count in subs.items():
        price = config.PLANS[plan]["price"]
        text += f"• {plan}: <b>{count}</b>"
        if price > 0:
            text += f" ({count * price:,.0f} ₽/мес)"
            total_revenue_monthly += count * price
        text += "\n"
    text += f"\n💰 MRR: <b>{total_revenue_monthly:,.0f} ₽</b>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "users"))
async def admin_users(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    users = await get_all_users(limit=20)
    text = "👥 <b>Последние пользователи</b>\n\n"
    for u in users:
        status = "✅" if u.plan != "free" else "🆓"
        text += (
            f"{status} {u.full_name or u.username or u.telegram_id} "
            f"| {u.plan} | {u.created_at.strftime('%d.%m')}\n"
        )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "leaks"))
async def admin_leaks(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    leaked = await get_leaked_leads(db_user.id, hours=24)
    if not leaked:
        text = "✅ Утечек не найдено за 24 часа!"
    else:
        text = f"🚨 <b>Утечки заявок ({len(leaked)})</b>\n\n"
        for lead in leaked[:20]:
            hours_ago = (datetime.utcnow() - lead.created_at).total_seconds() / 3600
            text += (
                f"• {lead.name or 'Без имени'} "
                f"| {hours_ago:.0f} ч. назад "
                f"| Score: {lead.score}\n"
            )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "vip"))
async def admin_vip(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    vips = await get_vip_leads(db_user.id)
    if not vips:
        text = "⭐ VIP-лидов пока нет."
    else:
        text = f"⭐ <b>VIP-лиды ({len(vips)})</b>\n\n"
        for lead in vips[:20]:
            text += (
                f"• {lead.name or '—'} | Score: {lead.score} "
                f"| {lead.status} | {lead.phone or '—'}\n"
            )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "funnel"))
async def admin_funnel(callback: CallbackQuery, db_user, **kwargs):
    if not _admin_only(db_user):
        return

    funnel = await get_lead_funnel(db_user.id)
    total = sum(funnel.values()) or 1

    text = "📈 <b>Воронка продаж</b>\n\n"
    labels = {
        "new": "🆕 Новые",
        "contacted": "📞 Контакт",
        "qualified": "✅ Квалиф.",
        "converted": "💰 Продажа",
        "lost": "❌ Потеряно",
    }
    for st, label in labels.items():
        count = funnel.get(st, 0)
        pct = count / total * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        text += f"{label}: <b>{count}</b> ({pct:.0f}%)\n{bar}\n\n"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "export"))
async def admin_export(callback: CallbackQuery, db_user, bot: Bot, **kwargs):
    if not _admin_only(db_user):
        return

    users = await get_all_users(limit=10000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Telegram ID", "Username", "Name", "Plan", "Registered"])
    for u in users:
        writer.writerow([
            u.id, u.telegram_id, u.username or "",
            u.full_name or "", u.plan,
            u.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    content = output.getvalue().encode("utf-8-sig")
    doc = BufferedInputFile(content, filename=f"users_{datetime.utcnow().strftime('%Y%m%d')}.csv")
    await bot.send_document(chat_id=callback.from_user.id, document=doc, caption="📤 Экспорт пользователей")
    await callback.answer("✅ Файл отправлен")