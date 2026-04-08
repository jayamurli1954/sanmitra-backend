import math
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.audit.service import log_audit_event
from app.db.mongo import get_collection
from app.modules.rag.providers import get_embedding_provider, get_embedding_strategy_name
from app.modules.rag.schemas import RagIngestRequest, RagQueryRequest

RAG_DOCUMENTS_COLLECTION = "rag_documents"
RAG_CHUNKS_COLLECTION = "rag_chunks"

_WORD_RE = re.compile(r"[a-z0-9]+")
_MIN_SCORE_FOR_ANSWER = 0.16
_COMMON_QUERY_STOPWORDS = {
    "what", "which", "when", "where", "who", "whom", "whose",
    "why", "how", "is", "are", "was", "were", "do", "does",
    "did", "can", "could", "should", "would", "please", "explain",
    "briefly", "about", "tell", "me", "the", "for", "and", "with",
}


async def ensure_rag_indexes() -> None:
    documents = get_collection(RAG_DOCUMENTS_COLLECTION)
    chunks = get_collection(RAG_CHUNKS_COLLECTION)

    await documents.create_index([("tenant_id", 1), ("app_key", 1), ("created_at", -1)])
    await documents.create_index([("tenant_id", 1), ("app_key", 1), ("source_type", 1), ("language", 1)])
    await documents.create_index([("tenant_id", 1), ("app_key", 1), ("legal_jurisdiction", 1), ("legal_court_name", 1)])
    await documents.create_index([("tenant_id", 1), ("app_key", 1), ("legal_act_name", 1), ("legal_section", 1)])
    await documents.create_index(
        [("tenant_id", 1), ("app_key", 1), ("external_id", 1)],
        unique=True,
        partialFilterExpression={"external_id": {"$exists": True}},
    )

    await chunks.create_index([("tenant_id", 1), ("app_key", 1), ("created_at", -1)])
    await chunks.create_index([("tenant_id", 1), ("app_key", 1), ("document_id", 1), ("chunk_index", 1)])
    await chunks.create_index([("tenant_id", 1), ("app_key", 1), ("source_type", 1), ("language", 1)])
    await chunks.create_index([("tenant_id", 1), ("app_key", 1), ("legal_jurisdiction", 1), ("legal_court_name", 1)])
    await chunks.create_index([("tenant_id", 1), ("app_key", 1), ("legal_act_name", 1), ("legal_section", 1)])
    await chunks.create_index("chunk_id", unique=True)


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _meaningful_query_tokens(query_tokens: set[str]) -> set[str]:
    return {token for token in query_tokens if len(token) >= 4 and token not in _COMMON_QUERY_STOPWORDS}


def _has_meaningful_overlap(meaningful_query_tokens: set[str], text: str) -> bool:
    if not meaningful_query_tokens:
        return True
    text_tokens = set(_tokenize(text))
    return bool(meaningful_query_tokens.intersection(text_tokens))


def _dedup_signature(item: dict[str, Any], answer_sentence: str) -> tuple[str, str, str, str]:
    legal = dict(item.get("legal_metadata") or {})
    return (
        str(legal.get("act_name") or "").strip().lower(),
        str(legal.get("section") or "").strip().lower(),
        str(legal.get("court_name") or "").strip().lower(),
        " ".join((answer_sentence or "").split()).lower(),
    )


def _dedup_tokens(text: str) -> set[str]:
    return {token for token in _tokenize(text) if len(token) >= 4 and token not in _COMMON_QUERY_STOPWORDS}


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left.union(right)
    if not union:
        return 0.0
    return len(left.intersection(right)) / len(union)


def _cosine(v1: list[float], v2: list[float]) -> float:
    size = min(len(v1), len(v2))
    if size == 0:
        return 0.0
    return sum(v1[i] * v2[i] for i in range(size))


def _lexical_score(query_tokens: set[str], chunk_tokens: set[str], text: str) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0

    overlap = len(query_tokens.intersection(chunk_tokens)) / max(len(query_tokens), 1)
    contiguous_bonus = 0.0
    query_phrase = " ".join(sorted(query_tokens))
    if query_phrase and query_phrase in (text or "").lower():
        contiguous_bonus = 0.05

    return min(1.0, overlap + contiguous_bonus)


def _hybrid_score(query_embedding: list[float], query_tokens: set[str], chunk: dict[str, Any]) -> float:
    semantic = _cosine(query_embedding, list(chunk.get("embedding") or []))
    lexical = _lexical_score(query_tokens, set(chunk.get("token_set") or []), str(chunk.get("text") or ""))
    score = (0.6 * semantic) + (0.4 * lexical)
    return max(0.0, min(1.0, score))


