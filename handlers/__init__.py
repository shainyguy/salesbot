from aiogram import Router

from .start import router as start_router
from .admin import router as admin_router
from .quiz import router as quiz_router
from .payments import router as payments_router
from .booking import router as booking_router
from .crm import router as crm_router


def setup_routers() -> Router:
    root = Router()
    root.include_router(start_router)
    root.include_router(admin_router)
    root.include_router(quiz_router)
    root.include_router(payments_router)
    root.include_router(booking_router)
    root.include_router(crm_router)
    return root