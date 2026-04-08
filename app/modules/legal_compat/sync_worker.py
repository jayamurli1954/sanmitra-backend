from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import xml.etree.ElementTree as ET
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import httpx
from pymongo import ReturnDocument

from app.config import get_settings
from app.db.mongo import get_collection, get_mongo_client
from app.modules.legal_compat.service import RAG_SYNC_QUEUE_COLLECTION
from app.modules.rag.schemas import RagIngestRequest, RagLegalMetadata
from app.modules.rag.service import ingest_document

logger = logging.getLogger(__name__)

_WORKER_TASK: asyncio.Task | None = None
_STOP_EVENT: asyncio.Event | None = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _strip_html(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value or "")
    compact = re.sub(r"\s+", " ", no_tags).strip()
    return unescape(compact)


def _rss_item_text(item: ET.Element, tag: str) -> str:
    direct = item.findtext(tag)
    if direct:
        return direct.strip()
    wildcard = item.findtext(f"{{*}}{tag}")
    return (wildcard or "").strip()


def _parse_feed_date(value: str) -> tuple[str, date | None]:
    if not value:
        return _now_utc().date().isoformat(), None

    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            raise ValueError("invalid date")
        return dt.date().isoformat(), dt.date()
    except Exception:
        text = (value or "").strip()
        if len(text) >= 10:
            raw = text[:10]
            try:
                return raw, date.fromisoformat(raw)
            except Exception:
                return raw, None
        return _now_utc().date().isoformat(), None


async def _fetch_google_news_items(*, query: str, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    feed_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=float(timeout_seconds), follow_redirects=True) as client:
            response = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})

        if response.status_code >= 400 or not response.text.strip():
            return []

        root = ET.fromstring(response.text)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in root.findall("./channel/item"):
            title = _rss_item_text(item, "title")
            link = _rss_item_text(item, "link")
            source = _rss_item_text(item, "source")
            desc = _rss_item_text(item, "description")
            pub_raw = _rss_item_text(item, "pubDate")
            date_text, doc_date = _parse_feed_date(pub_raw)

            title_clean = title.strip()
            if " - " in title_clean:
                left, right = title_clean.rsplit(" - ", 1)
                if len(right) <= 40:
                    title_clean = left.strip()

            normalized = title_clean.lower()
            if not title_clean or normalized in seen:
                continue
            seen.add(normalized)

            out.append(
                {
                    "title": title_clean,
                    "url": link,
                    "source": source or "Google News",
                    "summary": _strip_html(desc)[:400],
                    "date": date_text,
                    "doc_date": doc_date,
                }
            )
            if len(out) >= limit:
                break

        return out
    except Exception:
        return []


