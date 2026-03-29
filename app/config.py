import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME = os.getenv("APP_NAME", "SanMitra Unified Backend")
    APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

    MONGODB_URI = os.getenv("MONGODB_URI", "")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "sanmitra")
    MONGO_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "2000"))
    MONGO_CONNECT_TIMEOUT_MS = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "2000"))
    MONGO_SOCKET_TIMEOUT_MS = int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "5000"))

    POSTGRES_URI = os.getenv("POSTGRES_URI", "")
    PG_CONNECT_TIMEOUT_SECONDS = int(os.getenv("PG_CONNECT_TIMEOUT_SECONDS", "5"))
    PG_POOL_SIZE = int(os.getenv("PG_POOL_SIZE", "8"))
    PG_MAX_OVERFLOW = int(os.getenv("PG_MAX_OVERFLOW", "0"))
    PG_POOL_TIMEOUT_SECONDS = int(os.getenv("PG_POOL_TIMEOUT_SECONDS", "5"))
    PG_POOL_RECYCLE_SECONDS = int(os.getenv("PG_POOL_RECYCLE_SECONDS", "1800"))
    PG_AUTO_CREATE_TABLES = os.getenv("PG_AUTO_CREATE_TABLES", "true").lower() in {"1", "true", "yes", "on"}

    JWT_SECRET = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    GOOGLE_OAUTH_CLIENT_IDS = [
        client_id.strip()
        for client_id in os.getenv("GOOGLE_OAUTH_CLIENT_IDS", "").split(",")
        if client_id.strip()
    ]

    DEFAULT_APP_KEY = os.getenv("DEFAULT_APP_KEY", "mandirmitra").strip().lower()
    ALLOWED_APP_KEYS = [
        key.strip().lower()
        for key in os.getenv(
            "ALLOWED_APP_KEYS",
            "mandirmitra,gruhamitra,mitrabooks,legalmitra,investmitra",
        ).split(",")
        if key.strip()
    ]

    # Phase-2 RAG embedding configuration
    RAG_EMBEDDING_PROVIDER = os.getenv("RAG_EMBEDDING_PROVIDER", "hash").strip().lower()
    RAG_EMBEDDING_HASH_DIM = int(os.getenv("RAG_EMBEDDING_HASH_DIM", "256"))
    RAG_EMBEDDING_ST_MODEL = os.getenv("RAG_EMBEDDING_ST_MODEL", "all-MiniLM-L6-v2").strip()

    # Gemini embeddings (same API key/project can be used as generation, if enabled)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    RAG_GEMINI_EMBED_MODEL = os.getenv("RAG_GEMINI_EMBED_MODEL", "gemini-embedding-001").strip()
    RAG_GEMINI_EMBED_DIM = int(os.getenv("RAG_GEMINI_EMBED_DIM", "768"))
    RAG_GEMINI_TASK_TYPE = os.getenv("RAG_GEMINI_TASK_TYPE", "RETRIEVAL_DOCUMENT").strip().upper()
    RAG_GEMINI_API_BASE = os.getenv("RAG_GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").strip()

    # Hybrid legal response behavior
    LEGAL_HYBRID_AI_FALLBACK_ENABLED = os.getenv("LEGAL_HYBRID_AI_FALLBACK_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    LEGAL_FALLBACK_GEMINI_MODEL = os.getenv("LEGAL_FALLBACK_GEMINI_MODEL", "gemini-2.5-flash").strip()
    LEGAL_FALLBACK_MAX_TOKENS = int(os.getenv("LEGAL_FALLBACK_MAX_TOKENS", "900"))

    # Auto-sync queue hooks for low-confidence legal queries
    RAG_AUTO_SYNC_ENABLED = os.getenv("RAG_AUTO_SYNC_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

    # Legal RAG sync worker (continuous queue processor)
    LEGAL_SYNC_WORKER_ENABLED = os.getenv("LEGAL_SYNC_WORKER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    LEGAL_SYNC_WORKER_POLL_SECONDS = int(os.getenv("LEGAL_SYNC_WORKER_POLL_SECONDS", "60"))
    LEGAL_SYNC_WORKER_BATCH_SIZE = int(os.getenv("LEGAL_SYNC_WORKER_BATCH_SIZE", "5"))
    LEGAL_SYNC_WORKER_MAX_ATTEMPTS = int(os.getenv("LEGAL_SYNC_WORKER_MAX_ATTEMPTS", "4"))
    LEGAL_SYNC_WORKER_LOCK_TIMEOUT_SECONDS = int(os.getenv("LEGAL_SYNC_WORKER_LOCK_TIMEOUT_SECONDS", "600"))
    LEGAL_SYNC_WORKER_MAX_SOURCES_PER_JOB = int(os.getenv("LEGAL_SYNC_WORKER_MAX_SOURCES_PER_JOB", "8"))
    LEGAL_SYNC_WORKER_HTTP_TIMEOUT_SECONDS = int(os.getenv("LEGAL_SYNC_WORKER_HTTP_TIMEOUT_SECONDS", "15"))
    # Official form bank (MVP upload constraints)
    LEGAL_OFFICIAL_FORM_MAX_UPLOAD_MB = int(os.getenv("LEGAL_OFFICIAL_FORM_MAX_UPLOAD_MB", "20"))
    LEGAL_OFFICIAL_FORM_MAX_PAGES = int(os.getenv("LEGAL_OFFICIAL_FORM_MAX_PAGES", "80"))
    LEGAL_OFFICIAL_FORM_MIN_SUGGESTED_LABELS = int(os.getenv("LEGAL_OFFICIAL_FORM_MIN_SUGGESTED_LABELS", "3"))

    # OpenAI-compatible embeddings gateway
    RAG_EMBEDDING_OPENAI_URL = os.getenv("RAG_EMBEDDING_OPENAI_URL", "").strip()
    RAG_EMBEDDING_OPENAI_MODEL = os.getenv("RAG_EMBEDDING_OPENAI_MODEL", "text-embedding-3-small").strip()
    RAG_EMBEDDING_OPENAI_API_KEY = os.getenv("RAG_EMBEDDING_OPENAI_API_KEY", "").strip()

    SUPER_ADMIN_BOOTSTRAP = os.getenv(
        "SUPER_ADMIN_BOOTSTRAP",
        "true" if ENVIRONMENT != "production" else "false",
    ).lower() in {"1", "true", "yes", "on"}
    SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "superadmin@sanmitra.local")
    SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "superadmin123")
    SUPER_ADMIN_FULL_NAME = os.getenv("SUPER_ADMIN_FULL_NAME", "SanMitra Super Admin")
    SUPER_ADMIN_TENANT_ID = os.getenv("SUPER_ADMIN_TENANT_ID", "seed-tenant-1")


@lru_cache
def get_settings() -> Settings:
    return Settings()


