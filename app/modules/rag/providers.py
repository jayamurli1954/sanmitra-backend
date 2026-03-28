import asyncio
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import get_settings

_WORD_RE = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    name: str
    model_name: str

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def _hash_embedding(tokens: list[str], size: int) -> list[float]:
    if not tokens:
        return [0.0] * size

    vec = [0.0] * size
    for token in tokens:
        idx = hash(token) % size
        vec[idx] += 1.0

    return _l2_normalize(vec)


@dataclass
class HashEmbeddingProvider:
    dimension: int
    name: str = "hash"
    model_name: str = "hash-v2"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(_tokenize(text), self.dimension) for text in texts]


class SentenceTransformerEmbeddingProvider:
    name = "sentence_transformers"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise RuntimeError(
                "sentence-transformers provider selected but package/model is unavailable"
            ) from exc

        self._model = SentenceTransformer(self.model_name)
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()

        def _encode() -> list[list[float]]:
            vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            if hasattr(vectors, "tolist"):
                return vectors.tolist()
            return [list(v) for v in vectors]

        return await asyncio.to_thread(_encode)


class OpenAICompatibleEmbeddingProvider:
    name = "openai"

    def __init__(self, *, base_url: str, api_key: str, model_name: str) -> None:
        self.model_name = model_name
        self._base_url = base_url
        self._api_key = api_key

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self._base_url or not self._api_key:
            raise RuntimeError("openai provider requires RAG_EMBEDDING_OPENAI_URL and RAG_EMBEDDING_OPENAI_API_KEY")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "input": texts,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self._base_url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()

        data = list(body.get("data") or [])
        if not data:
            raise RuntimeError("embedding provider returned empty data")

        data.sort(key=lambda item: item.get("index", 0))
        vectors: list[list[float]] = []
        for item in data:
            vector = [float(v) for v in item.get("embedding", [])]
            vectors.append(_l2_normalize(vector))

        if len(vectors) != len(texts):
            raise RuntimeError("embedding provider response size mismatch")

        return vectors


class GeminiEmbeddingProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        api_base: str,
        output_dimensionality: int,
        task_type: str,
        max_parallel: int = 6,
    ) -> None:
        self.model_name = model_name
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._output_dimensionality = output_dimensionality
        self._task_type = task_type
        self._max_parallel = max(1, max_parallel)

    async def _embed_one(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, text: str) -> list[float]:
        if not self._api_key:
            raise RuntimeError("gemini provider requires GEMINI_API_KEY")

        if not text.strip():
            return [0.0] * self._output_dimensionality

        payload = {
            "model": f"models/{self.model_name}",
            "content": {"parts": [{"text": text}]},
            "task_type": self._task_type,
            "output_dimensionality": self._output_dimensionality,
        }

        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        url = f"{self._api_base}/models/{self.model_name}:embedContent"

        async with semaphore:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            detail = _truncate_detail(response.text, 500)
            raise RuntimeError(f"gemini embedding error {response.status_code}: {detail}")

        body = response.json()

        # REST may return either `embedding.values` or `embeddings[0].values`
        if isinstance(body.get("embedding"), dict):
            values = body["embedding"].get("values") or []
        else:
            embeddings = list(body.get("embeddings") or [])
            first = embeddings[0] if embeddings else {}
            if isinstance(first.get("values"), list):
                values = first.get("values") or []
            elif isinstance(first.get("embedding"), dict):
                values = first.get("embedding", {}).get("values") or []
            else:
                values = []

        vector = [float(v) for v in values]
        if not vector:
            raise RuntimeError("gemini embedding response did not contain vector values")

        return _l2_normalize(vector)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        semaphore = asyncio.Semaphore(self._max_parallel)
        async with httpx.AsyncClient(timeout=30) as client:
            tasks = [self._embed_one(client, semaphore, text) for text in texts]
            return await asyncio.gather(*tasks)


def _truncate_detail(text: str, max_len: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider = settings.RAG_EMBEDDING_PROVIDER

    if provider == "gemini":
        return GeminiEmbeddingProvider(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.RAG_GEMINI_EMBED_MODEL,
            api_base=settings.RAG_GEMINI_API_BASE,
            output_dimensionality=settings.RAG_GEMINI_EMBED_DIM,
            task_type=settings.RAG_GEMINI_TASK_TYPE,
        )

    if provider == "sentence_transformers":
        return SentenceTransformerEmbeddingProvider(settings.RAG_EMBEDDING_ST_MODEL)

    if provider == "openai":
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.RAG_EMBEDDING_OPENAI_URL,
            api_key=settings.RAG_EMBEDDING_OPENAI_API_KEY,
            model_name=settings.RAG_EMBEDDING_OPENAI_MODEL,
        )

    return HashEmbeddingProvider(dimension=settings.RAG_EMBEDDING_HASH_DIM)


def get_embedding_strategy_name() -> str:
    provider = get_embedding_provider()
    return f"hybrid_{provider.name}_{provider.model_name}_v2"
