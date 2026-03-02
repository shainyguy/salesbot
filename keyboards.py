from __future__ import annotations
from datetime import datetime, timedelta

from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.filters.callback_data import CallbackData
from config import config


# ── Callback Factories ─────────────────────────────────

class MenuCB(CallbackData, prefix="menu"):
    action: str


class QuizCB(CallbackData, prefix="quiz"):
    step: int
    answer: str


class PlanCB(CallbackData, prefix="plan"):
    name: str


class LeadCB(CallbackData, prefix="lead"):
    action: str
    lead_id: int = 0


class BookCB(CallbackData, prefix="book"):
    action: str
    value: str = ""


class AdminCB(CallbackData, prefix="adm"):
    action: str
    value: str = ""


class CrmCB(CallbackData, prefix="crm"):
    action: str
    value: str = ""


# ── Main Menu ──────────────────────────────────────────

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔍 Диагностика бизнеса", callback_data=MenuCB(action="diagnostics").pack())],
        [InlineKeyboardButton(text="💰 Расчёт потерь", callback_data=MenuCB(action="losses").pack())],
        [InlineKeyboardButton(text="📋 Квиз-воронка", callback_data=MenuCB(action="quiz").pack())],
        [InlineKeyboardButton(text="📅 Запись", callback_data=MenuCB(action="booking").pack())],
        [InlineKeyboardButton(text="📊 Мини-CRM", callback_data=MenuCB(action="crm").pack())],
        [InlineKeyboardButton(text="🤖 AI-консультант", callback_data=MenuCB(action="ai").pack())],
        [InlineKeyboardButton(text="👤 Профиль", callback_data=MenuCB(action="profile").pack()),
         InlineKeyboardButton(text="💎 Подписка", callback_data=MenuCB(action="subscription").pack())],
    ]
    if is_admin:
        buttons.append(
            [InlineKeyboardButton(text="👑 Админ-панель", callback_data=MenuCB(action="admin").pack())]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Quiz ───────────────────────────────────────────────

QUIZ_QUESTIONS: list[dict] = [
    {
        "text": "1️⃣ Какая у вас ниша?",
        "answers": [
            ("Услуги", "services"), ("Товары", "goods"),
            ("Онлайн-образование", "education"), ("Другое", "other"),
        ],
    },
    {
        "text": "2️⃣ Сколько заявок в месяц вы получаете?",
        "answers": [
            ("< 30", "low"), ("30–100", "mid"),
            ("100–500", "high"), ("> 500", "very_high"),
        ],
    },
    {
        "text": "3️⃣ Какой средний чек?",
        "answers": [
            ("до 5 000 ₽", "5k"), ("5 000–30 000 ₽", "30k"),
            ("30 000–100 000 ₽", "100k"), ("> 100 000 ₽", "100k+"),
        ],
    },
    {
        "text": "4️⃣ Какая конверсия из заявки в продажу?",
        "answers": [
            ("< 5 %", "low"), ("5–15 %", "mid"),
            ("15–30 %", "good"), ("> 30 %", "great"),
        ],
    },
    {
        "text": "5️⃣ Среднее время ответа на заявку?",
        "answers": [
            ("< 5 мин", "fast"), ("5–30 мин", "normal"),
            ("30 мин – 2 ч", "slow"), ("> 2 ч", "very_slow"),
        ],
    },
    {
        "text": "6️⃣ Есть ли CRM-система?",
        "answers": [("Да", "yes"), ("Нет", "no")],
    },
    {
        "text": "7️⃣ Есть ли система повторных продаж?",
        "answers": [("Да", "yes"), ("Нет", "no")],
    },
    {
        "text": "8️⃣ Контролируете ли вы утечки заявок?",
        "answers": [("Да", "yes"), ("Частично", "partial"), ("Нет", "no")],
    },
]


def quiz_kb(step: int) -> InlineKeyboardMarkup:
    q = QUIZ_QUESTIONS[step]
    buttons = [
        [InlineKeyboardButton(
            text=label,
            callback_data=QuizCB(step=step, answer=value).pack(),
        )]
        for label, value in q["answers"]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Subscription Plans ─────────────────────────────────

def plans_kb() -> InlineKeyboardMarkup:
    buttons = []
    for name, info in config.PLANS.items():
        if info["price"] == 0:
            continue
        buttons.append([
            InlineKeyboardButton(
                text=f"💎 {info['title']}",
                callback_data=PlanCB(name=name).pack(),
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCB(action="back").pack())])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Admin Panel ────────────────────────────────────────

def admin_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data=AdminCB(action="stats").pack())],
        [InlineKeyboardButton(text="💰 Доход", callback_data=AdminCB(action="revenue").pack())],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data=AdminCB(action="users").pack())],
        [InlineKeyboardButton(text="📋 Подписки", callback_data=AdminCB(action="subs").pack())],
        [InlineKeyboardButton(text="🚨 Утечки", callback_data=AdminCB(action="leaks").pack())],
        [InlineKeyboardButton(text="⭐ VIP-лиды", callback_data=AdminCB(action="vip").pack())],
        [InlineKeyboardButton(text="📤 Экспорт базы", callback_data=AdminCB(action="export").pack())],
        [InlineKeyboardButton(text="📈 Воронка", callback_data=AdminCB(action="funnel").pack())],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCB(action="back").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── CRM ────────────────────────────────────────────────

