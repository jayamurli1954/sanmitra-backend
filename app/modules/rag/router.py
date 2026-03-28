from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.permissions.rbac import Role, require_roles
from app.core.tenants.context import inject_app_key, resolve_tenant_id
from app.modules.rag.schemas import (
    RagDocumentListResponse,
    RagDocumentResponse,
    RagIngestRequest,
    RagQueryRequest,
    RagQueryResponse,
)
from app.modules.rag.service import ingest_document, list_documents, query_knowledge

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/documents", response_model=RagDocumentResponse)
async def ingest_rag_document(
    payload: RagIngestRequest,
    current_user: dict = Depends(
        require_roles([Role.super_admin, Role.tenant_admin, Role.operator])
    ),
    app_key: str = Depends(inject_app_key),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)

    try:
        result = await ingest_document(
            tenant_id=tenant_id,
            app_key=app_key,
            created_by=current_user.get("sub", "system"),
            payload=payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return RagDocumentResponse(**result)


@router.get("/documents", response_model=RagDocumentListResponse)
async def get_rag_documents(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(require_roles([Role.super_admin, Role.tenant_admin, Role.operator, Role.viewer])),
    app_key: str = Depends(inject_app_key),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    items = await list_documents(tenant_id=tenant_id, app_key=app_key, limit=limit)
    return RagDocumentListResponse(items=[RagDocumentResponse(**item) for item in items], count=len(items))


@router.post("/query", response_model=RagQueryResponse)
async def query_rag(
    payload: RagQueryRequest,
    current_user: dict = Depends(require_roles([Role.super_admin, Role.tenant_admin, Role.operator, Role.viewer])),
    app_key: str = Depends(inject_app_key),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RagQueryResponse(**result)