def _chunk_text(content: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    cleaned = " ".join(content.split())
    if not cleaned:
        return []

    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    length = len(cleaned)

    while start < length:
        end = min(start + chunk_size, length)

        if end < length:
            soft_split_start = min(start + int(chunk_size * 0.65), end)
            split_at = cleaned.rfind(" ", soft_split_start, end)
            if split_at > start:
                end = split_at

        fragment = cleaned[start:end].strip()
        if fragment:
            chunks.append(fragment)

        if end >= length:
            break

        next_start = max(end - chunk_overlap, start + 1)
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


def _clip(text: str, max_len: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 3].rstrip() + "..."


def _extractive_line(text: str, query_tokens: set[str]) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sentences:
        return _clip(text, 220)

    best = ""
    best_score = -1.0
    for sentence in sentences:
        tokens = set(_tokenize(sentence))
        score = len(tokens.intersection(query_tokens))
        if score > best_score:
            best_score = float(score)
            best = sentence

    return _clip(best or text, 220)


def _build_legal_display_and_filter(legal_metadata) -> tuple[dict[str, Any], dict[str, Any]]:
    if legal_metadata is None:
        return {}, {}

    raw = legal_metadata.model_dump(exclude_none=True)
    display: dict[str, Any] = {}
    filtered: dict[str, Any] = {}

    for key, value in raw.items():
        if value is None:
            continue
        if key == "doc_date":
            iso = value.isoformat() if hasattr(value, "isoformat") else str(value)
            display[key] = iso
            filtered["legal_doc_date"] = iso
        else:
            normalized = str(value).strip()
            display[key] = normalized
            filtered[f"legal_{key}"] = normalized.lower()

    return display, filtered


def _build_filter(*, tenant_id: str, app_key: str, payload: RagQueryRequest) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "tenant_id": tenant_id,
        "app_key": app_key,
    }

    if payload.language:
        filters["language"] = payload.language
    if payload.source_types:
        filters["source_type"] = {"$in": payload.source_types}
    if payload.tags:
        filters["tags"] = {"$in": payload.tags}

    legal = payload.legal_filters
    if legal:
        if legal.jurisdiction:
            filters["legal_jurisdiction"] = legal.jurisdiction
        if legal.court_name:
            filters["legal_court_name"] = legal.court_name
        if legal.act_name:
            filters["legal_act_name"] = legal.act_name
        if legal.section:
            filters["legal_section"] = legal.section
        if legal.matter_type:
            filters["legal_matter_type"] = legal.matter_type
        if legal.citation_contains:
            filters["legal_citation"] = {"$regex": re.escape(legal.citation_contains), "$options": "i"}
        if legal.doc_date_from or legal.doc_date_to:
            date_filter: dict[str, Any] = {}
            if legal.doc_date_from:
                date_filter["$gte"] = legal.doc_date_from.isoformat()
            if legal.doc_date_to:
                date_filter["$lte"] = legal.doc_date_to.isoformat()
            filters["legal_doc_date"] = date_filter

    return filters


def _build_reference_label(index: int, item: dict[str, Any]) -> str:
    parts = [f"[{index}]", f"{item.get('source_type', 'document').upper()}", str(item.get("title") or "Untitled")]

    legal = dict(item.get("legal_metadata") or {})
    if legal.get("act_name"):
        parts.append(f"Act: {legal['act_name']}")
    if legal.get("section"):
        parts.append(f"Section: {legal['section']}")
    if legal.get("court_name"):
        parts.append(f"Court: {legal['court_name']}")
    if legal.get("jurisdiction"):
        parts.append(f"Jurisdiction: {legal['jurisdiction']}")
    if legal.get("doc_date"):
        parts.append(f"Date: {legal['doc_date']}")

    return " | ".join(parts)


