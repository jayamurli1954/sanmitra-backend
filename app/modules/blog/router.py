from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from app.core.config import get_settings
from app.core.permissions.rbac import Role, require_roles
from app.modules.blog.schemas import BlogPostCreate, BlogPostResponse, BlogPostUpdate
from app.modules.blog.service import (
    create_blog_post, get_blog_posts, get_blog_post_by_slug, 
    update_blog_post, delete_blog_post
)

router = APIRouter(prefix="/blog", tags=["Blog"])


def _resolve_public_app_key(app_key: str | None) -> str:
    if app_key and app_key.strip():
        return app_key.strip()
    settings = get_settings()
    default_app_key = getattr(settings, "default_app_key", None) or getattr(settings, "DEFAULT_APP_KEY", None)
    return str(default_app_key or "legalmitra")

@router.get("/posts", response_model=List[BlogPostResponse])
async def list_posts(
    app_key: str | None = Header(default=None, alias="X-App-Key"),
    limit: int = Query(10, ge=1, le=50),
    skip: int = Query(0, ge=0),
):
    """List blog posts for the current app."""
    return await get_blog_posts(_resolve_public_app_key(app_key), only_published=True, limit=limit, skip=skip)

@router.get("/posts/{slug}", response_model=BlogPostResponse)
async def get_post(
    slug: str,
    app_key: str | None = Header(default=None, alias="X-App-Key")
):
    """Get a specific blog post by its slug."""
    post = await get_blog_post_by_slug(_resolve_public_app_key(app_key), slug, only_published=True)
    if not post:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return post

# Admin-only routes
@router.get("/admin/posts", response_model=List[BlogPostResponse])
async def list_admin_posts(
    app_key: str = Header(..., alias="X-App-Key"),
    limit: int = Query(10, ge=1, le=50),
    skip: int = Query(0, ge=0),
    admin_user: dict = Depends(require_roles([Role.super_admin]))
):
    """List all blog posts, including drafts (Super Admin only)."""
    return await get_blog_posts(app_key, only_published=False, limit=limit, skip=skip)

@router.post("/posts", response_model=BlogPostResponse)
async def create_post(
    payload: BlogPostCreate,
    app_key: str = Header(..., alias="X-App-Key"),
    admin_user: dict = Depends(require_roles([Role.super_admin]))
):
    """Create a new blog post (Super Admin only)."""
    return await create_blog_post(app_key, payload)

@router.patch("/posts/{post_id}", response_model=BlogPostResponse)
async def update_post_route(
    post_id: str,
    payload: BlogPostUpdate,
    app_key: str = Header(..., alias="X-App-Key"),
    admin_user: dict = Depends(require_roles([Role.super_admin]))
):
    """Update an existing blog post (Super Admin only)."""
    post = await update_blog_post(app_key, post_id, payload)
    if not post:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return post

@router.delete("/posts/{post_id}")
async def delete_post_route(
    post_id: str,
    app_key: str = Header(..., alias="X-App-Key"),
    admin_user: dict = Depends(require_roles([Role.super_admin]))
):
    """Delete a blog post (Super Admin only)."""
    success = await delete_blog_post(app_key, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return {"message": "Blog post deleted successfully"}
