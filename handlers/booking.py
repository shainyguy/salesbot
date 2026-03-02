from __future__ import annotations
from datetime import datetime, date as dt_date, time as dt_time

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import create_booking, create_lead, get_leads, log_event
from keyboards import (
    MenuCB, BookCB,
    services_kb, dates_kb, times_kb, confirm_booking_kb, back_kb,
)

router = Router()


class BookingState(StatesGroup):
    choose_service = State()
    choose_date = State()
    choose_time = State()
    confirm = State()


@router.callback_query(MenuCB.filter(F.action == "booking"))
async def booking_start(callback: CallbackQuery, state: FSMContext, db_user, has_premium, **kwargs):
    if not has_premium:
        await callback.answer("💎 Функция доступна по подписке", show_alert=True)
        return
    await state.set_state(BookingState.choose_service)
    await callback.message.edit_text(
        "📅 <b>Запись на услугу</b>\n\nВыберите услугу:",
        parse_mode="HTML",
        reply_markup=services_kb(),
    )
    await callback.answer()


@router.callback_query(BookCB.filter(F.action == "service"), BookingState.choose_service)
async def booking_service(callback: CallbackQuery, callback_data: BookCB, state: FSMContext, **kwargs):
    await state.update_data(service=callback_data.value)
    await state.set_state(BookingState.choose_date)
    await callback.message.edit_text(
        f"📅 Услуга: <b>{callback_data.value}</b>\n\nВыберите дату:",
        parse_mode="HTML",
        reply_markup=dates_kb(),
    )
    await callback.answer()


@router.callback_query(BookCB.filter(F.action == "date"), BookingState.choose_date)
async def booking_date(callback: CallbackQuery, callback_data: BookCB, state: FSMContext, **kwargs):
    await state.update_data(date=callback_data.value)
    await state.set_state(BookingState.choose_time)
    data = await state.get_data()
    await callback.message.edit_text(
        f"📅 Услуга: <b>{data['service']}</b>\n"
        f"📆 Дата: <b>{callback_data.value}</b>\n\n"
        f"Выберите время:",
        parse_mode="HTML",
        reply_markup=times_kb(),
    )
    await callback.answer()


@router.callback_query(BookCB.filter(F.action == "time"), BookingState.choose_time)
async def booking_time(callback: CallbackQuery, callback_data: BookCB, state: FSMContext, **kwargs):
    await state.update_data(time=callback_data.value)
    await state.set_state(BookingState.confirm)
    data = await state.get_data()
    await callback.message.edit_text(
        f"📅 <b>Подтвердите запись:</b>\n\n"
        f"🔹 Услуга: {data['service']}\n"
        f"📆 Дата: {data['date']}\n"
        f"🕐 Время: {data['time']}\n",
        parse_mode="HTML",
        reply_markup=confirm_booking_kb(),
    )
    await callback.answer()


@router.callback_query(BookCB.filter(F.action == "confirm"), BookingState.confirm)
async def booking_confirm(callback: CallbackQuery, state: FSMContext, db_user, **kwargs):
    data = await state.get_data()
    await state.clear()

    # Создаём лида если нет
    leads = await get_leads(db_user.id)
    own_lead = next(
        (l for l in leads if l.telegram_id == callback.from_user.id), None
    )
    if not own_lead:
        own_lead = await create_lead(
            owner_id=db_user.id,
            telegram_id=callback.from_user.id,
            name=callback.from_user.full_name,
            source="booking",
        )

    book_date = dt_date.fromisoformat(data["date"])
    h, m = data["time"].split(":")
    book_time = dt_time(int(h), int(m))

    booking = await create_booking(
        lead_id=own_lead.id,
        owner_id=db_user.id,
        service=data["service"],
        book_date=book_date,
        book_time=book_time,
    )
    await log_event(db_user.id, "booking_created", {
        "service": data["service"],
        "date": data["date"],
        "time": data["time"],
    })

    await callback.message.edit_text(
        f"✅ <b>Запись подтверждена!</b>\n\n"
        f"🔹 Услуга: {data['service']}\n"
        f"📆 Дата: {data['date']}\n"
        f"🕐 Время: {data['time']}\n"
        f"🆔 ID записи: #{booking.id}\n\n"
        f"Напоминание будет отправлено за 1 час.",
        parse_mode="HTML",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(BookCB.filter(F.action == "cancel"))
async def booking_cancel(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await callback.message.edit_text("❌ Запись отменена.", reply_markup=back_kb())
    await callback.answer()