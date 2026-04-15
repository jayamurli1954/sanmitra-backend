from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks

from app.config import get_settings
from app.db.mongo import get_collection

_logger = logging.getLogger(__name__)

RAG_SYNC_QUEUE_COLLECTION = "rag_sync_queue"

_CLOSING_DISCLAIMER = (
    "\n\n---\n"
    "*Disclaimer: This note is prepared for the use of the instructing advocate only. "
    "Verify the current legal position, recent amendments, and jurisdiction-specific practice "
    "before filing, advising a client, or taking final legal action. "
    "No professional liability attaches to this output.*"
)

_IST_TZ = timezone(timedelta(hours=5, minutes=30))

# ─── Utilities ────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_ist() -> datetime:
    return datetime.now(_IST_TZ)


def _normalize_query(query: str) -> str:
    return " ".join((query or "").strip().lower().split())


def _query_hash(query: str) -> str:
    return hashlib.sha256(_normalize_query(query).encode("utf-8")).hexdigest()


def _rag_answer_insufficient(answer: str) -> bool:
    value = (answer or "").strip().lower()
    markers = [
        "do not have enough indexed content",
        "ingest relevant documents",
    ]
    return any(marker in value for marker in markers)


# ─── Format Detection ─────────────────────────────────────────────────────────

_CRIMINAL_MARKERS = re.compile(
    r"\b(ipc|crpc|bns|bnss|bsa|fir|cognizable|bailable|chargesheet|"
    r"section\s+\d|accused|bail|custody|remand|acquittal|conviction|"
    r"evidence act|criminal|penal|offence|offense)\b",
    re.IGNORECASE,
)
_DRAFTING_MARKERS = re.compile(
    r"\b(draft|nda|agreement|deed|clause|contract|mou|memorandum of understanding|"
    r"arbitration clause|non.?disclosure|template|format)\b",
    re.IGNORECASE,
)
_SECTION_LOOKUP_MARKERS = re.compile(
    r"\b(what is|define|meaning of|explain|section \d|article \d|"
    r"under section|under article|interpret|scope of)\b",
    re.IGNORECASE,
)
_CASE_PREP_MARKERS = re.compile(
    r"\b(case prep|argument|submissions|how to argue|strategy|"
    r"hearing|bench questions|oral argument|written submission)\b",
    re.IGNORECASE,
)


def _detect_format_mode(query: str, query_type: str) -> str:
    """Return one of: cheat_sheet | drafting | quick_check | argument_note"""
    q = (query or "").strip()
    qt = (query_type or "research").strip().lower()

    if qt == "drafting" or _DRAFTING_MARKERS.search(q):
        return "drafting"
    if qt == "case_prep" or _CASE_PREP_MARKERS.search(q):
        return "argument_note"
    if _CRIMINAL_MARKERS.search(q):
        return "cheat_sheet"
    if qt in {"section_lookup", "interpretation"} or _SECTION_LOOKUP_MARKERS.search(q):
        return "quick_check"
    return "argument_note"


# ─── Prompt Builder ───────────────────────────────────────────────────────────

_SENIOR_COUNSEL_PERSONA = """\
You are an elite legal strategy assistant — Senior Counsel's Strategic Clerk — \
for a Senior Advocate at the Indian Bar (20–30+ years PQE).

PRIME DIRECTIVE
• Do NOT explain basic law. Do NOT behave like a junior or law student.
• Deliver courtroom-ready insight, not textbook content.
• The instructing advocate focuses on arguments, strategy, and legal positioning — not drafting.

CRIMINAL LAW — NEW TRINITY PROTOCOL
For any criminal law query, provide three layers:
  Legacy  : IPC / CrPC / Evidence Act section
  Current : BNS / BNSS / BSA equivalent
  Delta   : ONLY what changed (procedural or substantive)
  If mapping is uncertain → state: "Mapping evolving / not yet settled"

JURISPRUDENCE-FIRST ENGINE
• Prioritise case law over statute text.
• Cite binding SC precedents first; note divergent HC views separately.
• Identify ratio decidendi and classify each point:
    ✅ Settled Law | ⚖️ Divergent Views | 🆕 Res Integra
• Key domains:
    IBC  → Swiss Ribbons, Essar Steel (creditor-in-control)
    PMLA → Vijay Madanlal Choudhary (twin conditions)
    Writ → Maintainability + Alternate Remedy doctrine

RAG CONTEXT PROTOCOL
• When retrieved context is provided (labeled [R1], [R2]…), prefer it over model memory.
• Cite as: "As per retrieved source [R1]…"
• If retrieved content conflicts with recalled law, flag: "Retrieved source [Rx] diverges — verify."

ANTI-HALLUCINATION RULES
• Never invent case laws, section numbers, or amendments.
• If unsure → "No direct authority found — suggest verification."

COURT HIERARCHY
  Supreme Court → Binding
  Same High Court → Binding
  Other High Courts → Persuasive
  Tribunals → Contextual

LANGUAGE & TONE
• Sharp. Authoritative. Minimal. Courtroom-ready.
• Use: "impugned order", "Ld. Counsel", "per incuriam", "prima facie", "ratio decidendi"
• Prohibited: "File petition", "Draft affidavit"
• Use instead: "Instruct AoR to…" | "Settle draft prepared by junior…"

COMMERCIAL / STRATEGIC LAYER
• Where relevant, include: risk exposure, enforcement practicality, litigation vs settlement strategy.

DIGITAL EVIDENCE
• Always check Section 65B certificate requirements (now under BSA), metadata integrity, e-filing compliance.\
"""

