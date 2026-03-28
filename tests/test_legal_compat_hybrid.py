import pytest

from app.modules.legal_compat import service


@pytest.mark.asyncio
async def test_hybrid_returns_rag_when_citations_present() -> None:
    rag_result = {
        "answer": "Grounded answer",
        "citations": [{"index": 1, "reference": "[1] source"}],
        "strategy": "hybrid_hash",
    }

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="section 138 timeline",
        rag_result=rag_result,
        background_tasks=None,
    )

    assert result["response"] == "Grounded answer"
    assert len(result["citations"]) == 1
    assert result["note"] is None


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_ai_when_no_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _mock_generate(_: str, query_type: str = "research") -> str | None:
        return "AI fallback answer"

    monkeypatch.setattr(service, "_generate_gemini_fallback_answer", _mock_generate)

    rag_result = {
        "answer": "I do not have enough indexed content matching this question yet.",
        "citations": [],
        "strategy": "hybrid_hash",
    }

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="what is vakalatnama",
        rag_result=rag_result,
        background_tasks=None,
    )

    assert "AI fallback answer" in result["response"]
    assert "important note" in result["response"].lower()
    assert "disclaimer" in result["response"].lower()
    assert result["citations"] == []
    assert "important note" in result["note"].lower()
    assert "disclaimer" in result["note"].lower()


@pytest.mark.asyncio
async def test_hybrid_returns_neutral_note_when_fallback_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _mock_generate(_: str, query_type: str = "research") -> str | None:
        return None

    monkeypatch.setattr(service, "_generate_gemini_fallback_answer", _mock_generate)

    rag_result = {
        "answer": "I do not have enough indexed content matching this question yet. Please ingest relevant documents for this topic.",
        "citations": [],
        "strategy": "hybrid_hash",
    }

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="what is vakalatnama",
        rag_result=rag_result,
        background_tasks=None,
    )

    assert "important note" in result["response"].lower()
    assert "disclaimer" in result["response"].lower()
    assert result["citations"] == []
    assert "important note" in result["note"].lower()
    assert "disclaimer" in result["note"].lower()



@pytest.mark.asyncio
async def test_hybrid_enqueues_auto_sync_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import BackgroundTasks

    async def _mock_generate(_: str, query_type: str = "research") -> str | None:
        return "AI fallback answer"

    monkeypatch.setattr(service, "_generate_gemini_fallback_answer", _mock_generate)

    rag_result = {
        "answer": "I do not have enough indexed content matching this question yet.",
        "citations": [],
        "strategy": "hybrid_hash",
    }

    tasks = BackgroundTasks()
    await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="what is vakalatnama",
        rag_result=rag_result,
        background_tasks=tasks,
    )

    assert len(tasks.tasks) == 1