def crm_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="➕ Новый лид", callback_data=CrmCB(action="new_lead").pack())],
        [InlineKeyboardButton(text="📋 Все лиды", callback_data=CrmCB(action="list_leads").pack())],
        [InlineKeyboardButton(text="🔥 Горячие", callback_data=CrmCB(action="hot_leads").pack())],
        [InlineKeyboardButton(text="⭐ VIP", callback_data=CrmCB(action="vip_leads").pack())],
        [InlineKeyboardButton(text="🚨 Неотвеченные", callback_data=CrmCB(action="leaked").pack())],
        [InlineKeyboardButton(text="📊 Воронка", callback_data=CrmCB(action="funnel").pack())],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCB(action="back").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def lead_actions_kb(lead_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📞 Связался", callback_data=LeadCB(action="contacted", lead_id=lead_id).pack()),
            InlineKeyboardButton(text="✅ Квалиф.", callback_data=LeadCB(action="qualified", lead_id=lead_id).pack()),
        ],
        [
            InlineKeyboardButton(text="💰 Продажа", callback_data=LeadCB(action="converted", lead_id=lead_id).pack()),
            InlineKeyboardButton(text="❌ Потерян", callback_data=LeadCB(action="lost", lead_id=lead_id).pack()),
        ],
        [
            InlineKeyboardButton(text="⭐ VIP", callback_data=LeadCB(action="vip", lead_id=lead_id).pack()),
            InlineKeyboardButton(text="🔁 Дожим", callback_data=LeadCB(action="followup", lead_id=lead_id).pack()),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data=CrmCB(action="list_leads").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Booking ────────────────────────────────────────────

SERVICES = ["Консультация", "Аудит", "Стратегическая сессия", "Настройка воронки"]


def services_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=s, callback_data=BookCB(action="service", value=s).pack()
        )]
        for s in SERVICES
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCB(action="back").pack())])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def dates_kb() -> InlineKeyboardMarkup:
    buttons = []
    today = datetime.utcnow().date()
    for i in range(1, 8):
        d = today + timedelta(days=i)
        buttons.append([
            InlineKeyboardButton(
                text=d.strftime("%d.%m (%a)"),
                callback_data=BookCB(action="date", value=d.isoformat()).pack(),
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def times_kb() -> InlineKeyboardMarkup:
    slots = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00"]
    buttons = []
    row = []
    for t in slots:
        row.append(InlineKeyboardButton(text=t, callback_data=BookCB(action="time", value=t).pack()))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_booking_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=BookCB(action="confirm").pack()),
            InlineKeyboardButton(text="❌ Отменить", callback_data=BookCB(action="cancel").pack()),
        ]
    ])


# ── Helpers ────────────────────────────────────────────

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Меню", callback_data=MenuCB(action="back").pack())]
    ])