from __future__ import annotations
import uuid
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import (
    get_leads, get_lead, update_lead, create_lead,
    get_vip_leads, get_leaked_leads, get_lead_funnel,
    create_followup, log_event,
)
from keyboards import (
    MenuCB, CrmCB, LeadCB,
    crm_kb, lead_actions_kb, back_kb,
)
from ai_service import generate_followup_message

router = Router()


class NewLeadState(StatesGroup):
    name = State()
    phone = State()
    notes = State()


# ── Меню CRM ──────────────────────────────────────────

@router.callback_query(MenuCB.filter(F.action == "crm"))
async def crm_menu(callback: CallbackQuery, db_user, has_premium, **kwargs):
    if not has_premium:
        await callback.answer("💎 Функция доступна по подписке", show_alert=True)
        return
    await callback.message.edit_text(
        "📊 <b>Мини-CRM</b>\n\nУправляйте лидами прямо в Telegram:",
        parse_mode="HTML",
        reply_markup=crm_kb(),
    )
    await callback.answer()


# ── Новый лид ─────────────────────────────────────────

@router.callback_query(CrmCB.filter(F.action == "new_lead"))
async def new_lead_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.set_state(NewLeadState.name)
    await callback.message.edit_text(
        "➕ <b>Новый лид</b>\n\nВведите имя клиента:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(NewLeadState.name)
async def new_lead_name(message: Message, state: FSMContext, **kwargs):
    await state.update_data(name=message.text)
    await state.set_state(NewLeadState.phone)
    await message.answer("📞 Введите телефон клиента (или — чтобы пропустить):")


@router.message(NewLeadState.phone)
async def new_lead_phone(message: Message, state: FSMContext, **kwargs):
    phone = message.text if message.text != "—" else None
    await state.update_data(phone=phone)
    await state.set_state(NewLeadState.notes)
    await message.answer("📝 Заметки (или — чтобы пропустить):")


@router.message(NewLeadState.notes)
async def new_lead_notes(message: Message, state: FSMContext, db_user, **kwargs):
    data = await state.get_data()
    await state.clear()

    notes = message.text if message.text != "—" else None
    lead = await create_lead(
        owner_id=db_user.id,
        name=data["name"],
        phone=data.get("phone"),
        notes=notes,
        source="manual",
    )
    await log_event(db_user.id, "lead_created", {"name": data["name"]})

    await message.answer(
        f"✅ Лид создан!\n\n"
        f"👤 {lead.name}\n"
        f"📞 {lead.phone or '—'}\n"
        f"🆔 #{lead.id}",
        reply_markup=lead_actions_kb(lead.id),
    )


# ── Список лидов ──────────────────────────────────────

@router.callback_query(CrmCB.filter(F.action == "list_leads"))
async def list_leads(callback: CallbackQuery, db_user, **kwargs):
    leads = await get_leads(db_user.id, limit=20)
    if not leads:
        await callback.message.edit_text("📋 Лидов пока нет.", reply_markup=crm_kb())
        await callback.answer()
        return

    text = f"📋 <b>Лиды ({len(leads)})</b>\n\n"
    status_icons = {
        "new": "🆕", "contacted": "📞", "qualified": "✅",
        "converted": "💰", "lost": "❌",
    }
    for lead in leads:
        icon = status_icons.get(lead.status, "•")
        vip = "⭐" if lead.is_vip else ""
        text += f"{icon}{vip} <b>{lead.name or '—'}</b> | {lead.phone or '—'} | #{lead.id}\n"

    # Кнопки для первых 5 лидов
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = []
    for lead in leads[:5]:
        buttons.append([
            InlineKeyboardButton(
                text=f"👁 {lead.name or lead.id}",
                callback_data=LeadCB(action="view", lead_id=lead.id).pack(),
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="◀️ CRM", callback_data=MenuCB(action="crm").pack())
    ])

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ── Просмотр лида ─────────────────────────────────────

@router.callback_query(LeadCB.filter(F.action == "view"))
async def view_lead(callback: CallbackQuery, callback_data: LeadCB, **kwargs):
    lead = await get_lead(callback_data.lead_id)
    if not lead:
        await callback.answer("❌ Лид не найден", show_alert=True)
        return

    vip = "⭐ VIP" if lead.is_vip else ""
    text = (
        f"👤 <b>{lead.name or '—'}</b> {vip}\n\n"
        f"📞 Телефон: {lead.phone or '—'}\n"
        f"📧 Email: {lead.email or '—'}\n"
        f"📊 Статус: {lead.status}\n"
        f"🏆 Score: {lead.score}\n"
        f"📝 Источник: {lead.source or '—'}\n"
        f"📅 Создан: {lead.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    )
    if lead.notes:
        text += f"📝 Заметки: {lead.notes}\n"

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=lead_actions_kb(lead.id),
    )
    await callback.answer()


# ── Действия с лидом ──────────────────────────────────

@router.callback_query(LeadCB.filter(F.action.in_({"contacted", "qualified", "converted", "lost"})))
async def change_lead_status(callback: CallbackQuery, callback_data: LeadCB, db_user, **kwargs):
    status = callback_data.action
    lead_id = callback_data.lead_id

    extra = {}
    if status == "contacted" :
        extra["first_contact_at"] = datetime.utcnow()
        extra["response_time_min"] = None
        lead = await get_lead(lead_id)
        if lead:
            diff = (datetime.utcnow() - lead.created_at).total_seconds() / 60
            extra["response_time_min"] = int(diff)

    await update_lead(lead_id, status=status, **extra)
    await log_event(db_user.id, "lead_status_change", {"lead_id": lead_id, "status": status})
    await callback.answer(f"✅ Статус изменён: {status}")

    # Обновляем карточку
    await view_lead(callback, callback_data=LeadCB(action="view", lead_id=lead_id))


@router.callback_query(LeadCB.filter(F.action == "vip"))
async def toggle_vip(callback: CallbackQuery, callback_data: LeadCB, db_user, **kwargs):
    lead = await get_lead(callback_data.lead_id)
    if not lead:
        return
    new_vip = not lead.is_vip
    await update_lead(lead.id, is_vip=new_vip)
    status = "добавлен в VIP" if new_vip else "убран из VIP"
    await callback.answer(f"⭐ Лид {status}")
    await view_lead(callback, callback_data=LeadCB(action="view", lead_id=lead.id))


# ── Авто-дожим ────────────────────────────────────────

@router.callback_query(LeadCB.filter(F.action == "followup"))
async def start_followup(callback: CallbackQuery, callback_data: LeadCB, db_user, has_premium, **kwargs):
    if not has_premium:
        await callback.answer("💎 Дожим доступен по подписке", show_alert=True)
        return

    lead = await get_lead(callback_data.lead_id)
    if not lead:
        return

    chain_id = uuid.uuid4().hex[:8]
    lead_info = {
        "name": lead.name, "phone": lead.phone,
        "score": lead.score, "source": lead.source,
    }

    intervals = [
        timedelta(hours=1),
        timedelta(days=1),
        timedelta(days=2),
        timedelta(days=3),
    ]

    count = 0
    for step, delta in enumerate(intervals, 1):
        try:
            msg_text = await generate_followup_message(lead_info, step)
        except Exception:
            msg_text = f"Добрый день! Напоминаю о нашем предложении. Шаг {step}/4."

        await create_followup(
            lead_id=lead.id,
            owner_id=db_user.id,
            chain_id=chain_id,
            step=step,
            message_text=msg_text,
            scheduled_at=datetime.utcnow() + delta,
        )
        count += 1

    await log_event(db_user.id, "followup_created", {"lead_id": lead.id, "steps": count})
    await callback.answer(f"🔁 Создано {count} дожимающих сообщений", show_alert=True)


# ── Горячие лиды ──────────────────────────────────────

@router.callback_query(CrmCB.filter(F.action == "hot_leads"))
async def hot_leads(callback: CallbackQuery, db_user, **kwargs):
    leads = await get_leads(db_user.id)
    hot = [l for l in leads if l.score >= 60 and l.status not in ("converted", "lost")]

    if not hot:
        await callback.message.edit_text("🔥 Горячих лидов нет.", reply_markup=crm_kb())
        await callback.answer()
        return

    text = f"🔥 <b>Горячие лиды ({len(hot)})</b>\n\n"
    for lead in hot[:15]:
        vip = "⭐" if lead.is_vip else ""
        text += f"{vip} <b>{lead.name or '—'}</b> | Score: {lead.score} | #{lead.id}\n"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=crm_kb())
    await callback.answer()


