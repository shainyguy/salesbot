from __future__ import annotations
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import get_user, update_user, log_event
from keyboards import (
    main_menu_kb, MenuCB, back_kb,
)
from ai_service import ai_business_consultant

router = Router()


class AIChat(StatesGroup):
    waiting = State()


@router.message(Command("start"))
async def cmd_start(message: Message, db_user, **kwargs):
    is_admin = db_user.role == "admin"
    await log_event(db_user.id, "start")

    welcome = (
        f"👋 Добро пожаловать, <b>{message.from_user.full_name}</b>!\n\n"
        "Я — <b>SalesBot</b>, ваш AI-помощник для автоматизации продаж.\n\n"
        "🔍 Проведу диагностику бизнеса\n"
        "💰 Рассчитаю потери денег\n"
        "🔁 Настрою авто-дожим\n"
        "📅 Автоматизирую запись\n"
        "📊 Покажу аналитику\n"
        "🤖 AI-консультант 24/7\n\n"
    )

    if db_user.trial_ends and db_user.trial_ends > datetime.utcnow() and db_user.plan == "free":
        days_left = (db_user.trial_ends - datetime.utcnow()).days
        welcome += f"🎁 Пробный период: <b>{days_left} дн.</b>\n\n"

    welcome += "Выберите действие 👇"

    await message.answer(welcome, parse_mode="HTML", reply_markup=main_menu_kb(is_admin))


@router.message(Command("menu"))
async def cmd_menu(message: Message, db_user, **kwargs):
    is_admin = db_user.role == "admin"
    await message.answer("📋 Главное меню:", reply_markup=main_menu_kb(is_admin))


@router.message(Command("help"))
async def cmd_help(message: Message, **kwargs):
    text = (
        "ℹ️ <b>Доступные команды:</b>\n\n"
        "/start — Запуск бота\n"
        "/menu — Главное меню\n"
        "/profile — Мой профиль\n"
        "/subscribe — Подписка\n"
        "/help — Помощь\n\n"
        "По вопросам: @support"
    )
    await message.answer(text, parse_mode="HTML")


# ── Профиль ────────────────────────────────────────────

@router.callback_query(MenuCB.filter(F.action == "profile"))
async def show_profile(callback: CallbackQuery, db_user, **kwargs):
    now = datetime.utcnow()
    plan_info = config.PLANS.get(db_user.plan, {})

    sub_status = "❌ Нет"
    if db_user.subscription_expires and db_user.subscription_expires > now:
        days = (db_user.subscription_expires - now).days
        sub_status = f"✅ Активна ({days} дн.)"
    elif db_user.trial_ends and db_user.trial_ends > now:
        days = (db_user.trial_ends - now).days
        sub_status = f"🎁 Триал ({days} дн.)"

    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"📛 Имя: {db_user.full_name or '—'}\n"
        f"🆔 ID: <code>{db_user.telegram_id}</code>\n"
        f"📊 Тариф: <b>{plan_info.get('title', db_user.plan)}</b>\n"
        f"💎 Подписка: {sub_status}\n"
        f"📅 Регистрация: {db_user.created_at.strftime('%d.%m.%Y')}\n"
        f"🔗 Реф. код: <code>{db_user.referral_code or '—'}</code>\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb())
    await callback.answer()


# ── Назад в меню ───────────────────────────────────────

@router.callback_query(MenuCB.filter(F.action == "back"))
async def back_to_menu(callback: CallbackQuery, db_user, **kwargs):
    is_admin = db_user.role == "admin"
    await callback.message.edit_text("📋 Главное меню:", reply_markup=main_menu_kb(is_admin))
    await callback.answer()


# ── AI-консультант ─────────────────────────────────────

@router.callback_query(MenuCB.filter(F.action == "ai"))
async def ai_menu(callback: CallbackQuery, state: FSMContext, db_user, has_premium, **kwargs):
    if not has_premium:
        await callback.answer("💎 Функция доступна по подписке", show_alert=True)
        return
    await callback.message.edit_text(
        "🤖 <b>AI-консультант</b>\n\n"
        "Задайте любой вопрос о продажах, воронках, конверсии.\n"
        "Напишите сообщение 👇",
        parse_mode="HTML",
        reply_markup=back_kb(),
    )
    await state.set_state(AIChat.waiting)
    await callback.answer()


@router.message(AIChat.waiting)
async def ai_response(message: Message, state: FSMContext, db_user, **kwargs):
    await message.answer("🤖 Думаю...")
    answer = await ai_business_consultant(message.text)
    await message.answer(answer, reply_markup=back_kb())
    await log_event(db_user.id, "ai_chat", {"question": message.text[:200]})
    await state.clear()