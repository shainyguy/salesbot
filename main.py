from __future__ import annotations
import logging
import sys
import json

import httpx
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


# ── Обработчики ────────────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot": "salesbot"})


async def telegram_webhook_handler(request: web.Request) -> web.Response:
    try:
        body = await request.read()
        logger.info(f"=== TELEGRAM POST RECEIVED === size={len(body)}")
        data = json.loads(body)
        logger.info(f"UPDATE RAW: {json.dumps(data, ensure_ascii=False)[:2000]}")

        update = Update.model_validate(data, context={"bot": bot})
        logger.info(f"UPDATE OK: type={update.event_type} id={update.update_id}")

        await dp.feed_update(bot=bot, update=update)
        logger.info(f"UPDATE {update.update_id} PROCESSED")

    except Exception as e:
        logger.error(f"WEBHOOK PROCESSING ERROR: {e}", exc_info=True)

    return web.json_response({"ok": True})


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        logger.info(f"YOOKASSA: {json.dumps(body, ensure_ascii=False)[:500]}")
        success = await handle_yookassa_webhook(body)
        if success:
            metadata = body.get("object", {}).get("metadata", {})
            tid = metadata.get("telegram_id")
            plan = metadata.get("plan", "pro")
            if tid and body.get("event") == "payment.succeeded":
                try:
                    await bot.send_message(int(tid), f"✅ Тариф <b>{plan}</b> активирован! 🎉")
                except Exception:
                    pass
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"YooKassa error: {e}", exc_info=True)
        return web.json_response({"status": "error"}, status=500)


async def debug_handler(request: web.Request) -> web.Response:
    try:
        info = await bot.get_webhook_info()
        return web.json_response({
            "webhook_url": info.url,
            "pending_updates": info.pending_update_count,
            "last_error": info.last_error_message,
            "last_error_date": str(info.last_error_date) if info.last_error_date else None,
            "max_connections": info.max_connections,
            "ip_address": info.ip_address,
            "allowed_updates": info.allowed_updates,
            "webhook_host_env": config.WEBHOOK_HOST,
            "admin_ids": config.ADMIN_IDS,
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def test_handler(request: web.Request) -> web.Response:
    """Отправляет тестовое сообщение админу — проверить что бот вообще работает."""
    results = []
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "🟢 <b>Тест пройден!</b>\n\n"
                "Бот работает. Если ты видишь это сообщение — "
                "проблема только в webhook.\n\n"
                "Отправь мне /start прямо сейчас.",
            )
            results.append({"admin_id": admin_id, "status": "sent"})
        except Exception as e:
            results.append({"admin_id": admin_id, "status": "error", "error": str(e)})

    return web.json_response({"results": results})


async def selftest_handler(request: web.Request) -> web.Response:
    """
    Бот сам проверяет свой webhook:
    делает POST на свой URL и смотрит ответ.
    """
    host = config.WEBHOOK_HOST.strip().rstrip("/")
    url = f"https://{host}/webhook"

    fake_update = {
        "update_id": 999999999,
        "message": {
            "message_id": 1,
            "from": {"id": 1, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 1, "type": "private"},
            "date": 1234567890,
            "text": "/selftest"
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=fake_update, timeout=10)
            return web.json_response({
                "test_url": url,
                "status_code": resp.status_code,
                "response": resp.text[:500],
                "reachable": resp.status_code == 200,
            })
    except Exception as e:
        return web.json_response({
            "test_url": url,
            "reachable": False,
            "error": str(e),
        })


# ── Catch-all для ЛЮБОГО POST ──────────────────────────

async def catch_all_post(request: web.Request) -> web.Response:
    body = await request.read()
    logger.warning(
        f"CATCH-ALL POST: path={request.path} "
        f"from={request.remote} "
        f"body={body[:500]}"
    )
    return web.json_response({"caught": request.path})


# ── Startup ────────────────────────────────────────────

async def on_app_startup(app: web.Application) -> None:
    logger.info("=== STARTUP ===")

    await init_db()

    me = await bot.get_me()
    set_bot_username(me.username)
    logger.info(f"Bot: @{me.username} (id={me.id})")

    # ПОЛНЫЙ СБРОС
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook DELETED + pending dropped")

    import asyncio
    await asyncio.sleep(1)

    host = config.WEBHOOK_HOST.strip().rstrip("/")
    if not host:
        logger.error("WEBHOOK_HOST EMPTY!")
        return

    webhook_url = f"https://{host}/webhook"
    logger.info(f"Setting webhook: {webhook_url}")

    ok = await bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
    )
    logger.info(f"set_webhook: {ok}")

    info = await bot.get_webhook_info()
    logger.info(
        f"WEBHOOK: url={info.url} "
        f"pending={info.pending_update_count} "
        f"error={info.last_error_message} "
        f"ip={info.ip_address} "
        f"max_conn={info.max_connections}"
    )

    # Отправить тестовое сообщение админу
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🟢 <b>Бот запущен!</b>\n\n"
                f"Webhook: <code>{webhook_url}</code>\n"
                f"Отправь /start чтобы проверить.",
            )
            logger.info(f"Startup msg sent to admin {admin_id}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    init_scheduler(bot)
    logger.info("=== STARTUP COMPLETE ===")


async def on_app_shutdown(app: web.Application) -> None:
    logger.info("=== SHUTDOWN ===")
    try:
        await bot.delete_webhook()
        await bot.session.close()
    except Exception:
        pass


# ── App ────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()

    app.on_startup.append(on_app_startup)
    app.on_shutdown.append(on_app_shutdown)

    # Основные маршруты
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/debug", debug_handler)
    app.router.add_get("/test", test_handler)
    app.router.add_get("/selftest", selftest_handler)
    app.router.add_post("/webhook", telegram_webhook_handler)
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)

    for resource in app.router.resources():
        for route in resource:
            logger.info(f"ROUTE: {route.method} {route.resource.canonical}")

    return app


def main() -> None:
    logger.info(f"HOST={config.WEBAPP_HOST} PORT={config.WEBAPP_PORT}")
    logger.info(f"WEBHOOK_HOST={config.WEBHOOK_HOST}")
    app = create_app()
    web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT, print=None)


if __name__ == "__main__":
    main()