def _classify_source_type(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if any(k in text for k in ["judgment", "verdict", "bench", "supreme court", "high court", "order"]):
        return "judgment"
    return "news"


def _extract_court_name(title: str, summary: str) -> str | None:
    text = f"{title} {summary}".lower()
    if "supreme court" in text:
        return "Supreme Court"
    if "high court" in text:
        return "High Court"
    return None


def _external_id(*, tenant_id: str, app_key: str, query: str, title: str, source_url: str) -> str:
    seed = f"{tenant_id}|{app_key}|{query.strip().lower()}|{title.strip().lower()}|{(source_url or '').strip().lower()}"
    secret = (get_settings().JWT_SECRET or "sanmitra-sync-dev-secret").encode("utf-8")
    digest = hmac.new(secret, seed.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"legal-sync-{digest[:40]}"


def _build_payload(*, tenant_id: str, app_key: str, query: str, item: dict[str, Any]) -> RagIngestRequest:
    title = str(item.get("title") or "Legal Update").strip()
    source = str(item.get("source") or "Google News").strip()
    source_url = str(item.get("url") or "").strip() or None
    summary = str(item.get("summary") or "").strip()
    date_text = str(item.get("date") or _now_utc().date().isoformat()).strip()
    doc_date = item.get("doc_date") if isinstance(item.get("doc_date"), date) else None

    source_type = _classify_source_type(title, summary)
    court_name = _extract_court_name(title, summary)

    content = (
        f"Title: {title}\n"
        f"Source: {source}\n"
        f"Published: {date_text}\n"
        f"Query Trigger: {query}\n\n"
        f"Summary:\n{summary or 'No summary available.'}\n\n"
        f"Reference URL: {source_url or 'N/A'}"
    ).strip()

    if len(content) < 60:
        content = f"Title: {title}\nSummary: {summary or title}\nReference: {source_url or 'N/A'}"

    legal_metadata = RagLegalMetadata(
        jurisdiction="india",
        court_name=court_name,
        doc_date=doc_date,
        practice_area="general",
        matter_type="case_update" if source_type == "judgment" else "legal_news",
    )

    return RagIngestRequest(
        title=title[:300],
        content=content,
        source_type=source_type,
        source_uri=source_url,
        language="en",
        tags=["legalmitra", "auto-sync", "google-news", source_type],
        external_id=_external_id(
            tenant_id=tenant_id,
            app_key=app_key,
            query=query,
            title=title,
            source_url=source_url or "",
        ),
        legal_metadata=legal_metadata,
        metadata={
            "source": source,
            "origin": "legal_sync_worker",
            "query": query,
            "published_date": date_text,
        },
    )


async def _recover_stale_jobs(lock_timeout_seconds: int) -> int:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    now = _now_utc()
    cutoff = now - timedelta(seconds=max(30, int(lock_timeout_seconds)))
    result = await queue.update_many(
        {
            "status": "processing",
            "locked_at": {"$lt": cutoff},
        },
        {
            "$set": {
                "status": "pending",
                "updated_at": now,
                "last_error": "Recovered stale processing lock",
            },
            "$unset": {"worker_id": "", "locked_at": ""},
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def _claim_pending_job(*, tenant_id: str | None, app_key: str | None, worker_id: str, max_attempts: int) -> dict[str, Any] | None:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    now = _now_utc()

    filters: dict[str, Any] = {
        "status": "pending",
        "attempt_count": {"$lt": max_attempts},
        "$or": [
            {"retry_after": {"$exists": False}},
            {"retry_after": None},
            {"retry_after": {"$lte": now}},
        ],
    }
    if tenant_id:
        filters["tenant_id"] = tenant_id
    if app_key:
        filters["app_key"] = app_key

    return await queue.find_one_and_update(
        filters,
        {
            "$set": {
                "status": "processing",
                "worker_id": worker_id,
                "locked_at": now,
                "updated_at": now,
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def _mark_job_completed(job: dict[str, Any], *, source_count: int, ingested: int, duplicates: int, errors: int) -> None:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    now = _now_utc()
    await queue.update_one(
        {"job_id": job.get("job_id"), "status": "processing"},
        {
            "$set": {
                "status": "completed",
                "updated_at": now,
                "completed_at": now,
                "source_count": int(source_count),
                "ingested_count": int(ingested),
                "duplicate_count": int(duplicates),
                "error_count": int(errors),
                "last_error": None,
            },
            "$unset": {"worker_id": "", "locked_at": "", "retry_after": ""},
        },
    )


async def _mark_job_failed_or_retry(job: dict[str, Any], *, error_message: str, max_attempts: int, retry_delay_seconds: int) -> str:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    now = _now_utc()
    attempts = int(job.get("attempt_count") or 0)

    if attempts >= max_attempts:
        await queue.update_one(
            {"job_id": job.get("job_id"), "status": "processing"},
            {
                "$set": {
                    "status": "failed",
                    "updated_at": now,
                    "failed_at": now,
                    "last_error": (error_message or "unknown error")[:1000],
                },
                "$unset": {"worker_id": "", "locked_at": "", "retry_after": ""},
            },
        )
        return "failed"

    retry_after = now + timedelta(seconds=max(15, int(retry_delay_seconds)))
    await queue.update_one(
        {"job_id": job.get("job_id"), "status": "processing"},
        {
            "$set": {
                "status": "pending",
                "updated_at": now,
                "retry_after": retry_after,
                "last_error": (error_message or "unknown error")[:1000],
            },
            "$unset": {"worker_id": "", "locked_at": ""},
        },
    )
    return "retried"


async def _process_job(job: dict[str, Any], *, timeout_seconds: int, max_sources: int) -> dict[str, Any]:
    query = str(job.get("query") or "").strip()
    if not query:
        raise ValueError("Queue job has empty query")

    tenant_id = str(job.get("tenant_id") or "").strip()
    app_key = str(job.get("app_key") or "").strip()
    if not tenant_id or not app_key:
        raise ValueError("Queue job missing tenant_id or app_key")

    items = await _fetch_google_news_items(query=query, limit=max_sources, timeout_seconds=timeout_seconds)
    if not items:
        fallback_query = f"{query} India legal update"
        items = await _fetch_google_news_items(query=fallback_query, limit=max_sources, timeout_seconds=timeout_seconds)

    ingested = 0
    duplicates = 0
    errors = 0
    first_error: str | None = None

    for item in items:
        payload = _build_payload(tenant_id=tenant_id, app_key=app_key, query=query, item=item)
        try:
            await ingest_document(
                tenant_id=tenant_id,
                app_key=app_key,
                created_by="legal-sync-worker",
                payload=payload,
            )
            ingested += 1
        except ValueError as exc:
            message = str(exc)
            if "external_id already exists" in message:
                duplicates += 1
            else:
                errors += 1
                if not first_error:
                    first_error = message
        except Exception as exc:
            errors += 1
            if not first_error:
                first_error = str(exc)

    return {
        "source_count": len(items),
        "ingested": ingested,
        "duplicates": duplicates,
        "errors": errors,
        "error": first_error,
    }


async def run_legal_sync_once(
    *,
    max_jobs: int | None = None,
    tenant_id: str | None = None,
    app_key: str | None = None,
    worker_id: str = "manual",
) -> dict[str, Any]:
    settings = get_settings()

    batch_size = max(1, min(int(max_jobs or settings.LEGAL_SYNC_WORKER_BATCH_SIZE), 100))
    max_attempts = max(1, int(settings.LEGAL_SYNC_WORKER_MAX_ATTEMPTS))
    timeout_seconds = max(5, int(settings.LEGAL_SYNC_WORKER_HTTP_TIMEOUT_SECONDS))
    max_sources = max(1, min(int(settings.LEGAL_SYNC_WORKER_MAX_SOURCES_PER_JOB), 50))
    poll_seconds = max(10, int(settings.LEGAL_SYNC_WORKER_POLL_SECONDS))

    summary = {
        "worker_id": worker_id,
        "batch_size": batch_size,
        "recovered_stale": await _recover_stale_jobs(settings.LEGAL_SYNC_WORKER_LOCK_TIMEOUT_SECONDS),
        "claimed": 0,
        "completed": 0,
        "retried": 0,
        "failed": 0,
        "ingested": 0,
        "duplicates": 0,
        "errors": 0,
    }

    for _ in range(batch_size):
        job = await _claim_pending_job(
            tenant_id=tenant_id,
            app_key=app_key,
            worker_id=worker_id,
            max_attempts=max_attempts,
        )
        if not job:
            break

        summary["claimed"] += 1

        try:
            outcome = await _process_job(
                job,
                timeout_seconds=timeout_seconds,
                max_sources=max_sources,
            )
        except Exception as exc:
            outcome = {
                "source_count": 0,
                "ingested": 0,
                "duplicates": 0,
                "errors": 1,
                "error": str(exc),
            }

        summary["ingested"] += int(outcome.get("ingested") or 0)
        summary["duplicates"] += int(outcome.get("duplicates") or 0)
        summary["errors"] += int(outcome.get("errors") or 0)

        hard_failure = (outcome.get("ingested") or 0) == 0 and (outcome.get("duplicates") or 0) == 0 and (outcome.get("errors") or 0) > 0
        if hard_failure:
            status = await _mark_job_failed_or_retry(
                job,
                error_message=str(outcome.get("error") or "sync failed"),
                max_attempts=max_attempts,
                retry_delay_seconds=poll_seconds,
            )
            summary[status] += 1
            continue

        await _mark_job_completed(
            job,
            source_count=int(outcome.get("source_count") or 0),
            ingested=int(outcome.get("ingested") or 0),
            duplicates=int(outcome.get("duplicates") or 0),
            errors=int(outcome.get("errors") or 0),
        )
        summary["completed"] += 1

    return summary


async def _worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(10, int(settings.LEGAL_SYNC_WORKER_POLL_SECONDS))
    batch_size = max(1, int(settings.LEGAL_SYNC_WORKER_BATCH_SIZE))
    worker_id = "background"

    while not stop_event.is_set():
        try:
            result = await run_legal_sync_once(max_jobs=batch_size, worker_id=worker_id)
            next_sleep = 1 if int(result.get("claimed") or 0) > 0 else poll_seconds
        except Exception:
            logger.exception("Legal sync worker loop failed")
            next_sleep = poll_seconds

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=next_sleep)
        except asyncio.TimeoutError:
            continue


async def start_legal_sync_worker() -> bool:
    global _WORKER_TASK, _STOP_EVENT

    settings = get_settings()
    if not settings.LEGAL_SYNC_WORKER_ENABLED or not settings.MONGODB_URI:
        return False

    try:
        get_mongo_client()
    except Exception:
        logger.warning("Legal sync worker skipped: MongoDB not initialized")
        return False

    if _WORKER_TASK and not _WORKER_TASK.done():
        return True

    _STOP_EVENT = asyncio.Event()
    _WORKER_TASK = asyncio.create_task(_worker_loop(_STOP_EVENT), name="legal-sync-worker")
    logger.info("Legal sync worker started")
    return True


async def stop_legal_sync_worker() -> None:
    global _WORKER_TASK, _STOP_EVENT

    task = _WORKER_TASK
    stop_event = _STOP_EVENT
    _WORKER_TASK = None
    _STOP_EVENT = None

    if task is None:
        return

    if stop_event is not None:
        stop_event.set()

    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


