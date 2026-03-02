from __future__ import annotations
import logging
import sys
import json
import asyncio

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


# ── Telegram webhook handler ───────────────────────────

async def telegram_webhook_handler(request: web.Request) -> web.Response:
    try:
        body = await request.read()
        logger.info(f">>> TELEGRAM POST size={len(body)}")
        data = json.loads(body)
        logger.info(f"UPDATE: {json.dumps(data, ensure_ascii=False)[:2000]}")

        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot=bot, update=update)
        logger.info(f"UPDATE {update.update_id} OK")
    except Exception as e:
        logger.error(f"WEBHOOK ERROR: {e}", exc_info=True)

    return web.json_response({"ok": True})


# ── Другие эндпоинты ──────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def debug_handler(request: web.Request) -> web.Response:
    info = await bot.get_webhook_info()
    return web.json_response({
        "webhook_url": info.url,
        "pending": info.pending_update_count,
        "last_error": info.last_error_message,
        "ip": info.ip_address,
        "host_env": config.WEBHOOK_HOST,
        "admins": config.ADMIN_IDS,
    })


async def test_handler(request: web.Request) -> web.Response:
    results = []
    for aid in config.ADMIN_IDS:
        try:
            await bot.send_message(aid, "🟢 <b>Тест — бот работает!</b>\nОтправь /start")
            results.append({"id": aid, "ok": True})
        except Exception as e:
            results.append({"id": aid, "ok": False, "err": str(e)})
    return web.json_response(results)


async def fix_webhook_handler(request: web.Request) -> web.Response:
    """Ручная переустановка webhook — открой в браузере если webhook слетел."""
    host = config.WEBHOOK_HOST.strip().rstrip("/")
    url = f"https://{host}/webhook"

    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(0.5)
    ok = await bot.set_webhook(url=url, allowed_updates=["message", "callback_query"])
    info = await bot.get_webhook_info()

    for aid in config.ADMIN_IDS:
        try:
            await bot.send_message(aid, f"🔧 Webhook переустановлен!\nURL: {url}\nОтправь /start")
        except Exception:
            pass

    return web.json_response({
        "fixed": ok,
        "url": info.url,
        "pending": info.pending_update_count,
    })


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        logger.info(f"YOOKASSA: {json.dumps(body, ensure_ascii=False)[:500]}")
        success = await handle_yookassa_webhook(body)
        if success:
            meta = body.get("object", {}).get("metadata", {})
            tid = meta.get("telegram_id")
            plan = meta.get("plan", "pro")
            if tid and body.get("event") == "payment.succeeded":
                try:
                    await bot.send_message(int(tid), f"✅ Тариф <b>{plan}</b> активирован! 🎉")
                except Exception:
                    pass
        return web.json_response({"ok": True})
    except Exception as e:
        logger.error(f"YooKassa error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Startup ────────────────────────────────────────────

async def on_app_startup(app: web.Application) -> None:
    logger.info("=== STARTUP ===")

    await init_db()

    me = await bot.get_me()
    set_bot_username(me.username)
    logger.info(f"Bot: @{me.username}")

    # Удалить старый + подождать
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(1)

    # Установить новый
    host = config.WEBHOOK_HOST.strip().rstrip("/")
    url = f"https://{host}/webhook"
    ok = await bot.set_webhook(url=url, allowed_updates=["message", "callback_query"])
    logger.info(f"Webhook set={ok} url={url}")

    # Проверить
    info = await bot.get_webhook_info()
    logger.info(f"Webhook CHECK: url={info.url} pending={info.pending_update_count} err={info.last_error_message}")

    if not info.url:
        logger.error("!!! WEBHOOK URL IS EMPTY AFTER SET !!!")

    # Уведомить админа
    for aid in config.ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"🟢 <b>Бот запущен!</b>\n"
                f"Webhook: <code>{url}</code>\n"
                f"Webhook active: <b>{bool(info.url)}</b>\n"
                f"Отправь /start",
            )
        except Exception as e:
            logger.error(f"Admin notify fail: {e}")

    init_scheduler(bot)
    logger.info("=== STARTUP COMPLETE ===")


async def on_app_shutdown(app: web.Application) -> None:
    """НЕ удаляем webhook при остановке — Railway перезапускает контейнеры."""
    logger.info("=== SHUTDOWN (webhook kept) ===")
    try:
        await bot.session.close()
    except Exception:
        pass


# ── App ────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_app_startup)
    app.on_shutdown.append(on_app_shutdown)

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/debug", debug_handler)
    app.router.add_get("/test", test_handler)
    app.router.add_get("/fix", fix_webhook_handler)
    app.router.add_post("/webhook", telegram_webhook_handler)
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)

    return app


def main() -> None:
    logger.info(f"Starting: host={config.WEBAPP_HOST} port={config.WEBAPP_PORT} webhook={config.WEBHOOK_HOST}")
    app = create_app()
    web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT, print=None)


if __name__ == "__main__":
    main()
