from types import SimpleNamespace

from app.modules.rag.providers import get_embedding_provider


def _clear_provider_cache() -> None:
    get_embedding_provider.cache_clear()


def test_provider_defaults_to_hash(monkeypatch) -> None:
    fake_settings = SimpleNamespace(
        RAG_EMBEDDING_PROVIDER="hash",
        RAG_EMBEDDING_HASH_DIM=256,
        RAG_EMBEDDING_ST_MODEL="all-MiniLM-L6-v2",
        GEMINI_API_KEY="",
        RAG_GEMINI_EMBED_MODEL="gemini-embedding-001",
        RAG_GEMINI_API_BASE="https://generativelanguage.googleapis.com/v1beta",
        RAG_GEMINI_EMBED_DIM=768,
        RAG_GEMINI_TASK_TYPE="RETRIEVAL_DOCUMENT",
        RAG_EMBEDDING_OPENAI_URL="",
        RAG_EMBEDDING_OPENAI_API_KEY="",
        RAG_EMBEDDING_OPENAI_MODEL="text-embedding-3-small",
    )
    monkeypatch.setattr("app.modules.rag.providers.get_settings", lambda: fake_settings)
    _clear_provider_cache()

    provider = get_embedding_provider()
    assert provider.name == "hash"


def test_provider_can_select_gemini(monkeypatch) -> None:
    fake_settings = SimpleNamespace(
        RAG_EMBEDDING_PROVIDER="gemini",
        RAG_EMBEDDING_HASH_DIM=256,
        RAG_EMBEDDING_ST_MODEL="all-MiniLM-L6-v2",
        GEMINI_API_KEY="test-key",
        RAG_GEMINI_EMBED_MODEL="gemini-embedding-001",
        RAG_GEMINI_API_BASE="https://generativelanguage.googleapis.com/v1beta",
        RAG_GEMINI_EMBED_DIM=768,
        RAG_GEMINI_TASK_TYPE="RETRIEVAL_DOCUMENT",
        RAG_EMBEDDING_OPENAI_URL="",
        RAG_EMBEDDING_OPENAI_API_KEY="",
        RAG_EMBEDDING_OPENAI_MODEL="text-embedding-3-small",
    )
    monkeypatch.setattr("app.modules.rag.providers.get_settings", lambda: fake_settings)
    _clear_provider_cache()

    provider = get_embedding_provider()
    assert provider.name == "gemini"
    assert provider.model_name == "gemini-embedding-001"
