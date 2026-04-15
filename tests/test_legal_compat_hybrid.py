"""Tests for the unified Gemini-first hybrid response pipeline.

Design contract (post-rewrite):
- build_hybrid_legal_response ALWAYS calls Gemini, regardless of RAG hits.
- Relevant RAG citations are injected as context into the Gemini prompt, not
  returned verbatim as the answer.
- If Gemini is unavailable / returns empty → clean "Advisory Unavailable" message.
- Background sync is enqueued only when there are NO relevant citations.
"""
import pytest

from app.modules.legal_compat import service


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_rag_result(answer: str, citations: list) -> dict:
    return {"answer": answer, "citations": citations, "strategy": "hybrid_hash"}


def _rich_citation(index: int) -> dict:
    """A citation with enough snippet content to pass the relevance filter for
    a 'section 138 timeline' query."""
    return {
        "index": index,
        "title": "Dishonour of cheque under Section 138 NI Act timeline",
        "snippet": "Section 138 of the Negotiable Instruments Act — dishonour cheque timeline notice demand",
        "reference": f"[{index}] source",
    }


# ─── test: Gemini is called and its output is returned ────────────────────────

@pytest.mark.asyncio
async def test_hybrid_calls_gemini_and_returns_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Gemini succeeds, its response is returned regardless of RAG hits."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return "Gemini professional answer"

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    rag_result = _make_rag_result("Grounded answer", [_rich_citation(1)])
    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="section 138 timeline",
        rag_result=rag_result,
        background_tasks=None,
    )

    assert "Gemini professional answer" in result["response"]
    assert "disclaimer" in result["response"].lower()
    assert result["note"] is None
    assert len(result["citations"]) == 1


@pytest.mark.asyncio
async def test_hybrid_passes_relevant_citations_to_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relevant citations are filtered in and appear in the returned citations list."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return "Gemini answer with context"

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    rag_result = _make_rag_result("Some answer", [_rich_citation(1), _rich_citation(2)])
    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="section 138 cheque dishonour timeline notice",
        rag_result=rag_result,
        background_tasks=None,
    )

    assert result["citations"] == [_rich_citation(1), _rich_citation(2)]
    assert result["dropped_citation_count"] == 0


@pytest.mark.asyncio
async def test_hybrid_drops_irrelevant_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Citations unrelated to the query are dropped before Gemini context injection."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return "Gemini answer"

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    irrelevant = {
        "index": 1,
        "title": "GST rates for composite supply under Indian tax law",
        "snippet": "Goods and Services Tax composite supply bundled services rate notification",
        "reference": "[1] gst source",
    }
    rag_result = _make_rag_result("Some answer", [irrelevant])
    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="liability contractor painting contract act owner materials",
        rag_result=rag_result,
        background_tasks=None,
    )

    # Irrelevant GST citation must be dropped
    assert result["citations"] == []
    assert result["dropped_citation_count"] == 1


# ─── test: Gemini unavailable → clean failure ─────────────────────────────────

@pytest.mark.asyncio
async def test_hybrid_returns_clean_message_when_gemini_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Gemini returns None, return the 'Advisory Unavailable' message — no canned templates."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return None

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    rag_result = _make_rag_result(
        "I do not have enough indexed content matching this question yet.",
        [],
    )
    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="what is vakalatnama",
        rag_result=rag_result,
        background_tasks=None,
    )

    assert "advisory unavailable" in result["response"].lower()
    assert result["citations"] == []
    assert result["strategy"] == "gemini_unavailable"


# ─── test: auto-sync enqueue ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hybrid_enqueues_auto_sync_when_no_relevant_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background sync task is added when no relevant RAG citations survive the filter."""
    from fastapi import BackgroundTasks

    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return "Gemini answer"

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    rag_result = _make_rag_result(
        "I do not have enough indexed content.",
        [],
    )
    tasks = BackgroundTasks()
    await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="what is vakalatnama",
        rag_result=rag_result,
        background_tasks=tasks,
    )

    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_hybrid_does_not_enqueue_when_relevant_citations_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No background sync when relevant citations are already in the knowledge base."""
    from fastapi import BackgroundTasks

    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return "Gemini answer"

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    rag_result = _make_rag_result("Some answer", [_rich_citation(1)])
    tasks = BackgroundTasks()
    await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="section 138 cheque dishonour timeline notice",
        rag_result=rag_result,
        background_tasks=tasks,
    )

    assert len(tasks.tasks) == 0