_FORMAT_DIRECTIVES: dict[str, str] = {
    "cheat_sheet": """\
OUTPUT FORMAT — Cheat Sheet (mandatory for this query)

| Legacy (IPC/CrPC/Evidence Act) | Current (BNS/BNSS/BSA) | Delta | Key SC Case |
|---|---|---|---|
[populate all rows]

Then add a brief Argument Note (3–5 numbered submissions, case-backed).
End with: **Legal Position:** ✅/⚖️/🆕
""",

    "argument_note": """\
OUTPUT FORMAT — Argument Note

**Submissions:**
1. [Precise legal proposition — statute + case]
2. [Next point]
… (as many as the facts require; each backed by authority)

**Legal Position:** ✅ Settled Law / ⚖️ Divergent Views / 🆕 Res Integra
**Risk Exposure:** [concise — enforcement, limitation, evidentiary gaps]
**Suggested Action:** Instruct AoR to… / Settle draft prepared by junior…

No introduction. No textbook narration. Authority for every proposition.
""",

    "quick_check": """\
OUTPUT FORMAT — Quick Check

**Provision / Concept:** [exact section + Act + year]
**Core Rule:** [one sentence — what it mandates or permits]
**Ingredients / Tests:** [bullet list]
**Key Exception:** [if any]
**Limitation / Timeline:** [if applicable]
**Key SC Case:** [citation + ratio in one line]
**Legal Position:** ✅/⚖️/🆕

No narration. Precision only.
""",

    "drafting": """\
OUTPUT FORMAT — Full Draft Instrument

Produce the complete draft document only.
• No preamble. No advisory wrapper. No "here is a draft…" introduction.
• Start directly with the document title.
• Use proper legal numbering (1., 1.1, 1.2…).
• Include all standard clauses: parties, definitions, operative provisions, term, \
termination, governing law, jurisdiction, dispute resolution.
• For NDA/confidentiality: include confidentiality obligations, IP assignment, \
return of information, and non-solicitation.
• Mark blanks as [PARTY NAME], [DATE], [CITY], etc.
• End with signature block.
The advocate will review; do not pre-disclaim or hedge within the document.
""",
}


def _build_rag_context_block(citations: list[dict[str, Any]]) -> str:
    """Format relevant RAG citations as a labeled context block for Gemini."""
    if not citations:
        return ""
    parts = ["RETRIEVED CONTEXT (prefer over model memory where relevant):"]
    for i, c in enumerate(citations[:6], start=1):
        title = c.get("title") or c.get("reference") or f"Source {i}"
        snippet = c.get("snippet") or c.get("text") or ""
        date = c.get("date") or c.get("published_date") or ""
        source = c.get("source") or ""
        meta = []
        if source:
            meta.append(source)
        if date:
            meta.append(date)
        meta_str = " | ".join(meta)
        entry = f"[R{i}] {title}"
        if meta_str:
            entry += f" ({meta_str})"
        if snippet:
            entry += f"\n     {snippet[:400]}"
        parts.append(entry)
    return "\n".join(parts)


def _build_senior_counsel_prompt(
    query: str,
    format_mode: str,
    rag_context: str,
    today_ist: str,
) -> str:
    format_directive = _FORMAT_DIRECTIVES.get(format_mode, _FORMAT_DIRECTIVES["argument_note"])

    sections: list[str] = [
        _SENIOR_COUNSEL_PERSONA,
        f"Date (IST): {today_ist}",
    ]
    if rag_context:
        sections.append(rag_context)

    sections.append(format_directive)
    sections.append(f"QUERY:\n{query.strip()}")

    return "\n\n".join(sections)


# ─── Citation Relevance Filter ────────────────────────────────────────────────

