import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.accounting.models.base import Base
from app.api.legacy_alias_router import router as legacy_alias_router
from app.api.v1.router import api_router
from app.config import get_settings
from app.core.audit.service import ensure_audit_indexes
from app.core.onboarding.service import ensure_onboarding_indexes
from app.core.tenants.context import TenantContextMiddleware
from app.core.tenants.service import ensure_seed_tenant
from app.core.users.service import ensure_seed_user, ensure_super_admin_user
from app.db.mongo import close_mongo, init_mongo, ping_mongo
from app.db.postgres import close_postgres, create_postgres_tables, init_postgres, ping_postgres
from app.modules.housing.service import ensure_maintenance_indexes
from app.modules.investment.service import ensure_investment_indexes
from app.modules.legal.service import ensure_legal_indexes
from app.modules.legal_compat.service import ensure_legal_compat_indexes
from app.modules.legal_compat.sync_worker import start_legal_sync_worker, stop_legal_sync_worker
from app.modules.mandir_compat.service import ensure_demo_mandir_bootstrap
from app.modules.rag.service import ensure_rag_indexes
from app.modules.temple.service import ensure_donations_indexes

settings = get_settings()

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=r"https://[a-z0-9-]+\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TenantContextMiddleware)

app.include_router(legacy_alias_router)
app.include_router(api_router)


@app.on_event("startup")
async def on_startup() -> None:
    try:
        await init_mongo()
        await ensure_seed_tenant()
        await ensure_seed_user()
        await ensure_super_admin_user()
        await ensure_demo_mandir_bootstrap()
        await ensure_audit_indexes()
        await ensure_donations_indexes()
        await ensure_maintenance_indexes()
        await ensure_legal_indexes()
        await ensure_legal_compat_indexes()
        await ensure_investment_indexes()
        await ensure_onboarding_indexes()
        await ensure_rag_indexes()
    except Exception:
        # Keep app booting even if Mongo is unavailable; health endpoint will show degraded state.
        pass

    try:
        await start_legal_sync_worker()
    except Exception:
        # Worker is best-effort; API should still boot even if background sync is unavailable.
        pass

    try:
        await init_postgres()
        # Ensure model metadata is loaded before create_all.
        import app.accounting.models.entities  # noqa: F401

        if settings.PG_AUTO_CREATE_TABLES:
            await create_postgres_tables(Base.metadata)
    except Exception:
        # Keep app booting even if PostgreSQL is unavailable; health endpoint will show degraded state.
        pass


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await stop_legal_sync_worker()
    await close_mongo()
    await close_postgres()


async def _ping_with_timeout(coro, timeout_seconds: float) -> tuple[bool, str]:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


@app.get("/health")
async def health():
    mongo_task = _ping_with_timeout(ping_mongo(), timeout_seconds=2.5)
    pg_task = _ping_with_timeout(ping_postgres(), timeout_seconds=2.5)
    mongo_result, pg_result = await asyncio.gather(mongo_task, pg_task)

    mongo_ok, mongo_detail = mongo_result
    pg_ok, pg_detail = pg_result
    overall = "ok" if mongo_ok or pg_ok else "degraded"
    return {
        "status": overall,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "db": {
            "mongo": {"ok": mongo_ok, "detail": mongo_detail},
            "postgres": {"ok": pg_ok, "detail": pg_detail},
        },
    }