async def ingest_document(*, tenant_id: str, app_key: str, created_by: str, payload: RagIngestRequest):
    documents = get_collection(RAG_DOCUMENTS_COLLECTION)
    chunks_collection = get_collection(RAG_CHUNKS_COLLECTION)

    if payload.external_id:
        existing = await documents.find_one(
            {
                "tenant_id": tenant_id,
                "app_key": app_key,
                "external_id": payload.external_id,
            }
        )
        if existing:
            raise ValueError("external_id already exists for this tenant and app")

    now = datetime.now(timezone.utc)
    document_id = str(uuid4())

    provider = get_embedding_provider()
    embedding_strategy = get_embedding_strategy_name()

    legal_display, legal_filter_fields = _build_legal_display_and_filter(payload.legal_metadata)

    parts = _chunk_text(payload.content, payload.chunk_size, payload.chunk_overlap)
    embeddings = await provider.embed_texts(parts) if parts else []

    chunk_docs: list[dict[str, Any]] = []
    for idx, part in enumerate(parts):
        tokens = _tokenize(part)
        token_set = sorted(set(tokens))
        chunk_doc = {
            "chunk_id": str(uuid4()),
            "document_id": document_id,
            "tenant_id": tenant_id,
            "app_key": app_key,
            "title": payload.title,
            "source_type": payload.source_type,
            "source_uri": payload.source_uri,
            "language": payload.language,
            "tags": payload.tags,
            "metadata": payload.metadata,
            "legal_metadata": legal_display,
            "doc_version": payload.doc_version,
            "effective_date": payload.effective_date.isoformat() if payload.effective_date else None,
            "chunk_index": idx,
            "text": part,
            "token_count": len(tokens),
            "token_set": token_set,
            "embedding": embeddings[idx] if idx < len(embeddings) else [],
            "embedding_provider": provider.name,
            "embedding_model": provider.model_name,
            "embedding_strategy": embedding_strategy,
            "created_at": now,
        }
        chunk_doc.update(legal_filter_fields)
        chunk_docs.append(chunk_doc)

    document_doc = {
        "document_id": document_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "title": payload.title,
        "source_type": payload.source_type,
        "source_uri": payload.source_uri,
        "language": payload.language,
        "tags": payload.tags,
        "external_id": payload.external_id,
        "doc_version": payload.doc_version,
        "effective_date": payload.effective_date.isoformat() if payload.effective_date else None,
        "metadata": payload.metadata,
        "legal_metadata": legal_display,
        "embedding_provider": provider.name,
        "embedding_model": provider.model_name,
        "embedding_strategy": embedding_strategy,
        "chunk_count": len(chunk_docs),
        "created_by": created_by,
        "created_at": now,
    }
    document_doc.update(legal_filter_fields)

    await documents.insert_one(document_doc)
    if chunk_docs:
        await chunks_collection.insert_many(chunk_docs)

    await log_audit_event(
        tenant_id=tenant_id,
        user_id=created_by,
        product=app_key,
        action="ingest",
        entity_type="rag_document",
        entity_id=document_id,
        old_value=None,
        new_value={
            "title": payload.title,
            "source_type": payload.source_type,
            "chunk_count": len(chunk_docs),
            "embedding_provider": provider.name,
            "embedding_model": provider.model_name,
        },
    )

    return {
        "document_id": document_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "title": payload.title,
        "source_type": payload.source_type,
        "source_uri": payload.source_uri,
        "language": payload.language,
        "tags": payload.tags,
        "legal_metadata": legal_display or None,
        "embedding_provider": provider.name,
        "embedding_model": provider.model_name,
        "chunk_count": len(chunk_docs),
        "created_at": now,
    }


async def list_documents(*, tenant_id: str, app_key: str, limit: int = 50):
    documents = get_collection(RAG_DOCUMENTS_COLLECTION)

    cursor = (
        documents.find({"tenant_id": tenant_id, "app_key": app_key})
        .sort("created_at", -1)
        .limit(limit)
    )

    items: list[dict[str, Any]] = []
    async for doc in cursor:
        items.append(
            {
                "document_id": doc["document_id"],
                "tenant_id": doc["tenant_id"],
                "app_key": doc["app_key"],
                "title": doc["title"],
                "source_type": doc["source_type"],
                "source_uri": doc.get("source_uri"),
                "language": doc.get("language", "en"),
                "tags": list(doc.get("tags") or []),
                "legal_metadata": doc.get("legal_metadata") or None,
                "embedding_provider": str(doc.get("embedding_provider") or "hash"),
                "embedding_model": str(doc.get("embedding_model") or "hash-v2"),
                "chunk_count": int(doc.get("chunk_count") or 0),
                "created_at": doc["created_at"],
            }
        )

    return items


