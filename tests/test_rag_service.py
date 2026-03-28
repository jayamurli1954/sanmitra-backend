from app.modules.rag.schemas import RagLegalFilter, RagQueryRequest
from app.modules.rag.service import _build_filter, _chunk_text, _hybrid_score, _tokenize


def test_chunk_text_splits_long_content() -> None:
    text = " ".join(["legal" for _ in range(1500)])
    chunks = _chunk_text(text, chunk_size=500, chunk_overlap=60)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert all(c.strip() for c in chunks)


def test_hybrid_score_prefers_related_chunk() -> None:
    query = "how to file a trust deed amendment"
    query_tokens = set(_tokenize(query))
    # Simple synthetic embedding space for deterministic unit test
    query_embedding = [1.0, 0.0, 0.0]

    related_text = "To file a trust deed amendment, submit the amendment petition before the registrar with supporting trust records."
    unrelated_text = "Temple donation receipts and sevas are listed in the financial dashboard for monthly accounting."

    related_chunk = {
        "text": related_text,
        "token_set": sorted(set(_tokenize(related_text))),
        "embedding": [1.0, 0.0, 0.0],
    }
    unrelated_chunk = {
        "text": unrelated_text,
        "token_set": sorted(set(_tokenize(unrelated_text))),
        "embedding": [0.0, 1.0, 0.0],
    }

    related_score = _hybrid_score(query_embedding, query_tokens, related_chunk)
    unrelated_score = _hybrid_score(query_embedding, query_tokens, unrelated_chunk)

    assert related_score > unrelated_score


def test_build_filter_enforces_tenant_and_app() -> None:
    payload = RagQueryRequest(
        query="trust registration",
        source_types=["judgment", "statute"],
        tags=["civil", "trust"],
        language="en",
    )

    filters = _build_filter(tenant_id="tenant-1", app_key="legalmitra", payload=payload)

    assert filters["tenant_id"] == "tenant-1"
    assert filters["app_key"] == "legalmitra"
    assert filters["language"] == "en"
    assert filters["source_type"] == {"$in": ["judgment", "statute"]}
    assert filters["tags"] == {"$in": ["civil", "trust"]}


def test_build_filter_includes_legal_filters() -> None:
    payload = RagQueryRequest(
        query="section 138 cheque bounce",
        legal_filters=RagLegalFilter(
            jurisdiction="karnataka",
            court_name="high court",
            act_name="negotiable instruments act",
            section="138",
            matter_type="criminal",
            citation_contains="SCC",
        ),
    )

    filters = _build_filter(tenant_id="tenant-1", app_key="legalmitra", payload=payload)

    assert filters["legal_jurisdiction"] == "karnataka"
    assert filters["legal_court_name"] == "high court"
    assert filters["legal_act_name"] == "negotiable instruments act"
    assert filters["legal_section"] == "138"
    assert filters["legal_matter_type"] == "criminal"
    assert filters["legal_citation"] == {"$regex": "scc", "$options": "i"}

