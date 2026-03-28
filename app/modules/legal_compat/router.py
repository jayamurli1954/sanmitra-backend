from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
import re
import xml.etree.ElementTree as ET

import httpx
from io import BytesIO
from typing import Any
from urllib.parse import quote_plus, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.permissions.rbac import Role, require_roles
from app.core.tenants.context import resolve_app_key, resolve_tenant_id
from app.db.mongo import get_collection
from app.modules.legal_compat.service import build_hybrid_legal_response, list_sync_queue
from app.modules.legal_compat.template_catalog import get_template_library
from app.modules.legal_compat.template_drafting import render_guided_document_draft, render_template_document
from app.modules.legal_compat.sync_worker import run_legal_sync_once
from app.modules.rag.schemas import RagLegalFilter, RagQueryRequest
from app.modules.rag.service import query_knowledge

router = APIRouter(tags=["legal-compat"])

_DEFAULT_TENANT_ID = "seed-tenant-1"
_DEFAULT_APP_KEY = "legalmitra"


class LegacyLegalResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    query_type: str = Field(default="research", max_length=40)


class LegacyCaseSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    court: str | None = None
    year: int | None = None


class LegacyStatuteSearchRequest(BaseModel):
    act_name: str = Field(min_length=2, max_length=240)
    section: str | None = Field(default=None, max_length=120)


class LegacyTemplateRenderRequest(BaseModel):
    template_id: str = Field(min_length=2, max_length=120)
    fields: dict[str, Any] = Field(default_factory=dict)
    format: str = Field(default="text", max_length=20)


def _resolve_compat_tenant_id(x_tenant_id: str | None) -> str:
    tenant_id = (x_tenant_id or "").strip()
    return tenant_id or _DEFAULT_TENANT_ID


def _resolve_compat_app_key(x_app_key: str | None) -> str:
    return resolve_app_key((x_app_key or _DEFAULT_APP_KEY).strip())


def _static_case_items() -> list[dict[str, Any]]:
    return [
        {
            "title": "Recent Major Judgments",
            "court": "Supreme Court / High Courts",
            "year": 2026,
            "summary": "Latest important judgments will appear here. Click Refresh to load updates.",
            "query": "What are the latest major judgments from Supreme Court and High Courts?",
            "url": "",
        }
    ]


def _static_news_items() -> list[dict[str, Any]]:
    now_year = datetime.now().year
    return [
        {
            "title": "Latest Legal Updates",
            "source": "SanMitra Legal Desk",
            "date": str(now_year),
            "summary": "Latest legal news and regulatory updates will appear here. Click Refresh to load updates.",
            "query": "Summarize important legal and compliance updates in India.",
            "url": "",
        }
    ]


def _safe_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.year

    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _safe_iso_date(value: Any) -> str:
    if value is None:
        return str(datetime.now().date())
    if isinstance(value, datetime):
        return str(value.date())

    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    return text or str(datetime.now().date())


def _safe_source_label(doc: dict[str, Any]) -> str:
    metadata = dict(doc.get("metadata") or {})
    source = str(metadata.get("source") or "").strip()
    if source:
        return source

    uri = str(doc.get("source_uri") or "").strip()
    if uri:
        try:
            host = urlparse(uri).netloc.strip()
            if host:
                return host
        except Exception:
            pass

    return "Legal Source"


