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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())
dp.message.middleware(SubscriptionMiddleware())
dp.callback_query.middleware(SubscriptionMiddleware())

dp.include_router(setup_routers())


async def on_startup(bot: Bot) -> None:
    logger.info("=== STARTUP BEGIN ===")

    await init_db()

    me = await bot.get_me()
    set_bot_username(me.username)
    logger.info(f"Bot: @{me.username} (id={me.id})")

    # ── КРИТИЧНО: сброс старого вебхука ──
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Old webhook deleted")

    if config.WEBHOOK_HOST:
        host = config.WEBHOOK_HOST.strip().rstrip("/")
        webhook_url = f"https://{host}{config.WEBHOOK_PATH}"

        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info(f"Webhook SET: {webhook_url}")

        # Проверяем что вебхук реально установлен
        info = await bot.get_webhook_info()
        logger.info(f"Webhook INFO: url={info.url} pending={info.pending_update_count} error={info.last_error_message}")
    else:
        logger.error("WEBHOOK_HOST is EMPTY! Set RAILWAY_PUBLIC_DOMAIN env var!")

    init_scheduler(bot)
    logger.info("=== STARTUP COMPLETE ===")


async def on_shutdown(bot: Bot) -> None:
    logger.info("Shutting down...")
    await bot.delete_webhook()


# ── Логирование ВСЕХ входящих запросов ─────────────────

@web.middleware
async def request_logger(request: web.Request, handler):
    logger.info(f">>> {request.method} {request.path} from {request.remote}")
    try:
        response = await handler(request)
        logger.info(f"<<< {request.method} {request.path} -> {response.status}")
        return response
    except Exception as e:
        logger.error(f"!!! {request.method} {request.path} ERROR: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        logger.info(f"YooKassa: {json.dumps(body, ensure_ascii=False)[:500]}")
        success = await handle_yookassa_webhook(body)

        if success:
            metadata = body.get("object", {}).get("metadata", {})
            telegram_id = metadata.get("telegram_id")
            plan = metadata.get("plan", "pro")
            if telegram_id and body.get("event") == "payment.succeeded":
                try:
                    await bot.send_message(
                        chat_id=int(telegram_id),
                        text=f"✅ <b>Оплата получена!</b>\nТариф <b>{plan}</b> активирован на 30 дней. 🎉",
                    )
                except Exception as e:
                    logger.error(f"Notify user failed: {e}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"YooKassa error: {e}", exc_info=True)
        return web.json_response({"status": "error"}, status=500)


def main() -> None:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # App с middleware логирования
    app = web.Application(middlewares=[request_logger])

    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)

    # Telegram webhook handler
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    logger.info(f"Starting server {config.WEBAPP_HOST}:{config.WEBAPP_PORT}")
    web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)


if __name__ == "__main__":
    main()