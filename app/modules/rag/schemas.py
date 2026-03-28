from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RagLegalMetadata(BaseModel):
    jurisdiction: str | None = Field(default=None, max_length=120)
    court_name: str | None = Field(default=None, max_length=200)
    bench: str | None = Field(default=None, max_length=160)
    act_name: str | None = Field(default=None, max_length=200)
    section: str | None = Field(default=None, max_length=120)
    citation: str | None = Field(default=None, max_length=200)
    matter_type: str | None = Field(default=None, max_length=120)
    practice_area: str | None = Field(default=None, max_length=120)
    doc_date: date | None = None

    @model_validator(mode="after")
    def normalize(self):
        for field_name in [
            "jurisdiction",
            "court_name",
            "bench",
            "act_name",
            "section",
            "citation",
            "matter_type",
            "practice_area",
        ]:
            value = getattr(self, field_name)
            if value is not None:
                normalized = str(value).strip()
                setattr(self, field_name, normalized or None)
        return self


class RagLegalFilter(BaseModel):
    jurisdiction: str | None = Field(default=None, max_length=120)
    court_name: str | None = Field(default=None, max_length=200)
    act_name: str | None = Field(default=None, max_length=200)
    section: str | None = Field(default=None, max_length=120)
    matter_type: str | None = Field(default=None, max_length=120)
    citation_contains: str | None = Field(default=None, max_length=200)
    doc_date_from: date | None = None
    doc_date_to: date | None = None

    @model_validator(mode="after")
    def normalize(self):
        for field_name in [
            "jurisdiction",
            "court_name",
            "act_name",
            "section",
            "matter_type",
            "citation_contains",
        ]:
            value = getattr(self, field_name)
            if value is not None:
                normalized = str(value).strip().lower()
                setattr(self, field_name, normalized or None)

        if self.doc_date_from and self.doc_date_to and self.doc_date_from > self.doc_date_to:
            raise ValueError("doc_date_from must be <= doc_date_to")
        return self


class RagIngestRequest(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    content: str = Field(min_length=50, max_length=500000)
    source_type: str = Field(default="document", min_length=2, max_length=80)
    source_uri: str | None = Field(default=None, max_length=1200)
    language: str = Field(default="en", min_length=2, max_length=16)
    tags: list[str] = Field(default_factory=list)
    external_id: str | None = Field(default=None, max_length=160)
    doc_version: str | None = Field(default=None, max_length=80)
    effective_date: date | None = None
    legal_metadata: RagLegalMetadata | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunk_size: int = Field(default=1200, ge=300, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=800)

    @model_validator(mode="after")
    def normalize(self):
        self.title = self.title.strip()
        self.content = self.content.strip()
        self.source_type = self.source_type.strip().lower()
        self.source_uri = (self.source_uri or "").strip() or None
        self.language = self.language.strip().lower()
        self.tags = sorted({str(tag).strip().lower() for tag in self.tags if str(tag).strip()})
        self.external_id = (self.external_id or "").strip() or None
        self.doc_version = (self.doc_version or "").strip() or None
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self


class RagDocumentResponse(BaseModel):
    document_id: str
    tenant_id: str
    app_key: str
    title: str
    source_type: str
    source_uri: str | None = None
    language: str
    tags: list[str]
    legal_metadata: RagLegalMetadata | None = None
    embedding_provider: str
    embedding_model: str
    chunk_count: int
    created_at: datetime


class RagDocumentListResponse(BaseModel):
    items: list[RagDocumentResponse]
    count: int


class RagQueryRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    max_candidates: int = Field(default=300, ge=20, le=2000)
    source_types: list[str] | None = None
    tags: list[str] | None = None
    language: str | None = Field(default=None, min_length=2, max_length=16)
    legal_filters: RagLegalFilter | None = None
    include_context: bool = False

    @model_validator(mode="after")
    def normalize(self):
        self.query = self.query.strip()
        if self.source_types is not None:
            self.source_types = sorted({str(v).strip().lower() for v in self.source_types if str(v).strip()})
            if not self.source_types:
                self.source_types = None
        if self.tags is not None:
            self.tags = sorted({str(v).strip().lower() for v in self.tags if str(v).strip()})
            if not self.tags:
                self.tags = None
        if self.language is not None:
            self.language = self.language.strip().lower() or None
        return self


class RagCitation(BaseModel):
    index: int
    reference: str
    document_id: str
    chunk_id: str
    chunk_index: int
    title: str
    source_type: str
    source_uri: str | None
    language: str
    tags: list[str]
    legal_metadata: RagLegalMetadata | None = None
    score: float
    snippet: str


class RagQueryResponse(BaseModel):
    answer: str
    citations: list[RagCitation]
    strategy: str
    candidate_count: int
    context: list[str] | None = None