# ── VIP-лиды ──────────────────────────────────────────

@router.callback_query(CrmCB.filter(F.action == "vip_leads"))
async def vip_leads_list(callback: CallbackQuery, db_user, **kwargs):
    vips = await get_vip_leads(db_user.id)
    if not vips:
        await callback.message.edit_text("⭐ VIP-лидов пока нет.", reply_markup=crm_kb())
    else:
        text = f"⭐ <b>VIP-лиды ({len(vips)})</b>\n\n"
        for lead in vips[:15]:
            text += f"• <b>{lead.name or '—'}</b> | Score: {lead.score} | {lead.status}\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=crm_kb())
    await callback.answer()


# ── Неотвеченные ──────────────────────────────────────

@router.callback_query(CrmCB.filter(F.action == "leaked"))
async def leaked_leads(callback: CallbackQuery, db_user, **kwargs):
    leaked = await get_leaked_leads(db_user.id, hours=24)
    if not leaked:
        await callback.message.edit_text("✅ Все заявки обработаны!", reply_markup=crm_kb())
    else:
        text = f"🚨 <b>Неотвеченные ({len(leaked)})</b>\n\n"
        for lead in leaked[:15]:
            h = (datetime.utcnow() - lead.created_at).total_seconds() / 3600
            text += f"• {lead.name or '—'} | {h:.0f}ч назад | #{lead.id}\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=crm_kb())
    await callback.answer()


# ── Воронка ───────────────────────────────────────────

@router.callback_query(CrmCB.filter(F.action == "funnel"))
async def crm_funnel(callback: CallbackQuery, db_user, **kwargs):
    funnel = await get_lead_funnel(db_user.id)
    total = sum(funnel.values()) or 1

    labels = {
        "new": "🆕 Новые", "contacted": "📞 Контакт",
        "qualified": "✅ Квалиф.", "converted": "💰 Продажа", "lost": "❌ Потеряно",
    }

    text = "📈 <b>Воронка</b>\n\n"
    for st, label in labels.items():
        c = funnel.get(st, 0)
        pct = c / total * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        text += f"{label}: <b>{c}</b> ({pct:.0f}%)\n{bar}\n\n"

    conv = funnel.get("converted", 0)
    total_leads = funnel.get("new", 0) + funnel.get("contacted", 0) + funnel.get("qualified", 0) + conv + funnel.get("lost", 0)
    if total_leads:
        text += f"📊 Общая конверсия: <b>{conv / total_leads * 100:.1f}%</b>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=crm_kb())
    await callback.answer()