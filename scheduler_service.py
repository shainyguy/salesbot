from __future__ import annotations
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import get_pending_followups, update_followup, get_leaked_leads, get_all_users

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_bot_ref = None


def init_scheduler(bot) -> None:
    global _bot_ref
    _bot_ref = bot

    # Проверка фоллоу-апов каждые 2 минуты
    scheduler.add_job(process_followups, "interval", minutes=2, id="followups")
    # Проверка утечек каждые 30 минут
    scheduler.add_job(check_leaks, "interval", minutes=30, id="leak_check")
    # Ежедневный отчёт в 09:00
    scheduler.add_job(daily_report, "cron", hour=9, minute=0, id="daily_report")

    scheduler.start()
    logger.info("Scheduler started")


async def process_followups() -> None:
    """Отправляет запланированные follow-up сообщения."""
    if not _bot_ref:
        return
    try:
        pending = await get_pending_followups()
        for fu in pending:
            try:
                lead = fu.lead
                if lead and lead.telegram_id:
                    await _bot_ref.send_message(
                        chat_id=lead.telegram_id,
                        text=fu.message_text,
                    )
                await update_followup(
                    fu.id,
                    status="sent",
                    sent_at=datetime.utcnow(),
                )
                logger.info(f"FollowUp {fu.id} sent to lead {fu.lead_id}")
            except Exception as e:
                logger.error(f"FollowUp send error {fu.id}: {e}")
                await update_followup(fu.id, status="failed")
    except Exception as e:
        logger.error(f"process_followups error: {e}")


async def check_leaks() -> None:
    """Уведомляет админов о неотвеченных заявках."""
    if not _bot_ref:
        return
    try:
        from config import config
        users = await get_all_users()
        for user in users:
            if user.role != "admin":
                continue
            leaked = await get_leaked_leads(user.id, hours=2)
            if leaked:
                text = (
                    f"🚨 <b>Утечки заявок!</b>\n\n"
                    f"Найдено <b>{len(leaked)}</b> неотвеченных заявок "
                    f"(более 2 часов без контакта):\n\n"
                )
                for lead in leaked[:10]:
                    text += f"• {lead.name or 'Без имени'} — {lead.created_at.strftime('%d.%m %H:%M')}\n"
                try:
                    await _bot_ref.send_message(
                        chat_id=user.telegram_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"check_leaks error: {e}")


async def daily_report() -> None:
    """Отправляет ежедневный отчёт админам."""
    if not _bot_ref:
        return
    try:
        from config import config
        from database import count_users, get_revenue, count_active_subscriptions

        total = await count_users()
        revenue_30 = await get_revenue(30)
        revenue_7 = await get_revenue(7)
        subs = await count_active_subscriptions()

        text = (
            "📊 <b>Ежедневный отчёт</b>\n\n"
            f"👥 Всего пользователей: <b>{total}</b>\n"
            f"💰 Доход за 7 дней: <b>{revenue_7:,.0f} ₽</b>\n"
            f"💰 Доход за 30 дней: <b>{revenue_30:,.0f} ₽</b>\n\n"
            f"📋 Подписки:\n"
        )
        for plan, count in subs.items():
            text += f"  • {plan}: {count}\n"

        for admin_id in config.ADMIN_IDS:
            try:
                await _bot_ref.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        logger.error(f"daily_report error: {e}")