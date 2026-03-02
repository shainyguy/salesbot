from __future__ import annotations
import logging
import uuid
import base64
from datetime import datetime, timedelta

import httpx

from config import config
from database import create_payment, get_payment_by_yookassa, update_payment, update_user, get_user

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3"


def _auth_header() -> dict:
    creds = f"{config.YOOKASSA_SHOP_ID}:{config.YOOKASSA_SECRET_KEY}"
    b64 = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {b64}"}


async def create_yookassa_payment(
    user_telegram_id: int,
    user_db_id: int,
    plan: str,
) -> dict | None:
    """Создаёт платёж в YooKassa и возвращает {url, payment_id}."""
    plan_info = config.PLANS.get(plan)
    if not plan_info or plan_info["price"] == 0:
        return None

    idempotency_key = str(uuid.uuid4())
    payload = {
        "amount": {
            "value": f"{plan_info['price']:.2f}",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{(await _get_bot_username()) or ''}",
        },
        "capture": True,
        "description": f"Подписка {plan_info['title']}",
        "metadata": {
            "user_id": user_db_id,
            "telegram_id": user_telegram_id,
            "plan": plan,
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{YOOKASSA_API}/payments",
                json=payload,
                headers={
                    **_auth_header(),
                    "Idempotence-Key": idempotency_key,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            await create_payment(
                user_id=user_db_id,
                yookassa_id=data["id"],
                amount=plan_info["price"],
                plan=plan,
                status="pending",
                description=f"Подписка {plan}",
            )

            return {
                "url": data["confirmation"]["confirmation_url"],
                "payment_id": data["id"],
            }
    except Exception as e:
        logger.error(f"YooKassa payment error: {e}")
        return None


async def handle_yookassa_webhook(body: dict) -> bool:
    """Обрабатывает webhook от YooKassa."""
    try:
        event = body.get("event", "")
        obj = body.get("object", {})
        yookassa_id = obj.get("id", "")

        if event != "payment.succeeded":
            return True

        payment = await get_payment_by_yookassa(yookassa_id)
        if not payment:
            logger.warning(f"Payment not found: {yookassa_id}")
            return False

        await update_payment(
            payment.id,
            status="succeeded",
            completed_at=datetime.utcnow(),
        )

        metadata = obj.get("metadata", {})
        telegram_id = int(metadata.get("telegram_id", 0))
        plan = metadata.get("plan", "pro")

        if telegram_id:
            expires = datetime.utcnow() + timedelta(days=30)
            await update_user(
                telegram_id,
                plan=plan,
                subscription_expires=expires,
            )
            logger.info(f"Subscription activated: user {telegram_id}, plan {plan}")

        return True
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return False


async def check_payment_status(yookassa_id: str) -> str:
    """Проверяет статус платежа."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{YOOKASSA_API}/payments/{yookassa_id}",
                headers=_auth_header(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("status", "unknown")
    except Exception as e:
        logger.error(f"Status check error: {e}")
        return "error"


_bot_username_cache: str | None = None


async def _get_bot_username() -> str | None:
    global _bot_username_cache
    return _bot_username_cache


def set_bot_username(username: str) -> None:
    global _bot_username_cache
    _bot_username_cache = username