_LEGAL_QUERY_WORD_RE = re.compile(r"[a-z0-9]+")
_LEGAL_QUERY_STOPWORDS = {
    "what", "which", "when", "where", "who", "whom", "whose",
    "why", "how", "is", "are", "was", "were", "do", "does",
    "did", "can", "could", "should", "would", "please", "explain",
    "briefly", "about", "tell", "me", "the", "for", "and", "with",
    "a", "an", "of", "in", "on", "to", "by", "as", "or", "if",
    "this", "that", "these", "those", "be", "been", "being",
    "have", "has", "had", "from", "any", "all", "there", "here",
    "under", "over", "into", "per", "via", "than", "then",
}


def _extract_meaningful_query_terms(query: str) -> set[str]:
    tokens = set(_LEGAL_QUERY_WORD_RE.findall((query or "").lower()))
    return {t for t in tokens if len(t) >= 4 and t not in _LEGAL_QUERY_STOPWORDS}


def _citation_is_relevant(citation: dict[str, Any], query_terms: set[str]) -> tuple[bool, int, float]:
    """Return (relevant, overlap_count, overlap_ratio) for a single citation.

    Relevance rule: at least 2 meaningful query terms must appear in the citation's
    snippet/title/legal-metadata/reference, OR at least 30% of meaningful terms
    overlap.

    If the citation exposes no inspectable content, treat as relevant (stubs in tests).
    """
    if not query_terms:
        return (True, 0, 1.0)

    haystack_parts: list[str] = []
    for key in ("snippet", "title"):
        val = citation.get(key)
        if val:
            haystack_parts.append(str(val))
    legal_meta = citation.get("legal_metadata") or {}
    if isinstance(legal_meta, dict):
        for val in legal_meta.values():
            if val:
                haystack_parts.append(str(val))

    haystack = " ".join(haystack_parts).lower().strip()
    if not haystack:
        return (True, 0, 1.0)

    haystack_tokens = set(_LEGAL_QUERY_WORD_RE.findall(haystack))
    hits = query_terms.intersection(haystack_tokens)
    ratio = len(hits) / max(len(query_terms), 1)

    relevant = len(hits) >= 2 or ratio >= 0.30
    return (relevant, len(hits), ratio)


