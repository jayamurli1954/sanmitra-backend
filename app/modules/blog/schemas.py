from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class BlogPostBase(BaseModel):
    title: str = Field(..., example="Understanding Consumer Rights in India")
    slug: str = Field(..., example="understanding-consumer-rights-india")
    summary: str = Field(..., example="A brief overview of the Consumer Protection Act 2019.")
    content: str = Field(..., example="# Introduction\nConsumer rights are...")
    author: str = Field(..., example="LegalMitra Editorial")
    image_url: Optional[str] = Field(None, example="https://images.unsplash.com/photo-1589829545856-d10d557cf95f")
    tags: List[str] = Field(default_factory=list, example=["consumer-law", "legal-awareness"])
    is_published: bool = Field(default=False)

class BlogPostCreate(BlogPostBase):
    pass

class BlogPostUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    image_url: Optional[str] = None
    tags: Optional[List[str]] = None
    is_published: Optional[bool] = None

class BlogPostResponse(BlogPostBase):
    id: str
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True
