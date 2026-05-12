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


def test_general_research_prompt_is_not_founder_biased() -> None:
    format_mode = service._detect_format_mode(
        "Brief me on Indian Contracts Act",
        "research",
    )

    prompt = service._build_senior_counsel_prompt(
        query="Brief me on Indian Contracts Act",
        format_mode=format_mode,
        rag_context="",
        today_ist="2026-05-05",
    )

    assert format_mode == "legal_advisor"
    assert "PRACTICAL LEGAL ADVISOR" in prompt
    assert "Do not assume the user is a startup founder" in prompt
    assert "FOUNDER-FRIENDLY ADVISOR" not in prompt
    assert "specific business context (SaaS, data, etc.)" not in prompt


def test_extract_current_query_ignores_previous_turn_text() -> None:
    raw_query = (
        'The user\'s current question is "What are the grounds for divorce under Hindu Marriage Act?". '
        "The previous turn was about FIR quashing under BNSS."
    )

    assert (
        service.extract_current_legal_query(raw_query)
        == "What are the grounds for divorce under Hindu Marriage Act?"
    )


def test_family_law_query_uses_advisor_format_even_with_prior_bnss_context() -> None:
    raw_query = (
        'The user\'s current question is "What are the grounds for divorce under Hindu Marriage Act?". '
        "The previous turn was about FIR quashing under BNSS."
    )

    format_mode = service._detect_format_mode(raw_query, "research")
    prompt = service._build_senior_counsel_prompt(
        query=service.extract_current_legal_query(raw_query),
        format_mode=format_mode,
        rag_context="",
        today_ist="2026-05-07",
    )

    assert format_mode == "legal_advisor"
    assert "PRACTICAL LEGAL ADVISOR" in prompt
    assert "Legacy (IPC/CrPC/Evidence Act)" not in prompt
    query_section = prompt.split("QUERY:\n", 1)[1]
    assert "previous turn" not in query_section.lower()
    assert "bnss" not in query_section.lower()


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


@pytest.mark.asyncio
async def test_hybrid_returns_bnss_fir_quashing_fallback_when_gemini_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known BNSS FIR quashing queries get a narrow source-backed offline fallback."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return None

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="Quash FIR procedure in BNSS",
        rag_result=_make_rag_result("", []),
        background_tasks=None,
    )

    response = result["response"].lower()
    assert "fir quashing under bnss" in response
    assert "section 528" in response
    assert "bhajan lal" in response
    assert result["strategy"] == "offline_bnss_fir_quashing_fallback"
    assert result["citations"]


@pytest.mark.asyncio
async def test_hybrid_returns_hindu_marriage_divorce_fallback_when_gemini_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Family-law divorce queries must not fall through to unrelated BNSS fallback text."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return None

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="What are the grounds for divorce under Hindu Marriage Act?",
        rag_result=_make_rag_result("", []),
        background_tasks=None,
    )

    response = result["response"].lower()
    assert "hindu marriage act" in response
    assert "section 13" in response
    assert "cruelty" in response
    assert "desertion" in response
    assert "fir quashing" not in response
    assert "bnss" not in response
    assert "freshness note" not in response
    assert "offline fallback" not in response
    assert result["strategy"] == "offline_hindu_marriage_divorce_fallback"
    assert result["citations"]


@pytest.mark.asyncio
async def test_hybrid_returns_ni_act_138_limitation_fallback_when_gemini_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Common cheque-bounce limitation queries should not become Advisory Unavailable."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return None

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="What is the limitation timeline for filing a cheque bounce case under Section 138 NI Act?",
        rag_result=_make_rag_result("", []),
        background_tasks=None,
    )

    response = result["response"].lower()
    assert "section 138" in response
    assert "section 142" in response
    assert "3 months" in response
    assert "30 days" in response
    assert "15 days" in response
    assert "1 month" in response
    assert "econ antri" in response
    assert "advisory unavailable" not in response
    assert "p. sarathy" not in response
    assert result["strategy"] == "offline_ni_act_138_limitation_fallback"
    assert result["citations"]


@pytest.mark.asyncio
async def test_hybrid_returns_companies_act_244_fallback_when_gemini_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 244 eligibility queries should match the Classic answer quality."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return None

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="Who can file an oppression and mismanagement petition under Section 244 Companies Act?",
        rag_result=_make_rag_result("", []),
        background_tasks=None,
    )

    response = result["response"].lower()
    assert "section 244" in response
    assert "not less than 100 members" in response
    assert "one-tenth of the issued share capital" in response
    assert "one-fifth of the total number" in response
    assert "waiver" in response
    assert "cyrus investments" in response
    assert "v.s. krishnan" in response
    assert "central government" not in response
    assert "advisory unavailable" not in response
    assert result["strategy"] == "offline_companies_act_244_eligibility_fallback"
    assert result["citations"]


@pytest.mark.asyncio
async def test_hybrid_returns_indian_acts_list_fallback_when_gemini_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Common list queries should get a useful fallback instead of advisory unavailable."""
    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        return None

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="can you give me to 25 legal Act in India",
        rag_result=_make_rag_result("", []),
        background_tasks=None,
    )

    response = result["response"].lower()
    assert "25 important indian acts" in response
    assert "companies act, 2013" in response
    assert "digital personal data protection act, 2023" in response
    assert result["strategy"] == "deterministic_indian_acts_list"


@pytest.mark.asyncio
async def test_hybrid_uses_deterministic_indian_acts_list_before_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit 25 Indian Acts requests should not depend on Gemini counting correctly."""
    called = False

    async def _mock_gemini(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
        nonlocal called
        called = True
        return "Only 20 acts"

    monkeypatch.setattr(service, "_call_gemini_text", _mock_gemini)

    result = await service.build_hybrid_legal_response(
        tenant_id="tenant-1",
        app_key="legalmitra",
        query="Can you list down top 25 Indian Legal Act",
        rag_result=_make_rag_result("", []),
        background_tasks=None,
    )

    assert called is False
    assert "25. Prevention of Money Laundering Act, 2002" in result["response"]
    assert result["strategy"] == "deterministic_indian_acts_list"


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
