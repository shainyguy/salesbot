from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: list[int] = []

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "")

    GIGACHAT_API_KEY: str = os.getenv("GIGACHAT_API_KEY", "")

    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_HOST: str = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    WEBAPP_HOST: str = "0.0.0.0"
    WEBAPP_PORT: int = int(os.getenv("PORT", "8080"))

    TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "3"))

    PLANS: dict = {
        "free": {
            "title": "Free",
            "price": 0,
            "leads_limit": 10,
            "followups_limit": 1,
            "analytics": False,
            "ai": False,
            "booking": False,
        },
        "pro": {
            "title": "Pro — 990 ₽/мес",
            "price": 990,
            "leads_limit": 100,
            "followups_limit": 5,
            "analytics": True,
            "ai": True,
            "booking": True,
        },
        "business": {
            "title": "Business — 2 990 ₽/мес",
            "price": 2990,
            "leads_limit": 500,
            "followups_limit": 20,
            "analytics": True,
            "ai": True,
            "booking": True,
        },
        "enterprise": {
            "title": "Enterprise — 9 990 ₽/мес",
            "price": 9990,
            "leads_limit": 999999,
            "followups_limit": 999999,
            "analytics": True,
            "ai": True,
            "booking": True,
        },
    }

    def __init__(self) -> None:
        raw = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = [int(x.strip()) for x in raw.split(",") if x.strip()]

        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.DATABASE_URL = url


config = Config()