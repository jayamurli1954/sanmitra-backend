from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_tenant_id
from app.modules.legal.schemas import LegalCaseCreateRequest, LegalCaseListResponse, LegalCaseResponse
from app.modules.legal.service import create_legal_case, list_legal_cases
from app.services.legal_web_search import legal_web_search

router = APIRouter(prefix="/legal", tags=["legal"])


@router.post("/cases", response_model=LegalCaseResponse)
async def create_case(
    payload: LegalCaseCreateRequest,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    case = await create_legal_case(
        tenant_id=tenant_id,
        created_by=current_user.get("sub", "system"),
        payload=payload,
    )
    return LegalCaseResponse(**case)


@router.get("/cases", response_model=LegalCaseListResponse)
async def get_cases(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    items = await list_legal_cases(tenant_id=tenant_id, limit=limit)
    return LegalCaseListResponse(items=[LegalCaseResponse(**i) for i in items], count=len(items))


# --- Web Search Endpoints (Tavily Integration) ---

@router.get("/news")
async def get_legal_news(
    query: str = Query(..., description="Legal topic to search (e.g., 'GST amendment 2026')"),
    max_results: int = Query(5, ge=1, le=10, description="Number of results to return"),
):
    """
    Get latest Indian legal news for a given topic.

    Uses Tavily API to fetch real-time legal news from Indian legal sources.

    Example: GET /api/v1/legal/news?query=Supreme Court tenant eviction

    Returns:
    - results: List of news articles with title, URL, summary, date
    - ai_summary: AI-generated summary of top results
    - source: "tavily_web_search"
    - fetched_at: Timestamp when results were fetched
    """
    return await legal_web_search.search_legal_news(query=query, max_results=max_results)


@router.get("/judgements")
async def get_court_judgements(
    query: str = Query(..., description="Legal topic or case name"),
    court: str = Query("Supreme Court", description="Court level: 'Supreme Court', 'High Court', or 'All'"),
    max_results: int = Query(5, ge=1, le=10, description="Number of results to return"),
):
    """
    Search for specific Indian court judgements.

    Uses Tavily API to find relevant court judgements and orders.

    Example: GET /api/v1/legal/judgements?query=tenant eviction rights&court=Supreme Court

    Returns:
    - judgements: List of judgement records with title, URL, summary, court, date
    - ai_summary: AI-generated analysis of the judgements
    - total_found: Number of judgements found
    - source: "tavily_web_search"
    - fetched_at: Timestamp when results were fetched
    """
    return await legal_web_search.search_court_judgements(
        query=query, court=court, max_results=max_results
    )


@router.get("/web-search-rag")
async def get_web_search_context(
    query: str = Query(..., description="User's legal question"),
):
    """
    Get web search context formatted for RAG pipeline enrichment.

    Fetches fresh legal information from web sources and formats it
    to be injected into the RAG system for LLM augmentation.

    Example: GET /api/v1/legal/web-search-rag?query=What are tenant rights in India 2026

    Returns:
    - context: Plain text formatted for RAG injection
    - success: Whether the search was successful
    - num_sources: Number of sources included
    - metadata: Source information and timestamps
    """
    return await legal_web_search.enrich_rag_context(query=query)