async def query_knowledge(*, tenant_id: str, app_key: str, payload: RagQueryRequest):
    chunks_collection = get_collection(RAG_CHUNKS_COLLECTION)

    query_tokens_list = _tokenize(payload.query)
    query_tokens = set(query_tokens_list)

    provider = get_embedding_provider()
    strategy = get_embedding_strategy_name()
    query_embedding_batch = await provider.embed_texts([payload.query])
    query_embedding = query_embedding_batch[0] if query_embedding_batch else []

    filters = _build_filter(tenant_id=tenant_id, app_key=app_key, payload=payload)

    cursor = chunks_collection.find(filters).sort("created_at", -1).limit(payload.max_candidates)

    scored: list[dict[str, Any]] = []
    async for chunk in cursor:
        score = _hybrid_score(query_embedding, query_tokens, chunk)
        if score <= 0.01:
            continue

        scored.append(
            {
                "score": round(score, 6),
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
                "chunk_index": int(chunk.get("chunk_index") or 0),
                "title": chunk.get("title") or "Untitled",
                "source_type": chunk.get("source_type") or "document",
                "source_uri": chunk.get("source_uri"),
                "language": chunk.get("language") or "en",
                "tags": list(chunk.get("tags") or []),
                "legal_metadata": dict(chunk.get("legal_metadata") or {}),
                "text": str(chunk.get("text") or ""),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)

    if not scored:
        return {
            "answer": "I do not have enough indexed content to answer this question yet. Please ingest relevant documents first.",
            "citations": [],
            "strategy": strategy,
            "candidate_count": 0,
            "context": [] if payload.include_context else None,
        }

    meaningful_tokens = _meaningful_query_tokens(query_tokens)

    deduped: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, str, str, str]] = set()
    for item in scored:
        sentence = item.get("answer_sentence") or _extractive_line(item["text"], query_tokens)
        signature = _dedup_signature(item, sentence)
        sentence_tokens = _dedup_tokens(sentence)

        if signature in seen_signatures:
            continue

        is_near_duplicate = False
        for existing in deduped:
            existing_signature = existing.get("_dedup_signature")
            if not existing_signature:
                continue

            same_act = signature[0] and signature[0] == existing_signature[0]
            same_section = signature[1] and signature[1] == existing_signature[1]
            if not (same_act and same_section):
                continue

            existing_tokens = existing.get("_dedup_tokens") or set()
            similarity = _jaccard_similarity(sentence_tokens, existing_tokens)
            if similarity >= 0.35:
                is_near_duplicate = True
                break

        if is_near_duplicate:
            continue

        seen_signatures.add(signature)
        enriched = dict(item)
        enriched["answer_sentence"] = sentence
        enriched["_dedup_signature"] = signature
        enriched["_dedup_tokens"] = sentence_tokens
        deduped.append(enriched)
        if len(deduped) >= payload.top_k:
            break

    top = deduped
    if not top:
        return {
            "answer": "I do not have enough indexed content to answer this question yet. Please ingest relevant documents first.",
            "citations": [],
            "strategy": strategy,
            "candidate_count": len(scored),
            "context": [] if payload.include_context else None,
        }

    top_score = float(top[0].get("score") or 0.0)
    if top_score < _MIN_SCORE_FOR_ANSWER or not _has_meaningful_overlap(meaningful_tokens, top[0].get("text") or ""):
        return {
            "answer": "I do not have enough indexed content matching this question yet. Please ingest relevant documents for this topic.",
            "citations": [],
            "strategy": strategy,
            "candidate_count": len(scored),
            "context": [] if payload.include_context else None,
        }

    answer_lines = ["Based on the indexed legal knowledge base:"]
    for idx, item in enumerate(top, start=1):
        sentence = item.get("answer_sentence") or _extractive_line(item["text"], query_tokens)
        answer_lines.append(f"{idx}. {sentence} [{idx}]")

    citations = []
    context: list[str] = []
    for idx, item in enumerate(top, start=1):
        reference = _build_reference_label(idx, item)
        citations.append(
            {
                "index": idx,
                "reference": reference,
                "document_id": item["document_id"],
                "chunk_id": item["chunk_id"],
                "chunk_index": item["chunk_index"],
                "title": item["title"],
                "source_type": item["source_type"],
                "source_uri": item["source_uri"],
                "language": item["language"],
                "tags": item["tags"],
                "legal_metadata": item.get("legal_metadata") or None,
                "score": item["score"],
                "snippet": _clip(item["text"], 300),
            }
        )
        if payload.include_context:
            context.append(item["text"])

    return {
        "answer": "\n".join(answer_lines),
        "citations": citations,
        "strategy": strategy,
        "candidate_count": len(scored),
        "context": context if payload.include_context else None,
    }

