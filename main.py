from __future__ import annotations
import logging
import sys
import json

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database import init_db
from middlewares import AuthMiddleware, SubscriptionMiddleware
from scheduler_service import init_scheduler
from payment_service import set_bot_username, handle_yookassa_webhook
from handlers import setup_routers

logging.basicConfig(
    level=logging.DEBUG,
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


# ── Обработчики маршрутов ──────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    """Health check для Railway."""
    return web.json_response({"status": "ok", "bot": "salesbot"})


async def telegram_webhook_handler(request: web.Request) -> web.Response:
    """
    Ручная обработка Telegram webhook.
    Не используем SimpleRequestHandler — он глючит с middleware.
    """
    try:
        body = await request.read()
        data = json.loads(body)
        logger.info(f"TELEGRAM UPDATE RAW: {json.dumps(data, ensure_ascii=False)[:1000]}")

        update = Update.model_validate(data, context={"bot": bot})
        logger.info(f"UPDATE parsed: type={update.event_type}, id={update.update_id}")

        await dp.feed_update(bot=bot, update=update)
        logger.info(f"UPDATE {update.update_id} processed OK")

    except Exception as e:
        logger.error(f"WEBHOOK ERROR: {e}", exc_info=True)

    # ВСЕГДА 200 — иначе Telegram уйдёт в backoff
    return web.json_response({"ok": True})


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Обработка оплаты от YooKassa."""
    try:
        body = await request.json()
        logger.info(f"YOOKASSA: {json.dumps(body, ensure_ascii=False)[:500]}")
        success = await handle_yookassa_webhook(body)

        if success:
            metadata = body.get("object", {}).get("metadata", {})
            telegram_id = metadata.get("telegram_id")
            plan = metadata.get("plan", "pro")
            if telegram_id and body.get("event") == "payment.succeeded":
                try:
                    await bot.send_message(
                        chat_id=int(telegram_id),
                        text=f"✅ <b>Оплата получена!</b>\n"
                             f"Тариф <b>{plan}</b> активирован на 30 дней. 🎉",
                    )
                except Exception as e:
                    logger.error(f"Notify user error: {e}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"YooKassa error: {e}", exc_info=True)
        return web.json_response({"status": "error"}, status=500)


async def debug_handler(request: web.Request) -> web.Response:
    """Отладочный эндпоинт — проверить что webhook работает."""
    try:
        info = await bot.get_webhook_info()
        return web.json_response({
            "webhook_url": info.url,
            "pending_updates": info.pending_update_count,
            "last_error": info.last_error_message,
            "last_error_date": str(info.last_error_date) if info.last_error_date else None,
            "bot_token_set": bool(config.BOT_TOKEN),
            "webhook_host": config.WEBHOOK_HOST,
            "admin_ids": config.ADMIN_IDS,
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Startup / Shutdown ─────────────────────────────────

async def on_app_startup(app: web.Application) -> None:
    """Вызывается при старте aiohttp приложения."""
    logger.info("=== APP STARTUP ===")

    # 1. База данных
    await init_db()

    # 2. Информация о боте
    me = await bot.get_me()
    set_bot_username(me.username)
    logger.info(f"Bot: @{me.username} (id={me.id})")

    # 3. Сброс старого webhook
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Old webhook DELETED")

    # 4. Установка нового webhook
    host = config.WEBHOOK_HOST.strip().rstrip("/")
    if not host:
        logger.error("!!! WEBHOOK_HOST IS EMPTY !!!")
        logger.error("Set RAILWAY_PUBLIC_DOMAIN in Railway variables")
        return

    webhook_url = f"https://{host}/webhook"
    logger.info(f"Setting webhook to: {webhook_url}")

    result = await bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
    )
    logger.info(f"set_webhook result: {result}")

    # 5. Проверяем
    info = await bot.get_webhook_info()
    logger.info(
        f"WEBHOOK INFO: url={info.url} "
        f"pending={info.pending_update_count} "
        f"error={info.last_error_message} "
        f"max_connections={info.max_connections}"
    )

    # 6. Планировщик
    init_scheduler(bot)
    logger.info("=== APP STARTUP COMPLETE ===")


async def on_app_shutdown(app: web.Application) -> None:
    """Вызывается при остановке."""
    logger.info("=== APP SHUTDOWN ===")
    try:
        await bot.delete_webhook()
        await bot.session.close()
    except Exception as e:
        logger.error(f"Shutdown error: {e}")


# ── Создание приложения ───────────────────────────────

def create_app() -> web.Application:
    app = web.Application()

    # Сигналы жизненного цикла
    app.on_startup.append(on_app_startup)
    app.on_shutdown.append(on_app_shutdown)

    # Маршруты
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/debug", debug_handler)
    app.router.add_post("/webhook", telegram_webhook_handler)
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)

    # Логируем все зарегистрированные маршруты
    for resource in app.router.resources():
        for route in resource:
            logger.info(f"ROUTE: {route.method} {route.resource.canonical}")

    return app


# ── Точка входа ────────────────────────────────────────

def main() -> None:
    logger.info(f"=== SALESBOT STARTING ===")
    logger.info(f"HOST: {config.WEBAPP_HOST}")
    logger.info(f"PORT: {config.WEBAPP_PORT}")
    logger.info(f"WEBHOOK_HOST: {config.WEBHOOK_HOST}")
    logger.info(f"ADMIN_IDS: {config.ADMIN_IDS}")
    logger.info(f"BOT_TOKEN set: {bool(config.BOT_TOKEN)}")
    logger.info(f"DB URL set: {bool(config.DATABASE_URL)}")

    app = create_app()
    web.run_app(
        app,
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        print=None,  # Убираем двойной вывод
    )


if __name__ == "__main__":
    main()
