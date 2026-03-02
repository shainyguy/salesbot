from __future__ import annotations
import logging
import sys
import json

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import config
from database import init_db
from middlewares import AuthMiddleware, SubscriptionMiddleware
from scheduler_service import init_scheduler
from payment_service import set_bot_username, handle_yookassa_webhook
from handlers import setup_routers

# ── Logging ────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── Bot & Dispatcher ──────────────────────────────────

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Register middlewares
dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())
dp.message.middleware(SubscriptionMiddleware())
dp.callback_query.middleware(SubscriptionMiddleware())

# Register routers
dp.include_router(setup_routers())


# ── Lifecycle ──────────────────────────────────────────

async def on_startup(bot: Bot) -> None:
    logger.info("Starting up...")
    await init_db()

    me = await bot.get_me()
    set_bot_username(me.username)
    logger.info(f"Bot: @{me.username}")

    if config.WEBHOOK_HOST:
        webhook_url = f"https://{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info(f"Webhook set: {webhook_url}")

    init_scheduler(bot)
    logger.info("Startup complete")


async def on_shutdown(bot: Bot) -> None:
    logger.info("Shutting down...")
    await bot.delete_webhook()
    logger.info("Webhook deleted")


# ── Additional routes ──────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        logger.info(f"YooKassa webhook: {json.dumps(body, ensure_ascii=False)[:500]}")
        success = await handle_yookassa_webhook(body)

        if success:
            # Уведомляем пользователя
            metadata = body.get("object", {}).get("metadata", {})
            telegram_id = metadata.get("telegram_id")
            plan = metadata.get("plan", "pro")
            if telegram_id and body.get("event") == "payment.succeeded":
                try:
                    await bot.send_message(
                        chat_id=int(telegram_id),
                        text=(
                            f"✅ <b>Оплата получена!</b>\n\n"
                            f"Тариф <b>{plan}</b> активирован на 30 дней.\n"
                            f"Спасибо за доверие! 🎉"
                        ),
                    )
                except Exception as e:
                    logger.error(f"Failed to notify user: {e}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"YooKassa webhook error: {e}")
        return web.json_response({"status": "error"}, status=500)


# ── Main ───────────────────────────────────────────────

def main() -> None:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    # Health check
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)

    # YooKassa webhook
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)

    # Telegram webhook
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    logger.info(f"Starting on {config.WEBAPP_HOST}:{config.WEBAPP_PORT}")
    web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)


if __name__ == "__main__":
    main()