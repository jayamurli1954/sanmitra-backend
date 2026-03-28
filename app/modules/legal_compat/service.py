from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks

from app.config import get_settings
from app.db.mongo import get_collection

RAG_SYNC_QUEUE_COLLECTION = "rag_sync_queue"
_INTRO_ADVISORY = "Important Note: This is informational legal guidance generated from available context."
_CLOSING_DISCLAIMER = (
    "Disclaimer:\n"
    "- Verify latest statutes, notifications, and binding judgments independently before relying on this output.\n"
    "- Procedural requirements and maintainability can vary by court/forum and fact pattern.\n"
    "- Obtain case-specific advice from a qualified advocate before filing, advising a client, or taking final legal action."
)
_NEUTRAL_NOTE = (
    "Important Note: Verify the latest legal position and forum-specific rules before reliance.\n"
    "Disclaimer: Informational support only; seek advocate review for case-specific action."
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


def _looks_brief(answer: str) -> bool:
    text = (answer or "").strip()
    if len(text) < 650:
        return True
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    return len(non_empty_lines) < 8


def _is_definition_query(query: str) -> bool:
    q = (query or "").strip().lower()
    return q.startswith("what is") or q.startswith("what is a") or q.startswith("define")


def _normalize_query_type(query_type: str | None) -> str:
    value = (query_type or "research").strip().lower()
    allowed = {
        "research",
        "drafting",
        "opinion",
        "case_prep",
        "summary",
        "section_lookup",
        "interpretation",
    }
    return value if value in allowed else "research"


def _query_type_guidance(query_type: str) -> str:
    guidance = {
        "research": (
            "Provide comprehensive legal research with issue framing, applicable law, practical checklist, risks, "
            "and actionable next steps."
        ),
        "drafting": (
            "Produce drafting-oriented guidance: required clauses, structure, placeholders, filing tips, and a model outline."
        ),
        "opinion": (
            "Give a legal-opinion style response with assumptions, issues, applicable law, analysis, conclusion, and risk flags."
        ),
        "case_prep": (
            "Give litigation preparation guidance: chronology, issues matrix, evidence plan, authorities strategy, and hearing checklist."
        ),
        "summary": (
            "Provide an executive summary first, then key legal points, practical implications, and what to verify next."
        ),
        "section_lookup": (
            "Explain the section in plain language, ingredients/tests, timelines, exceptions, common mistakes, and usage in practice."
        ),
        "interpretation": (
            "Interpret provisions with text-purpose-context method, competing views, likely court approach, and practical impact."
        ),
    }
    return guidance.get(query_type, guidance["research"])


def _extract_definition_term(query: str) -> str:
    q = (query or "").strip().rstrip("?")
    lower = q.lower()
    if lower.startswith("what is"):
        term = q[7:].strip()
    elif lower.startswith("define"):
        term = q[6:].strip()
    else:
        term = q

    noise_phrases = [
        "explain me briefly",
        "explain briefly",
        "in brief",
        "briefly",
    ]
    term_lower = term.lower()
    for phrase in noise_phrases:
        idx = term_lower.find(phrase)
        if idx >= 0:
            term = term[:idx].strip()
            break

    term = term.strip(" :-,.;")
    return term or "This legal concept"


def _needs_expansion_for_query(query: str, answer: str) -> bool:
    if _looks_brief(answer):
        return True

    if _is_definition_query(query):
        lower = (answer or "").lower()
        markers = [
            "simple meaning",
            "what does it allow",
            "who signs",
            "where is it used",
            "important points",
            "example",
            "why it matters",
            "professional checklist",
        ]
        hits = sum(1 for m in markers if m in lower)
        text = (answer or "").strip()
        if not text.endswith((".", "!", "?")):
            return True
        return hits < 7

    return False


def _is_gst_query(query: str) -> bool:
    q = (query or "").lower()
    markers = [
        "gst",
        "goods and services tax",
        "input tax credit",
        "itc",
        "gstr-1",
        "gstr 1",
        "gstr-3b",
        "gstr 3b",
        "reverse charge",
        "hsn",
        "tax invoice",
    ]
    return any(marker in q for marker in markers)


def _gst_professional_fallback(query: str) -> str:
    q = (query or "").lower()
    is_startup = "startup" in q
    is_saas = any(marker in q for marker in ["saas", "software", "platform", "subscription", "app", "legalmitra", "tech"])
    business_label = "your startup"
    if "sanmitratech" in q or "legalmitra" in q:
        business_label = "SanmitraTech-LegalMitra"

    direct_answer = (
        "GST may become applicable early. "
        "If your business provides inter-state taxable digital services, registration can be required irrespective of threshold; "
        "otherwise threshold-based registration rules apply. For SaaS/digital legal-tech services, the common output tax rate in practice is 18% (subject to exact classification)."
    )

    if is_startup and is_saas:
        direct_answer = (
            f"For {business_label} (assumed legal-tech SaaS), GST is likely applicable from an early stage in many real operating setups, "
            "especially if you invoice clients across states. Typical service tax rate in practice is 18% for SaaS/digital services, subject to correct classification and latest notifications."
        )

    return (
        "## Quick Answer (for your exact query)\n"
        f"{direct_answer}\n\n"
        "## Quick Summary\n"
        "- GST Required: Depends on threshold + mandatory-registration triggers (inter-state/e-commerce/reverse-charge contexts).\n"
        "- Likely Classification: Taxable service (for SaaS/legal-tech style offerings, subject to final mapping).\n"
        "- Typical Rate in Practice: 18% for many SaaS/digital services (verify exact SAC and latest notification position).\n"
        "- Filing Pattern: Usually monthly return workflow (GSTR-1, GSTR-3B, reconciliation), unless eligible for notified alternatives.\n\n"
        "## Business-Specific Impact\n"
        f"- Assumption used: query appears related to {business_label}.\n"
        "- If your customers are in multiple states, place-of-supply and inter-state treatment become the highest-priority controls.\n"
        "- Pricing, contracts, and invoice structure must align with GST treatment to avoid margin leakage and disputes.\n\n"
        "## Key Rules to Validate Immediately\n"
        "1. Registration trigger: threshold rule + mandatory-registration scenarios relevant to your model.\n"
        "2. Classification: correct SAC/HSN mapping and rate support documentation.\n"
        "3. Place of supply: IGST vs CGST/SGST logic for each supply pattern.\n"
        "4. ITC controls: vendor compliance, invoice quality, and reconciliation evidence.\n"
        "5. Contract safeguards: tax change, withholding, indemnity, and invoice acceptance clauses.\n\n"
        "## Risk Hotspots\n"
        "- Wrong place-of-supply mapping causing tax mismatch and interest exposure.\n"
        "- Incorrect discount/credit note treatment impacting output tax and ITC.\n"
        "- Delayed return filing creating compounding compliance and cash-flow pressure.\n\n"
        "## 7-Day Action Plan\n"
        "1. Build state-wise transaction map (service lines, client type, invoicing channel).\n"
        "2. Run mandatory-registration checklist and record legal basis for each conclusion.\n"
        "3. Finalize SAC/rate matrix with notification references.\n"
        "4. Set return + reconciliation calendar with owner and cut-off dates.\n"
        "5. Conduct one CA/indirect-tax legal review before scale-up billing."
    )


def _topic_specific_fallback(query: str) -> str | None:
    if _is_gst_query(query):
        return _gst_professional_fallback(query)
    return None


def _default_direct_answer_line(query: str, query_type: str) -> str:
    q = (query or "").strip()
    mode = _normalize_query_type(query_type)
    if _is_gst_query(q):
        return (
            "Based on your GST query, the immediate legal answer is: registration and compliance obligations depend on threshold plus mandatory-registration triggers, "
            "and SaaS/digital services are commonly taxed at 18% subject to exact classification and latest notification position."
        )
    if mode == "drafting":
        return "Based on your drafting query, the immediate legal answer is: start with forum-specific maintainability requirements and then structure facts, grounds, and relief in that order."
    if mode == "case_prep":
        return "Based on your case-prep query, the immediate legal answer is: build chronology + evidence matrix first, then map authorities issue-wise before hearing strategy."
    if mode in {"opinion", "interpretation", "section_lookup"}:
        return "Based on your query, the immediate legal answer is: apply the exact statutory ingredients to your facts and validate limitation, jurisdiction, and evidence before taking final action."
    return "Based on your query, the immediate legal answer is: determine the exact statute/procedure applicable to your facts first, then execute the action plan only after maintainability and timeline checks."


def _ensure_direct_answer_prefix(answer: str, query: str, query_type: str) -> str:
    body = (answer or "").strip()
    if not body:
        body = "No substantive answer could be generated."

    lower = body.lower()
    if "quick answer" in lower or "direct answer" in lower:
        return body

    direct_line = _default_direct_answer_line(query, query_type)
    prefix = f"## Quick Answer (for your exact query)\n{direct_line}"
    return f"{prefix}\n\n{body}".strip()


def _build_follow_up_probe(query: str, query_type: str) -> str:
    mode = _normalize_query_type(query_type)

    if _is_gst_query(query):
        return (
            "## If You Want, I Can\n"
            "- Build a startup-specific GST applicability decision matrix (threshold + mandatory-registration triggers).\n"
            "- Prepare a month-wise compliance workflow (GSTR-1, GSTR-3B, reconciliation, payment checkpoints).\n\n"
            "Would you like me to generate this for your exact setup using turnover, operating states, and B2B/B2C mix?"
        )

    if mode == "drafting":
        return (
            "## If You Want, I Can\n"
            "- Convert this into a section-wise draft format ready for filing/use.\n"
            "- Prepare clause language with placeholders for your exact facts.\n\n"
            "Would you like me to draft it now if you share parties, dates, and forum?"
        )

    if mode == "case_prep":
        return (
            "## If You Want, I Can\n"
            "- Build an issue-wise evidence matrix for hearing prep.\n"
            "- Create a short oral-argument flow with likely bench questions.\n\n"
            "Would you like me to do that using your chronology and available documents?"
        )

    if mode in {"opinion", "interpretation", "section_lookup"}:
        return (
            "## If You Want, I Can\n"
            "- Convert this into a fact-specific legal opinion format.\n"
            "- Add issue-wise risk grading and recommended action priority.\n\n"
            "Would you like me to prepare that if you share key facts and jurisdiction?"
        )

    return (
        "## If You Want, I Can\n"
        "- Convert this into a concise decision note for immediate execution.\n"
        "- Prepare a fact checklist to make the advice case-specific.\n\n"
        "Would you like me to generate that for your exact facts now?"
    )


def _append_follow_up_probe(answer: str, query: str, query_type: str) -> str:
    body = (answer or "").strip()
    if not body:
        return _build_follow_up_probe(query, query_type)

    if "if you want, i can" in body.lower():
        return body

    return f"{body}\n\n{_build_follow_up_probe(query, query_type)}".strip()


def _finalize_response_text(answer: str, query: str, query_type: str) -> str:
    body = _ensure_direct_answer_prefix(answer, query, query_type)
    body = _append_follow_up_probe(body, query, query_type)
    return _decorate_response_text(body)


def _general_professional_fallback(query: str, query_type: str = "research") -> str:
    mode = _normalize_query_type(query_type)

    topic_answer = _topic_specific_fallback(query)
    if topic_answer:
        return topic_answer

    if _is_definition_query(query):
        term = _extract_definition_term(query)
        return (
            f"## Simple Meaning\n"
            f"{term} is a legal authorization document used in Indian legal practice. It formally allows a professional representative to act for a client in legal proceedings.\n\n"
            "## What Does It Allow\n"
            "- Appearance and representation before court/tribunal\n"
            "- Filing pleadings, applications, and procedural documents\n"
            "- Receiving notices and communications in the matter\n"
            "- Conducting hearing-related procedural steps\n\n"
            "## Who Signs It\n"
            "- Client/authorized signatory\n"
            "- Advocate/authorized counsel (as per practice)\n"
            "- Witness/signing formalities, where required by forum practice\n\n"
            "## Where It Is Used\n"
            "- Civil and criminal courts\n"
            "- High Courts, Supreme Court, and tribunals\n"
            "- Regulatory/adjudicatory forums requiring formal authorization\n\n"
            "## Important Points\n"
            "- Execute before substantive representation starts\n"
            "- Ensure names, case details, and scope of authority are correct\n"
            "- Follow local court rules on format, stamps, and filing\n\n"
            "## Practical Example\n"
            "If a client appoints counsel for a cheque bounce matter, this document enables counsel to file, appear, and conduct procedural steps on behalf of the client.\n\n"
            "## Why It Matters\n"
            "Without proper authorization, representation can be procedurally challenged; with proper execution, proceedings remain valid and efficient.\n\n"
            "## Immediate Checklist\n"
            "- Verify party names and addresses\n"
            "- Confirm forum-specific format and stamp requirements\n"
            "- Check sign/date/witness formalities\n"
            "- File with initial pleadings and keep a client copy"
        )

    if mode == "drafting":
        return (
            f"## Drafting Objective\n"
            f"Query: {query}\n\n"
            "## Recommended Document Structure\n"
            "1. Title and jurisdiction heading\n"
            "2. Parties and capacities\n"
            "3. Factual background in chronology\n"
            "4. Legal basis with sections/rules\n"
            "5. Relief/prayer and interim relief (if any)\n"
            "6. Verification, annexure list, and signatures\n\n"
            "## Mandatory Inputs Before Drafting\n"
            "- Correct party names, addresses, IDs\n"
            "- Dates/events timeline with supporting documents\n"
            "- Applicable forum and territorial jurisdiction\n"
            "- Limitation computation and delay explanation (if needed)\n\n"
            "## Drafting Checklist\n"
            "- Use precise facts; avoid allegations without proof\n"
            "- Keep each paragraph single-purpose and numbered\n"
            "- Cross-reference annexures in the facts section\n"
            "- Ensure prayer exactly matches maintainable relief\n"
            "- Add service details and procedural compliance notes\n\n"
            "## Immediate Next Step\n"
            "Share parties, dates, forum, and relief sought to generate a section-wise draft."
        )

    if mode == "case_prep":
        return (
            f"## Case Preparation Plan\n"
            f"Query: {query}\n\n"
            "## Step 1: Chronology and Issues Matrix\n"
            "- Build a date-wise chronology with document proof for each event\n"
            "- Frame issues as legal questions to be answered by evidence and law\n\n"
            "## Step 2: Evidence Strategy\n"
            "- Primary documents, admissions, electronic records, and service proofs\n"
            "- Witness list with purpose of each witness\n"
            "- Gaps, contradictions, and likely objections from opposite side\n\n"
            "## Step 3: Authorities Strategy\n"
            "- Leading binding precedents first, then persuasive authorities\n"
            "- Map each precedent to one specific issue\n\n"
            "## Step 4: Hearing Prep\n"
            "- 3-minute opening, issue-wise argument notes, and fallback points\n"
            "- Bench questions likely to arise and short answers\n\n"
            "## Step 5: Risk Controls\n"
            "- Limitation/jurisdiction checks\n"
            "- Procedural defects and cure strategy\n"
            "- Settlement window and negotiation position"
        )

    if mode in {"section_lookup", "interpretation", "opinion"}:
        return (
            f"## Legal Position\n"
            f"Query: {query}\n\n"
            "## Core Provision/Issue\n"
            "Identify exact section/rule and trigger conditions from facts.\n\n"
            "## Practical Interpretation\n"
            "- Ingredients/tests that must be satisfied\n"
            "- Procedural requirements and timelines\n"
            "- Common defenses/exceptions and when they apply\n\n"
            "## Application Framework\n"
            "- Match each fact to each legal ingredient\n"
            "- Identify missing evidence/documents\n"
            "- Assess maintainability before filing/advising\n\n"
            "## Risk Notes\n"
            "- Limitation, jurisdiction, service, and evidentiary vulnerabilities\n"
            "- Compliance steps required to reduce challenge risk\n\n"
            "## Next Action\n"
            "Share facts timeline, forum, and key documents for a fact-specific opinion."
        )

    if mode == "summary":
        return (
            f"## Executive Summary\n"
            f"For \"{query}\", the legal approach is to first verify the governing statute, limitation timeline, and forum, then align facts and documents issue-wise before taking procedural action.\n\n"
            "## Key Points\n"
            "- Confirm exact legal provision and current applicability\n"
            "- Verify limitation start date and stop-date events\n"
            "- Prepare chronology + annexures + service proof\n"
            "- Validate jurisdiction and maintainability conditions\n"
            "- Anticipate likely defenses and evidentiary gaps\n\n"
            "## Immediate Checklist\n"
            "- Party details and addresses\n"
            "- Date-wise event chart\n"
            "- Supporting documents and communication trail\n"
            "- Relief sought and forum selection"
        )

    return (
        f"For your query, \"{query}\", here is a practical legal overview:\n\n"
        "1. Core issue:\n"
        "Identify the exact legal relationship, applicable law, and the triggering facts (dates, notices, payments, agreements, or filings).\n\n"
        "2. Governing framework:\n"
        "Map the issue to the relevant Indian statute/procedure and check jurisdiction-specific practice before taking action.\n\n"
        "3. Immediate actions:\n"
        "Collect documents in chronology, preserve proof of communication/service, and confirm limitation deadlines before issuing notice or filing.\n\n"
        "4. Practical checklist:\n"
        "Prepare party details, factual timeline, documentary annexures, legal grounds, relief sought, and jurisdiction clause/court selection.\n\n"
        "5. Risk and strategy:\n"
        "Evaluate likely defenses from the opposite side, evidentiary gaps, and whether settlement/mediation is strategically better than immediate litigation.\n\n"
        "6. Next step for precise drafting:\n"
        "Share relevant dates, document type, court/jurisdiction, and the exact relief needed; then a detailed case-specific draft can be prepared."
    )



def _decorate_response_text(answer: str) -> str:
    body = (answer or "").strip()
    if not body:
        body = "No substantive answer could be generated."

    lower = body.lower()
    has_intro = "important note" in lower
    has_disclaimer = "disclaimer" in lower

    parts: list[str] = []
    if not has_intro:
        parts.append(_INTRO_ADVISORY)
    parts.append(body)
    if not has_disclaimer:
        parts.append(_CLOSING_DISCLAIMER)

    return "\n\n".join(parts).strip()

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


async def _call_gemini_text(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
    settings = get_settings()
    api_key = settings.GEMINI_API_KEY
    if not api_key:
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
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params={"key": api_key}, json=payload)

        if response.status_code >= 400:
            return None

        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            return None

        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
        text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        return text or None
    except Exception:
        return None


async def _generate_gemini_fallback_answer(query: str, query_type: str = "research") -> str | None:
    settings = get_settings()
    mode = _normalize_query_type(query_type)
    mode_guidance = _query_type_guidance(mode)
    shared_style = (
        "You are LegalMitra, a specialized Indian legal assistant for advocates and professionals. "
        "Provide high-quality, practical, detailed responses. "
        "Do not mention internal systems, indexing, retrieval, embeddings, or model limitations. "
        "Do not fabricate citations. If uncertain, state practical assumptions clearly."
    )

    if _is_definition_query(query):
        draft_prompt = (
            f"{shared_style}\n\n"
            "Answer the user in clear, practical markdown with section headings and bullet points.\n\n"
            "Use this exact section structure:\n"
            "## Quick Answer (for your exact query)\n"
            "## Simple Meaning\n"
            "## What Does It Allow\n"
            "## Who Signs It\n"
            "## Where Is It Used\n"
            "## Important Points\n"
            "## Practical Example\n"
            "## Why It Matters\n"
            "## Professional Checklist\n"
            "## If You Want, I Can\n\n"
            "Style requirements:\n"
            "- Plain English, practical, concise but complete\n"
            "- 8-14 bullets overall\n"
            "- Indian legal context\n"
            "- The first section must directly answer the user's query\n"
            "- End with exactly one targeted follow-up question in the last section\n\n"
            f"User query: {query}"
        )
    else:
        draft_prompt = (
            f"{shared_style}\n\n"
            f"Query type: {mode}\n"
            f"Query-type guidance: {mode_guidance}\n\n"
            "Provide a practical, detailed response to the user query in clear markdown with short headings.\n\n"
            "Required structure (in this exact order):\n"
            "1) ## Quick Answer (for your exact query)\n"
            "2) ## Business-Specific Impact\n"
            "3) ## Key Rules / Legal Position (Indian context)\n"
            "4) ## Action Plan\n"
            "5) ## Risks and Precautions\n"
            "6) ## If You Want, I Can\n\n"
            "Mandatory output behavior:\n"
            "- The first section must give a direct answer (yes/no/likely outcome/range where possible)\n"
            "- Tailor to business context inferred from user query; state assumptions explicitly\n"
            "- Avoid generic-only narration; every section should tie back to the query\n"
            "- Include one targeted follow-up question in the last section\n"
            "- Do not add fabricated citations\n"
            "- Keep depth practical and professional (8-14 bullets total)\n\n"
            f"User query: {query}"
        )

    text = await _call_gemini_text(
        prompt=draft_prompt,
        max_tokens=max(settings.LEGAL_FALLBACK_MAX_TOKENS, 1400),
        temperature=0.15,
    )

    if not text:
        return None

    candidate = text.strip()
    if _needs_expansion_for_query(query, candidate):
        expand_prompt = (
            f"{shared_style}\n\n"
            f"Query type: {mode}\n"
            f"Query-type guidance: {mode_guidance}\n\n"
            "Expand the following draft into a fuller professional advisory note for an Indian legal audience. "
            "Keep it practical and structured; add missing direct answer specificity, business-specific impact, risks, and action checklist. "
            "End with an 'If You Want, I Can' section and one targeted question. "
            "Do not add fabricated citations.\n\n"
            f"Query: {query}\n\n"
            f"Draft answer:\n{candidate}"
        )
        expanded = await _call_gemini_text(
            prompt=expand_prompt,
            max_tokens=max(settings.LEGAL_FALLBACK_MAX_TOKENS, 1800),
            temperature=0.15,
        )
        if expanded and len(expanded.strip()) > len(candidate):
            candidate = expanded.strip()

    if _needs_expansion_for_query(query, candidate):
        return _general_professional_fallback(query, query_type=mode)

    return candidate


async def build_hybrid_legal_response(
    *,
    tenant_id: str,
    app_key: str,
    query: str,
    query_type: str = "research",
    rag_result: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any]:
    citations = list(rag_result.get("citations") or [])
    rag_answer = str(rag_result.get("answer") or "").strip()

    if citations:
        # Preserve grounded RAG output when citations exist.
        answer = rag_answer or _general_professional_fallback(query, query_type=query_type)
        return {
            "response": answer,
            "citations": citations,
            "strategy": str(rag_result.get("strategy") or "rag"),
            "note": None,
        }

    if background_tasks is not None:
        background_tasks.add_task(
            enqueue_auto_sync_query,
            tenant_id=tenant_id,
            app_key=app_key,
            query=query,
            reason="low_rag_confidence",
        )

    decorated_note = _NEUTRAL_NOTE

    fallback_answer = await _generate_gemini_fallback_answer(query, query_type=query_type)
    if fallback_answer:
        return {
            "response": _finalize_response_text(fallback_answer, query, query_type),
            "citations": [],
            "strategy": f"{str(rag_result.get('strategy') or 'rag')}_fallback",
            "note": decorated_note,
        }

    if rag_answer and (not _rag_answer_insufficient(rag_answer)) and len(rag_answer) >= 180:
        answer = rag_answer
    else:
        answer = _general_professional_fallback(query, query_type=query_type)

    return {
        "response": _finalize_response_text(answer, query, query_type),
        "citations": [],
        "strategy": str(rag_result.get("strategy") or "rag"),
        "note": decorated_note,
    }


async def list_sync_queue(*, tenant_id: str, app_key: str, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
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
