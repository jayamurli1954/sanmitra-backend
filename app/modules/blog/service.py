from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4
from app.db.mongo import get_collection
from app.modules.blog.schemas import BlogPostCreate, BlogPostUpdate

BLOG_COLLECTION = "legal_blog_posts"

async def create_blog_post(app_key: str, payload: BlogPostCreate) -> dict:
    collection = get_collection(BLOG_COLLECTION)
    
    post_dict = payload.model_dump()
    post_dict["id"] = str(uuid4())
    post_dict["app_key"] = app_key
    post_dict["created_at"] = datetime.now(timezone.utc)
    post_dict["updated_at"] = datetime.now(timezone.utc)
    
    if post_dict["is_published"]:
        post_dict["published_at"] = datetime.now(timezone.utc)
    else:
        post_dict["published_at"] = None
        
    await collection.insert_one(post_dict)
    return post_dict

async def get_blog_posts(app_key: str, only_published: bool = True, limit: int = 10, skip: int = 0) -> List[dict]:
    collection = get_collection(BLOG_COLLECTION)
    
    query = {"app_key": app_key}
    if only_published:
        query["is_published"] = True
        
    cursor = collection.find(query).sort("published_at" if only_published else "created_at", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)

async def get_blog_post_by_slug(app_key: str, slug: str, only_published: bool = True) -> Optional[dict]:
    collection = get_collection(BLOG_COLLECTION)
    query = {"app_key": app_key, "slug": slug}
    if only_published:
        query["is_published"] = True
    return await collection.find_one(query)

async def update_blog_post(app_key: str, post_id: str, payload: BlogPostUpdate) -> Optional[dict]:
    collection = get_collection(BLOG_COLLECTION)
    
    update_data = payload.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    if "is_published" in update_data:
        if update_data["is_published"]:
            update_data["published_at"] = datetime.now(timezone.utc)
        else:
            update_data["published_at"] = None
            
    result = await collection.find_one_and_update(
        {"app_key": app_key, "id": post_id},
        {"$set": update_data},
        return_document=True
    )
    return result

async def delete_blog_post(app_key: str, post_id: str) -> bool:
    collection = get_collection(BLOG_COLLECTION)
    result = await collection.delete_one({"app_key": app_key, "id": post_id})
    return result.deleted_count > 0