def _filter_citations_by_relevance(
    citations: list[dict[str, Any]], query: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split citations into (relevant, dropped)."""
    query_terms = _extract_meaningful_query_terms(query)
    if not query_terms:
        return (list(citations), [])

    relevant: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for c in citations:
        is_rel, hits, ratio = _citation_is_relevant(c, query_terms)
        if is_rel:
            relevant.append(c)
        else:
            _logger.debug(
                "citation_dropped title=%r hits=%d ratio=%.2f",
                c.get("title") or c.get("reference") or "?", hits, ratio,
            )
            dropped.append(c)
    return (relevant, dropped)


# ─── Gemini API Call ──────────────────────────────────────────────────────────

async def _call_gemini_text(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
    settings = get_settings()
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        _logger.warning("gemini_call skipped: GEMINI_API_KEY not configured")
        return None

    api_base = settings.RAG_GEMINI_API_BASE.rstrip("/")
    model = settings.LEGAL_FALLBACK_GEMINI_MODEL

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "topP": 0.9,
        },
    }

    url = f"{api_base}/models/{model}:generateContent"
    _logger.info(
        "gemini_call start model=%s prompt_len=%d max_tokens=%d temperature=%.2f",
        model, len(prompt), max_tokens, temperature,
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params={"key": api_key}, json=payload)

        if response.status_code >= 400:
            body_excerpt = (response.text or "")[:500]
            _logger.error(
                "gemini_call http_error status=%d model=%s body=%s",
                response.status_code, model, body_excerpt,
            )
            return None

        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            prompt_feedback = body.get("promptFeedback") or {}
            _logger.warning(
                "gemini_call empty_candidates model=%s promptFeedback=%s",
                model, prompt_feedback,
            )
            return None

        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
        text = "\n".join(
            str(part.get("text") or "") for part in parts if isinstance(part, dict)
        ).strip()
        if not text:
            finish_reason = (candidates[0] or {}).get("finishReason")
            _logger.warning(
                "gemini_call empty_text model=%s finishReason=%s", model, finish_reason
            )
            return None

        _logger.info("gemini_call ok model=%s response_len=%d", model, len(text))
        return text
    except Exception as exc:
        _logger.exception("gemini_call exception model=%s err=%s", model, exc)
        return None


# ─── Main Response Builder ────────────────────────────────────────────────────

async def build_hybrid_legal_response(
    *,
    tenant_id: str,
    app_key: str,
    query: str,
    query_type: str = "research",
    rag_result: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    citations = list(rag_result.get("citations") or [])

    # Filter RAG citations for relevance — only topically matching ones go into context.
    relevant_citations, dropped_citations = _filter_citations_by_relevance(citations, query)

    query_preview = (query or "")[:80]
    _logger.info(
        "hybrid_response tenant=%s app=%s rag_citations=%d relevant=%d dropped=%d query=%r",
        tenant_id, app_key, len(citations), len(relevant_citations),
        len(dropped_citations), query_preview,
    )

    # Enqueue low-confidence queries for background sync regardless of Gemini outcome.
    if not relevant_citations and background_tasks is not None:
        background_tasks.add_task(
            enqueue_auto_sync_query,
            tenant_id=tenant_id,
            app_key=app_key,
            query=query,
            reason="low_rag_confidence",
        )

    # Build the Gemini prompt — relevant RAG snippets become context, not the answer.
    today_ist = _now_ist().strftime("%d-%m-%Y")
    format_mode = _detect_format_mode(query, query_type)
    rag_context = _build_rag_context_block(relevant_citations)

    prompt = _build_senior_counsel_prompt(
        query=query,
        format_mode=format_mode,
        rag_context=rag_context,
        today_ist=today_ist,
    )

    gemini_answer = await _call_gemini_text(
        prompt=prompt,
        max_tokens=max(settings.LEGAL_FALLBACK_MAX_TOKENS, 2000),
        temperature=0.15,
    )

    if gemini_answer and gemini_answer.strip():
        response_text = gemini_answer.strip() + _CLOSING_DISCLAIMER
        _logger.info(
            "hybrid_response path=gemini tenant=%s app=%s format=%s response_len=%d",
            tenant_id, app_key, format_mode, len(response_text),
        )
        return {
            "response": response_text,
            "citations": relevant_citations,
            "strategy": f"{str(rag_result.get('strategy') or 'rag')}_gemini",
            "note": None,
            "dropped_citation_count": len(dropped_citations),
        }

    # Gemini unavailable or returned empty — return a clean, honest failure.
    _logger.warning(
        "hybrid_response path=gemini_unavailable tenant=%s app=%s (no API key or empty response)",
        tenant_id, app_key,
    )
    return {
        "response": (
            "**Advisory Unavailable**\n\n"
            "The AI engine did not return a response for this query. "
            "This may be a transient issue or an unsupported query type.\n\n"
            "**Suggested action:** Retry the query, narrow the scope, "
            "or route to a junior for manual research."
        ),
        "citations": relevant_citations,
        "strategy": "gemini_unavailable",
        "note": "AI engine did not respond — retry or rephrase the query.",
        "dropped_citation_count": len(dropped_citations),
    }


# ─── Index & Queue Management ─────────────────────────────────────────────────

async def ensure_legal_compat_indexes() -> None:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    await queue.create_index([("tenant_id", 1), ("app_key", 1), ("status", 1), ("created_at", -1)])
    await queue.create_index([("status", 1), ("updated_at", 1)])
    await queue.create_index(
        [("tenant_id", 1), ("app_key", 1), ("query_hash", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={"status": "pending"},
    )


async def enqueue_auto_sync_query(*, tenant_id: str, app_key: str, query: str, reason: str) -> None:
    settings = get_settings()
    if not settings.RAG_AUTO_SYNC_ENABLED:
        return

    normalized_query = _normalize_query(query)
    if not normalized_query:
        return

    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    now = _now_utc()
    doc = {
        "job_id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": app_key,
        "query": query.strip(),
        "normalized_query": normalized_query,
        "query_hash": _query_hash(query),
        "reason": reason,
        "status": "pending",
        "attempt_count": 0,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }

    await queue.update_one(
        {
            "tenant_id": tenant_id,
            "app_key": app_key,
            "query_hash": doc["query_hash"],
            "status": "pending",
        },
        {
            "$setOnInsert": doc,
            "$set": {"last_seen_at": now},
        },
        upsert=True,
    )


async def list_sync_queue(
    *, tenant_id: str, app_key: str, status: str = "pending", limit: int = 50
) -> list[dict[str, Any]]:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    status_value = (status or "pending").strip().lower()
    cursor = (
        queue.find({"tenant_id": tenant_id, "app_key": app_key, "status": status_value})
        .sort("updated_at", -1)
        .limit(limit)
    )

    items: list[dict[str, Any]] = []
    async for doc in cursor:
        items.append(
            {
                "job_id": str(doc.get("job_id") or ""),
                "query": str(doc.get("query") or ""),
                "reason": str(doc.get("reason") or ""),
                "status": str(doc.get("status") or "pending"),
                "attempt_count": int(doc.get("attempt_count") or 0),
                "last_error": doc.get("last_error"),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            }
        )

    return items
