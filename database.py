from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import select, func, update, and_, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from config import config
from models import Base, User, Lead, Payment, Booking, FollowUp, AnalyticsEvent

logger = logging.getLogger(__name__)

engine = create_async_engine(
    config.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы sb_* если их нет. Чужие таблицы не трогает."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("DB init OK — all sb_* tables ready")
    except Exception as e:
        logger.error(f"DB init FAILED: {e}", exc_info=True)
        raise


async def get_session() -> AsyncSession:
    return SessionFactory()


# ── Users ──────────────────────────────────────────────

async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    async with SessionFactory() as s:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await s.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            return user

        import uuid
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            role="admin" if telegram_id in config.ADMIN_IDS else "user",
            plan="free",
            trial_ends=datetime.utcnow() + timedelta(days=config.TRIAL_DAYS),
            referral_code=uuid.uuid4().hex[:8],
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        logger.info(f"New user: {telegram_id} ({full_name})")
        return user


async def get_user(telegram_id: int) -> User | None:
    async with SessionFactory() as s:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await s.execute(stmt)
        return result.scalar_one_or_none()


async def update_user(telegram_id: int, **kwargs) -> None:
    async with SessionFactory() as s:
        stmt = update(User).where(User.telegram_id == telegram_id).values(**kwargs)
        await s.execute(stmt)
        await s.commit()


async def get_all_users(limit: int = 1000, offset: int = 0) -> Sequence[User]:
    async with SessionFactory() as s:
        stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        result = await s.execute(stmt)
        return result.scalars().all()


async def count_users() -> int:
    async with SessionFactory() as s:
        stmt = select(func.count(User.id))
        result = await s.execute(stmt)
        return result.scalar_one()


async def count_active_subscriptions() -> dict[str, int]:
    async with SessionFactory() as s:
        now = datetime.utcnow()
        plans = {}
        for plan_name in config.PLANS:
            if plan_name == "free":
                stmt = select(func.count(User.id)).where(User.plan == plan_name)
            else:
                stmt = select(func.count(User.id)).where(
                    and_(User.plan == plan_name, User.subscription_expires > now)
                )
            result = await s.execute(stmt)
            plans[plan_name] = result.scalar_one()
        return plans


# ── Leads ──────────────────────────────────────────────

async def create_lead(owner_id: int, **kwargs) -> Lead:
    async with SessionFactory() as s:
        lead = Lead(owner_id=owner_id, **kwargs)
        s.add(lead)
        await s.commit()
        await s.refresh(lead)
        return lead


async def get_leads(owner_id: int, status: str | None = None, limit: int = 100) -> Sequence[Lead]:
    async with SessionFactory() as s:
        stmt = select(Lead).where(Lead.owner_id == owner_id)
        if status:
            stmt = stmt.where(Lead.status == status)
        stmt = stmt.order_by(Lead.created_at.desc()).limit(limit)
        result = await s.execute(stmt)
        return result.scalars().all()


async def get_lead(lead_id: int) -> Lead | None:
    async with SessionFactory() as s:
        result = await s.execute(select(Lead).where(Lead.id == lead_id))
        return result.scalar_one_or_none()


async def update_lead(lead_id: int, **kwargs) -> None:
    async with SessionFactory() as s:
        stmt = update(Lead).where(Lead.id == lead_id).values(**kwargs)
        await s.execute(stmt)
        await s.commit()


async def count_leads(owner_id: int) -> int:
    async with SessionFactory() as s:
        stmt = select(func.count(Lead.id)).where(Lead.owner_id == owner_id)
        result = await s.execute(stmt)
        return result.scalar_one()


async def get_vip_leads(owner_id: int) -> Sequence[Lead]:
    async with SessionFactory() as s:
        stmt = select(Lead).where(
            and_(Lead.owner_id == owner_id, Lead.is_vip == True)
        ).order_by(Lead.score.desc())
        result = await s.execute(stmt)
        return result.scalars().all()


async def get_leaked_leads(owner_id: int, hours: int = 24) -> Sequence[Lead]:
    async with SessionFactory() as s:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stmt = select(Lead).where(
            and_(
                Lead.owner_id == owner_id,
                Lead.status == "new",
                Lead.first_contact_at.is_(None),
                Lead.created_at < cutoff,
            )
        )
        result = await s.execute(stmt)
        return result.scalars().all()


async def get_lead_funnel(owner_id: int) -> dict[str, int]:
    async with SessionFactory() as s:
        funnel = {}
        for st in ("new", "contacted", "qualified", "converted", "lost"):
            stmt = select(func.count(Lead.id)).where(
                and_(Lead.owner_id == owner_id, Lead.status == st)
            )
            result = await s.execute(stmt)
            funnel[st] = result.scalar_one()
        return funnel


# ── Payments ───────────────────────────────────────────

async def create_payment(user_id: int, **kwargs) -> Payment:
    async with SessionFactory() as s:
        p = Payment(user_id=user_id, **kwargs)
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p


async def get_payment_by_yookassa(yookassa_id: str) -> Payment | None:
    async with SessionFactory() as s:
        stmt = select(Payment).where(Payment.yookassa_id == yookassa_id)
        result = await s.execute(stmt)
        return result.scalar_one_or_none()


async def update_payment(payment_id: int, **kwargs) -> None:
    async with SessionFactory() as s:
        stmt = update(Payment).where(Payment.id == payment_id).values(**kwargs)
        await s.execute(stmt)
        await s.commit()


async def get_revenue(days: int = 30) -> float:
    async with SessionFactory() as s:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            and_(Payment.status == "succeeded", Payment.created_at >= cutoff)
        )
        result = await s.execute(stmt)
        return float(result.scalar_one())