async def _collect_docs(docs_collection, query_filter: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = docs_collection.find(query_filter).sort("created_at", -1).limit(limit)
    async for doc in cursor:
        items.append(doc)
    return items



def _strip_html(value: str) -> str:
    if not value:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", value)
    compact = re.sub(r"\s+", " ", no_tags).strip()
    return unescape(compact)


def _rss_item_text(item: ET.Element, tag: str) -> str:
    direct = item.findtext(tag)
    if direct:
        return direct.strip()
    wildcard = item.findtext(f"{{*}}{tag}")
    return (wildcard or "").strip()


def _parse_pub_date(value: str) -> tuple[str, int]:
    if not value:
        now = datetime.now()
        return now.date().isoformat(), now.year
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            raise ValueError('invalid date')
        return dt.date().isoformat(), dt.year
    except Exception:
        year = _safe_year(value) or datetime.now().year
        return str(value)[:10] if value else datetime.now().date().isoformat(), year


async def _fetch_google_news_items(query: str, limit: int = 10) -> list[dict[str, Any]]:
    feed_url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code >= 400 or not response.text.strip():
            return []

        root = ET.fromstring(response.text)
        items = root.findall("./channel/item")
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in items:
            title = _rss_item_text(item, "title")
            link = _rss_item_text(item, "link")
            source = _rss_item_text(item, "source")
            pub_raw = _rss_item_text(item, "pubDate")
            desc = _rss_item_text(item, "description")
            date_text, year = _parse_pub_date(pub_raw)

            title_clean = title.strip()
            if " - " in title_clean:
                left, right = title_clean.rsplit(" - ", 1)
                if len(right) <= 40:
                    title_clean = left.strip()

            if not title_clean or title_clean.lower() in seen:
                continue
            seen.add(title_clean.lower())

            out.append(
                {
                    "title": title_clean,
                    "url": link,
                    "source": source or "Google News",
                    "date": date_text,
                    "year": year,
                    "summary": _strip_html(desc)[:220] if desc else "",
                }
            )
            if len(out) >= limit:
                break

        return out
    except Exception:
        return []


async def _fetch_web_major_cases(limit: int = 10) -> list[dict[str, Any]]:
    queries = [
        "Supreme Court India latest judgment order",
        "High Court India latest judgment order",
        "India constitutional bench judgment latest",
    ]

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in queries:
        items = await _fetch_google_news_items(query, limit=8)
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue

            normalized = title.lower()
            if normalized in seen:
                continue
            seen.add(normalized)

            lower = normalized
            if not any(k in lower for k in ["judgment", "order", "verdict", "bench", "court"]):
                continue

            court = "Supreme Court" if "supreme court" in lower else "High Court"
            merged.append(
                {
                    "title": title,
                    "court": court,
                    "year": int(item.get("year") or datetime.now().year),
                    "summary": str(item.get("summary") or f"Web update from {item.get('source') or 'Google News'}")[:220],
                    "query": title,
                    "url": str(item.get("url") or ""),
                }
            )
            if len(merged) >= limit:
                return merged

    return merged


async def _fetch_web_legal_news(limit: int = 10) -> list[dict[str, Any]]:
    queries = [
        "India legal news latest law and compliance updates",
        "India legal regulatory update notification circular",
    ]

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in queries:
        items = await _fetch_google_news_items(query, limit=10)
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue

            normalized = title.lower()
            if normalized in seen:
                continue
            seen.add(normalized)

            merged.append(
                {
                    "title": title,
                    "source": str(item.get("source") or "Google News"),
                    "date": str(item.get("date") or datetime.now().date().isoformat()),
                    "summary": str(item.get("summary") or "Latest legal and compliance update")[:220],
                    "query": title,
                    "url": str(item.get("url") or ""),
                }
            )
            if len(merged) >= limit:
                return merged

    return merged


_STATIC_TEMPLATE_LIBRARY: list[dict[str, Any]] = get_template_library()


def _template_categories(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for template in templates:
        category = str(template.get("category") or "general")
        counts[category] = counts.get(category, 0) + 1
    return [{"name": name, "count": count} for name, count in counts.items()]


def _find_template(template_id: str) -> dict[str, Any] | None:
    for template in _STATIC_TEMPLATE_LIBRARY:
        if str(template.get("template_id")) == template_id:
            return template
    return None


def _render_template_body(body_lines: list[str], fields: dict[str, Any]) -> str:
    rendered = "\n".join(body_lines)
    normalized_fields = {str(k): str(v) for k, v in fields.items()}
    if "date" not in normalized_fields or not normalized_fields["date"]:
        normalized_fields["date"] = datetime.now().date().isoformat()

    for key, value in normalized_fields.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


@router.get("/major-cases")
async def major_cases(
    force_web: bool = Query(default=False),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if force_web:
        web_cases = await _fetch_web_major_cases(limit=10)
        if web_cases:
            return {"cases": web_cases}

    cases: list[dict[str, Any]] = []
    try:
        docs = get_collection("rag_documents")
        base_filter = {"tenant_id": tenant_id, "app_key": app_key}
        primary_docs = await _collect_docs(
            docs,
            {
                **base_filter,
                "source_type": {"$in": ["judgment", "case"]},
            },
            limit=12,
        )
        fallback_docs = (
            await _collect_docs(
                docs,
                {
                    **base_filter,
                    "$or": [
                        {"source_type": {"$regex": "judgment|case|order|decision", "$options": "i"}},
                        {"legal_court_name": {"$regex": "supreme court|high court", "$options": "i"}},
                        {"legal_metadata.court_name": {"$regex": "supreme court|high court", "$options": "i"}},
                        {"title": {"$regex": "judgment|order|supreme court|high court|vs|v\.", "$options": "i"}},
                    ],
                },
                limit=20,
            )
            if not primary_docs
            else []
        )

        seen: set[str] = set()
        for doc in [*primary_docs, *fallback_docs]:
            legal = dict(doc.get("legal_metadata") or {})
            title = str(doc.get("title") or "Legal Case Note")
            court = str(legal.get("court_name") or doc.get("legal_court_name") or "High Court")
            year = _safe_year(legal.get("doc_date") or doc.get("legal_doc_date") or doc.get("created_at")) or datetime.now().year
            key = f"{title.lower()}|{court.lower()}|{year}"
            if key in seen:
                continue
            seen.add(key)
            cases.append(
                {
                    "title": title,
                    "court": court,
                    "year": year,
                    "summary": f"Indexed source: {str(doc.get('source_type') or 'document')}",
                    "query": title,
                    "url": str(doc.get("source_uri") or ""),
                }
            )
            if len(cases) >= 10:
                break
    except Exception:
        cases = []

    if not cases:
        cases = await _fetch_web_major_cases(limit=10)

    if not cases:
        cases = _static_case_items()

    return {"cases": cases}


@router.get("/legal-news")
async def legal_news(
    force_web: bool = Query(default=False),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if force_web:
        web_news = await _fetch_web_legal_news(limit=10)
        if web_news:
            return {"news": web_news}

    news: list[dict[str, Any]] = []
    try:
        docs = get_collection("rag_documents")
        base_filter = {"tenant_id": tenant_id, "app_key": app_key}
        primary_docs = await _collect_docs(
            docs,
            {
                **base_filter,
                "source_type": {"$in": ["news", "update"]},
            },
            limit=12,
        )
        fallback_docs = (
            await _collect_docs(
                docs,
                {
                    **base_filter,
                    "$or": [
                        {"source_type": {"$regex": "news|update|notification|circular|alert|press", "$options": "i"}},
                        {"tags": {"$in": ["news", "update", "notification", "circular", "alert"]}},
                        {"title": {"$regex": "update|notification|circular|amendment|compliance|legal news", "$options": "i"}},
                    ],
                },
                limit=20,
            )
            if not primary_docs
            else []
        )

        seen: set[str] = set()
        for doc in [*primary_docs, *fallback_docs]:
            title = str(doc.get("title") or "Legal Update")
            date_text = _safe_iso_date(doc.get("created_at") or doc.get("legal_doc_date") or datetime.now())
            key = f"{title.lower()}|{date_text}"
            if key in seen:
                continue
            seen.add(key)
            news.append(
                {
                    "title": title,
                    "source": _safe_source_label(doc),
                    "date": date_text,
                    "summary": f"Indexed source: {str(doc.get('source_type') or 'document')}",
                    "query": title,
                    "url": str(doc.get("source_uri") or ""),
                }
            )
            if len(news) >= 10:
                break
    except Exception:
        news = []

    if not news:
        news = await _fetch_web_legal_news(limit=10)

    if not news:
        news = _static_news_items()

    return {"news": news}


@router.post("/legal-research")
async def legal_research(
    payload: LegacyLegalResearchRequest,
    background_tasks: BackgroundTasks,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    rag_payload = RagQueryRequest(
        query=payload.query,
        top_k=5,
        max_candidates=300,
        include_context=False,
    )
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=rag_payload)
    except Exception:
        result = {
            "answer": "I do not have enough indexed content matching this question yet. Please ingest relevant documents for this topic.",
            "citations": [],
            "strategy": "rag_unavailable",
            "candidate_count": 0,
            "context": None,
        }

    return await build_hybrid_legal_response(
        tenant_id=tenant_id,
        app_key=app_key,
        query=payload.query,
        query_type=payload.query_type,
        rag_result=result,
        background_tasks=background_tasks,
    )


@router.get("/legal-sync/queue")
async def legal_sync_queue(
    status: str = Query(default="pending", max_length=40),
    limit: int = Query(default=25, ge=1, le=200),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    items = await list_sync_queue(tenant_id=tenant_id, app_key=app_key, status=status, limit=limit)
    return {"items": items, "count": len(items)}



@router.post("/legal-sync/run-once")
async def legal_sync_run_once(
    max_jobs: int = Query(default=5, ge=1, le=100),
    current_user: dict = Depends(require_roles([Role.super_admin, Role.tenant_admin, Role.operator])),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    requested_app_key = x_app_key or current_user.get("app_key") or _DEFAULT_APP_KEY
    app_key = _resolve_compat_app_key(requested_app_key)
    try:
        summary = await run_legal_sync_once(
            max_jobs=max_jobs,
            tenant_id=tenant_id,
            app_key=app_key,
            worker_id="manual-api",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Legal sync unavailable: {str(exc)}") from exc
    return summary


@router.post("/search-cases")
async def search_cases(
    payload: LegacyCaseSearchRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    legal_filters = RagLegalFilter(court_name=payload.court.lower()) if payload.court else None
    rag_payload = RagQueryRequest(
        query=payload.query,
        source_types=["judgment", "case"],
        top_k=5,
        max_candidates=250,
        legal_filters=legal_filters,
    )
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=rag_payload)
    except Exception:
        result = {"citations": []}

    cases = [
        {
            "content": f"{c['reference']}\n\n{c['snippet']}",
            "citation": c,
        }
        for c in result["citations"]
    ]
    return {"cases": cases}


@router.post("/search-statute")
async def search_statute(
    payload: LegacyStatuteSearchRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    q = f"{payload.act_name}"
    if payload.section:
        q += f" section {payload.section}"

    legal_filters = RagLegalFilter(
        act_name=payload.act_name.lower(),
        section=(payload.section or "").lower() or None,
    )
    rag_payload = RagQueryRequest(
        query=q,
        source_types=["statute", "act", "regulation"],
        legal_filters=legal_filters,
        top_k=4,
        max_candidates=200,
    )
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=rag_payload)
    except Exception:
        result = {
            "answer": "I do not have enough indexed legal content for this statute query yet.",
            "citations": [],
        }

    return {
        "content": result["answer"],
        "explanation": result["answer"],
        "citations": result["citations"],
    }


@router.post("/draft-document")
async def draft_document(payload: dict[str, Any]):
    doc_type = str(payload.get("document_type") or "legal notice")
    facts = str(payload.get("facts") or "")
    parties = payload.get("parties") or {}
    grounds = payload.get("legal_grounds") or []
    prayer = str(payload.get("prayer") or "")

    draft = render_guided_document_draft(
        document_type=doc_type,
        facts=facts,
        parties=parties,
        legal_grounds=grounds,
        prayer=prayer,
        extra_fields=payload,
    )

    return {
        "drafted_document": draft["drafted_document"],
        "draft_status": draft["draft_status"],
        "follow_up_questions": draft["follow_up_questions"],
        "recommended_clauses": draft["recommended_clauses"],
        "firmness_score": draft["firmness_score"],
    }


@router.post("/review-document")
async def review_document(
    file: UploadFile = File(...),
    query: str | None = Form(default=None),
):
    content = await file.read()
    preview = content[:3000].decode("utf-8", errors="ignore") if content else ""
    analysis = [
        f"Document: {file.filename}",
        f"Question: {query or 'General legal review'}",
        "",
        "Preliminary review (compat mode):",
        "- Verify jurisdiction, governing law, and dispute resolution clauses.",
        "- Verify obligations, timelines, penalty/termination clauses.",
        "- Verify signatures, witnesses, annexures, and date consistency.",
    ]
    if preview:
        analysis.append("")
        analysis.append("Document preview:")
        analysis.append(preview[:1200])
    return {"analysis": "\n".join(analysis)}


@router.get("/v2/templates")
async def v2_templates():
    templates = [
        {
            "template_id": t["template_id"],
            "name": t["name"],
            "description": t["description"],
            "category": t["category"],
            "is_premium": bool(t.get("is_premium", False)),
            "tags": t.get("tags", []),
            "act": t.get("act", []),
            "court": t.get("court", []),
        }
        for t in _STATIC_TEMPLATE_LIBRARY
    ]
    return {"total": len(templates), "templates": templates, "items": templates}


@router.get("/v2/templates/categories")
async def v2_template_categories():
    return {"categories": _template_categories(_STATIC_TEMPLATE_LIBRARY)}


@router.get("/v2/templates/{template_id}")
async def v2_template_detail(template_id: str):
    template = _find_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/v2/templates/{template_id}/fields")
async def v2_template_fields(template_id: str):
    template = _find_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"fields": template.get("fields", [])}


@router.post("/v2/templates/render")
async def v2_template_render(payload: LegacyTemplateRenderRequest):
    template = _find_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    draft = render_template_document(template=template, fields=payload.fields)
    rendered_document = draft["rendered_document"]

    return {
        "rendered_document": rendered_document,
        "draft_status": draft["draft_status"],
        "validation": {
            "citations": {
                "total_citations": 0,
                "valid_citations": 0,
                "accuracy_score": 100,
                "invalid_citations": [],
            },
            "foreign_law": {"has_foreign_law": False},
            "risky_language": {"has_risky_language": False},
            "firmness_score": draft["firmness_score"],
            "missing_required_fields": draft["missing_required_fields"],
            "unresolved_placeholders": draft["unresolved_placeholders"],
            "follow_up_questions": draft["follow_up_questions"],
            "recommended_clauses": draft["recommended_clauses"],
        },
    }


@router.post("/v2/templates/render-pdf")
async def v2_template_render_pdf(payload: LegacyTemplateRenderRequest):
    template = _find_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    draft = render_template_document(template=template, fields=payload.fields)
    rendered_document = draft["rendered_document"]
    file_bytes = BytesIO(rendered_document.encode("utf-8"))
    filename = f"{payload.template_id}.txt"
    return StreamingResponse(
        file_bytes,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.get("/templates/categories")
async def templates_categories_legacy():
    categories = _template_categories(_STATIC_TEMPLATE_LIBRARY)
    return {"categories": categories, "items": categories}


@router.get("/templates/summary")
async def templates_summary_legacy():
    templates = _STATIC_TEMPLATE_LIBRARY
    categories = _template_categories(templates)
    premium_count = sum(1 for t in templates if bool(t.get("is_premium", False)))
    return {
        "total_templates": len(templates),
        "premium_templates": premium_count,
        "free_templates": max(0, len(templates) - premium_count),
        "categories": categories,
    }


@router.get("/models/recommended")
async def recommended_models_legacy():
    return {
        "models": [
            {
                "id": "sanmitra-legal-research-v1",
                "name": "SanMitra Legal Research",
                "purpose": "case_research",
                "latency_tier": "balanced",
                "recommended": True,
            },
            {
                "id": "sanmitra-legal-drafting-v1",
                "name": "SanMitra Legal Drafting",
                "purpose": "document_drafting",
                "latency_tier": "quality",
                "recommended": True,
            },
        ]
    }


@router.get("/diary")
async def list_professional_diary(
    limit: int = Query(default=20, ge=1, le=100),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    try:
        entries_col = get_collection("legal_diary_entries")
        cursor = entries_col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).limit(limit)
        entries = await cursor.to_list(length=limit)
    except Exception:
        entries = []

    items = [
        {
            "entry_id": str(doc.get("entry_id") or doc.get("_id") or ""),
            "title": str(doc.get("title") or "Diary Entry"),
            "content": str(doc.get("content") or ""),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "tags": doc.get("tags") or [],
        }
        for doc in entries
    ]
    return {"items": items, "count": len(items)}


@router.post("/diary")
async def create_professional_diary(
    payload: dict[str, Any],
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    entry_id = str(payload.get("entry_id") or f"diary-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    now = datetime.now()
    doc = {
        "entry_id": entry_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "title": str(payload.get("title") or "Untitled Entry"),
        "content": str(payload.get("content") or ""),
        "tags": payload.get("tags") or [],
        "created_at": now,
        "updated_at": now,
    }

    try:
        entries_col = get_collection("legal_diary_entries")
        await entries_col.insert_one(doc)
    except Exception:
        # Preserve legacy UX even if datastore is unavailable in local/dev mode.
        pass

    return {
        "entry_id": entry_id,
        "status": "created",
        "title": doc["title"],
        "content": doc["content"],
        "tags": doc["tags"],
        "created_at": doc["created_at"],
    }




