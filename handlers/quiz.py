from __future__ import annotations
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database import log_event, create_lead, update_user
from keyboards import (
    MenuCB, QuizCB, quiz_kb, back_kb, plans_kb,
    QUIZ_QUESTIONS,
)
from ai_service import generate_diagnostics_report

router = Router()

ANSWER_LABELS = {
    "services": "Услуги", "goods": "Товары", "education": "Онлайн-образование", "other": "Другое",
    "low": "Мало", "mid": "Средне", "high": "Много", "very_high": "Очень много",
    "5k": "до 5 000 ₽", "30k": "5 000–30 000 ₽", "100k": "30 000–100 000 ₽", "100k+": "> 100 000 ₽",
    "good": "Хорошая", "great": "Отличная",
    "fast": "< 5 мин", "normal": "5–30 мин", "slow": "30 мин – 2 ч", "very_slow": "> 2 ч",
    "yes": "Да", "no": "Нет", "partial": "Частично",
}

LEADS_MAP = {"low": 20, "mid": 65, "high": 300, "very_high": 700}
CHECK_MAP = {"5k": 3000, "30k": 15000, "100k": 60000, "100k+": 200000}
CONV_MAP = {"low": 3, "mid": 10, "good": 22, "great": 35}


class QuizState(StatesGroup):
    in_progress = State()


# ── Запуск квиза ───────────────────────────────────────

@router.callback_query(MenuCB.filter(F.action == "quiz"))
async def start_quiz(callback: CallbackQuery, state: FSMContext, db_user, **kwargs):
    await state.set_state(QuizState.in_progress)
    await state.update_data(quiz_answers={}, step=0)
    q = QUIZ_QUESTIONS[0]
    await callback.message.edit_text(
        f"📋 <b>Квиз-воронка</b>\n\n{q['text']}",
        parse_mode="HTML",
        reply_markup=quiz_kb(0),
    )
    await callback.answer()


@router.callback_query(QuizCB.filter(), QuizState.in_progress)
async def quiz_answer(callback: CallbackQuery, callback_data: QuizCB, state: FSMContext, db_user, **kwargs):
    data = await state.get_data()
    answers: dict = data.get("quiz_answers", {})
    step = callback_data.step

    field_names = [
        "niche", "leads_count", "avg_check", "conversion",
        "response_time", "has_crm", "has_repeat_sales", "leak_control",
    ]
    if step < len(field_names):
        answers[field_names[step]] = callback_data.answer

    next_step = step + 1

    if next_step < len(QUIZ_QUESTIONS):
        await state.update_data(quiz_answers=answers, step=next_step)
        q = QUIZ_QUESTIONS[next_step]
        await callback.message.edit_text(
            f"📋 <b>Квиз-воронка</b>\n\n{q['text']}",
            parse_mode="HTML",
            reply_markup=quiz_kb(next_step),
        )
    else:
        # Квиз завершён
        await state.clear()
        await _finish_quiz(callback, db_user, answers)

    await callback.answer()


async def _finish_quiz(callback: CallbackQuery, db_user, answers: dict):
    # Расчёт score
    score = _calculate_score(answers)
    is_vip = score >= 70

    # Создание лида
    lead = await create_lead(
        owner_id=db_user.id,
        telegram_id=callback.from_user.id,
        name=callback.from_user.full_name,
        source="quiz",
        score=score,
        is_vip=is_vip,
        quiz_data=answers,
    )
    await log_event(db_user.id, "quiz_complete", answers)

    # Расчёт потерь
    loss_text = _calculate_losses(answers)

    # Формирование ответа
    readable = "\n".join(
        f"  • {ANSWER_LABELS.get(v, v)}" for k, v in answers.items()
    )

    result_text = (
        f"✅ <b>Квиз завершён!</b>\n\n"
        f"📊 Ваши ответы:\n{readable}\n\n"
        f"🏆 Ваш Score: <b>{score}/100</b>\n"
        f"{'⭐ Вы — VIP-клиент!' if is_vip else ''}\n\n"
        f"{loss_text}\n\n"
        f"⏳ Формирую AI-рекомендации..."
    )

    await callback.message.edit_text(result_text, parse_mode="HTML")

    # AI-рекомендации
    try:
        report = await generate_diagnostics_report(answers)
        await callback.message.answer(
            f"🤖 <b>AI-анализ:</b>\n\n{report}",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
    except Exception:
        await callback.message.answer(
            "🤖 AI-анализ будет доступен позже.",
            reply_markup=back_kb(),
        )


def _calculate_score(answers: dict) -> int:
    score = 50
    conv = answers.get("conversion", "low")
    score += {"low": -20, "mid": 0, "good": 15, "great": 25}.get(conv, 0)

    rt = answers.get("response_time", "normal")
    score += {"fast": 15, "normal": 5, "slow": -10, "very_slow": -20}.get(rt, 0)

    if answers.get("has_crm") == "yes":
        score += 10
    if answers.get("has_repeat_sales") == "yes":
        score += 10
    if answers.get("leak_control") == "yes":
        score += 10
    elif answers.get("leak_control") == "no":
        score -= 10

    return max(0, min(100, score))


def _calculate_losses(answers: dict) -> str:
    leads = LEADS_MAP.get(answers.get("leads_count", "mid"), 65)
    check = CHECK_MAP.get(answers.get("avg_check", "30k"), 15000)
    conv_current = CONV_MAP.get(answers.get("conversion", "mid"), 10) / 100
    conv_target = min(conv_current + 0.15, 0.45)

    monthly_now = leads * check * conv_current
    monthly_potential = leads * check * conv_target
    loss = monthly_potential - monthly_now

    return (
        f"💰 <b>Расчёт потерь:</b>\n"
        f"  Текущий оборот: ~{monthly_now:,.0f} ₽/мес\n"
        f"  Потенциал: ~{monthly_potential:,.0f} ₽/мес\n"
        f"  <b>Вы теряете ~{loss:,.0f} ₽/мес</b>"
    )


# ── Диагностика (отдельная кнопка) ────────────────────

@router.callback_query(MenuCB.filter(F.action == "diagnostics"))
async def diagnostics_start(callback: CallbackQuery, state: FSMContext, db_user, **kwargs):
    # Запускаем тот же квиз
    await start_quiz(callback, state, db_user)


# ── Расчёт потерь (отдельная кнопка) ──────────────────

@router.callback_query(MenuCB.filter(F.action == "losses"))
async def losses_start(callback: CallbackQuery, state: FSMContext, db_user, **kwargs):
    await start_quiz(callback, state, db_user)