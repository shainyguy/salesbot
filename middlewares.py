from __future__ import annotations
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import config
from database import get_or_create_user

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        try:
            if isinstance(event, Message) and event.from_user:
                logger.info(f"MSG from {event.from_user.id}: {event.text}")
                user = await get_or_create_user(
                    telegram_id=event.from_user.id,
                    username=event.from_user.username,
                    full_name=event.from_user.full_name,
                )
            elif isinstance(event, CallbackQuery) and event.from_user:
                logger.info(f"CB from {event.from_user.id}: {event.data}")
                user = await get_or_create_user(
                    telegram_id=event.from_user.id,
                    username=event.from_user.username,
                    full_name=event.from_user.full_name,
                )
        except Exception as e:
            logger.error(f"AUTH MIDDLEWARE ERROR: {e}", exc_info=True)
            user = None

        data["db_user"] = user
        return await handler(event, data)


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("db_user")
        if not user:
            data["has_premium"] = False
            return await handler(event, data)

        if user.role == "admin":
            data["has_premium"] = True
            return await handler(event, data)

        now = datetime.utcnow()
        has_sub = (
            user.subscription_expires is not None
            and user.subscription_expires > now
        )
        has_trial = (
            user.trial_ends is not None
            and user.trial_ends > now
        )
        data["has_premium"] = has_sub or has_trial
        return await handler(event, data)