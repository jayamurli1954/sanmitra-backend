from fastapi import APIRouter

from app.accounting.router import router as accounting_router
from app.core.auth.router import router as auth_router
from app.core.email_delivery.router import router as email_delivery_router
from app.core.onboarding.router import router as onboarding_router
from app.core.tenants.router import router as tenants_router
from app.core.users.router import router as users_router
from app.modules.housing.router import router as housing_router
from app.modules.investment.router import router as investment_router
from app.modules.legal.router import router as legal_router
from app.modules.legal_compat.router import router as legal_compat_router
from app.modules.mandir_compat.router import router as mandir_compat_router
from app.modules.mitrabooks_compat.router import router as mitrabooks_compat_router
from app.modules.rag.router import router as rag_router
from app.modules.temple.router import router as temple_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(email_delivery_router)
api_router.include_router(onboarding_router)
api_router.include_router(tenants_router)
api_router.include_router(users_router)
api_router.include_router(accounting_router)
api_router.include_router(temple_router)
api_router.include_router(housing_router)
api_router.include_router(legal_router)
api_router.include_router(legal_compat_router)
api_router.include_router(mandir_compat_router)
api_router.include_router(mitrabooks_compat_router)
api_router.include_router(investment_router)
api_router.include_router(rag_router)