# ── Bookings ───────────────────────────────────────────

async def create_booking(lead_id: int, owner_id: int, **kwargs) -> Booking:
    async with SessionFactory() as s:
        b = Booking(lead_id=lead_id, owner_id=owner_id, **kwargs)
        s.add(b)
        await s.commit()
        await s.refresh(b)
        return b


async def get_bookings(owner_id: int, status: str = "confirmed") -> Sequence[Booking]:
    async with SessionFactory() as s:
        stmt = (
            select(Booking)
            .where(and_(Booking.owner_id == owner_id, Booking.status == status))
            .order_by(Booking.book_date, Booking.book_time)
        )
        result = await s.execute(stmt)
        return result.scalars().all()


async def update_booking(booking_id: int, **kwargs) -> None:
    async with SessionFactory() as s:
        stmt = update(Booking).where(Booking.id == booking_id).values(**kwargs)
        await s.execute(stmt)
        await s.commit()


# ── FollowUps ──────────────────────────────────────────

async def create_followup(lead_id: int, owner_id: int, **kwargs) -> FollowUp:
    async with SessionFactory() as s:
        f = FollowUp(lead_id=lead_id, owner_id=owner_id, **kwargs)
        s.add(f)
        await s.commit()
        await s.refresh(f)
        return f


async def get_pending_followups() -> Sequence[FollowUp]:
    async with SessionFactory() as s:
        now = datetime.utcnow()
        stmt = select(FollowUp).where(
            and_(FollowUp.status == "pending", FollowUp.scheduled_at <= now)
        )
        result = await s.execute(stmt)
        return result.scalars().all()


async def update_followup(followup_id: int, **kwargs) -> None:
    async with SessionFactory() as s:
        stmt = update(FollowUp).where(FollowUp.id == followup_id).values(**kwargs)
        await s.execute(stmt)
        await s.commit()


# ── Analytics ──────────────────────────────────────────

async def log_event(user_id: int, event_type: str, event_data: dict | None = None) -> None:
    try:
        async with SessionFactory() as s:
            e = AnalyticsEvent(user_id=user_id, event_type=event_type, event_data=event_data)
            s.add(e)
            await s.commit()
    except Exception as e:
        logger.error(f"log_event failed: {e}")


async def get_events_count(event_type: str, days: int = 30) -> int:
    async with SessionFactory() as s:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = select(func.count(AnalyticsEvent.id)).where(
            and_(AnalyticsEvent.event_type == event_type, AnalyticsEvent.created_at >= cutoff)
        )
        result = await s.execute(stmt)
        return result.scalar_one()