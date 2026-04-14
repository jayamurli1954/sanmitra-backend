"""
Legal Web Search Service — Tavily Integration

Provides live web search for Indian legal news, judgements, and amendments.
Designed to enrich LegalMitra's RAG pipeline with real-time legal information.
"""

import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from tavily import TavilyClient

from app.config import Settings

logger = logging.getLogger(__name__)

# India-specific legal domains for filtered search
INDIAN_LEGAL_DOMAINS = [
    "sci.gov.in",           # Supreme Court of India
    "indiankanoon.org",     # Indian Kanoon - case law database
    "barandbench.com",      # Bar & Bench - legal news & analysis
    "livelaw.in",           # LiveLaw - legal news
    "scobserver.in",        # SC Observer - Supreme Court coverage
    "lawmin.gov.in",        # Ministry of Law & Justice
    "indiacode.nic.in",     # India Code - Acts & Rules
]


class LegalWebSearchService:
    """Tavily-based web search for Indian legal content."""

    def __init__(self):
        """Initialize Tavily client with API key from config."""
        self.enabled = Settings.ENABLE_WEB_SEARCH
        self.timeout = Settings.WEB_SEARCH_TIMEOUT_SECONDS
        self.api_key = Settings.TAVILY_API_KEY

        if self.enabled and not self.api_key:
            logger.warning("Web search enabled but TAVILY_API_KEY not configured. Web search will be unavailable.")
            self.enabled = False

        self.client = TavilyClient(api_key=self.api_key) if self.api_key else None

    @staticmethod
    def _string_similarity(a: str, b: str) -> float:
        """Calculate similarity between two strings (0.0 to 1.0)."""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _deduplicate_results(self, results: list[dict], similarity_threshold: float = 0.50) -> list[dict]:
        """
        Deduplicate results by grouping similar titles and consolidating sources.
        Uses transitive clustering: if A~B and B~C, then A, B, C are all in the same group.

        Args:
            results: List of result dictionaries with 'title' and 'url' keys
            similarity_threshold: Minimum similarity (0-1) to consider as duplicates (default 0.50 = ~50% match)

        Returns:
            List of deduplicated results with 'alternative_sources' field added
        """
        if not results:
            return results

        n = len(results)

        # Build similarity matrix and clusters using union-find
        clusters = {i: {i} for i in range(n)}  # Each item starts in its own cluster

        # Find all similar pairs and merge their clusters
        for i in range(n):
            for j in range(i + 1, n):
                similarity = self._string_similarity(
                    results[i].get("title", ""),
                    results[j].get("title", "")
                )

                if similarity >= similarity_threshold:
                    # Merge clusters containing i and j
                    cluster_i = clusters[i]
                    cluster_j = clusters[j]

                    if cluster_i is not cluster_j:
                        # Merge j's cluster into i's cluster
                        merged = cluster_i | cluster_j
                        for member in merged:
                            clusters[member] = merged

        # Group results by cluster and create deduplicated list
        seen_clusters = set()
        deduplicated = []

        for i in range(n):
            cluster_id = id(clusters[i])  # Use object identity as cluster ID
            if cluster_id in seen_clusters:
                continue

            seen_clusters.add(cluster_id)
            cluster_indices = sorted(clusters[i])

            # Use first result as primary
            primary_idx = cluster_indices[0]
            primary_result = results[primary_idx].copy()
            primary_result["alternative_sources"] = []

            # Add other results in cluster as alternative sources
            for other_idx in cluster_indices[1:]:
                other_result = results[other_idx]
                similarity = self._string_similarity(
                    primary_result.get("title", ""),
                    other_result.get("title", "")
                )
                primary_result["alternative_sources"].append({
                    "url": other_result.get("url", ""),
                    "source": other_result.get("source", "unknown"),
                    "similarity": round(similarity, 2)
                })

            deduplicated.append(primary_result)

        return deduplicated

    def search_legal_news(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """
        Search for latest Indian legal news.

        Args:
            query: Search query (e.g., "GST amendment 2026", "tenant eviction SC judgement")
            max_results: Number of results to return (1-10)

        Returns:
            Dictionary with search results, AI summary, and metadata
        """
        if not self.enabled or not self.client:
            return {
                "success": False,
                "error": "Web search unavailable",
                "query": query,
                "results": [],
                "source": "unavailable"
            }

        try:
            # Add India context to query
            india_query = f"{query} India legal news"

            response = self.client.search(
                query=india_query,
                search_depth="advanced",
                max_results=min(max_results, 10),
                include_domains=INDIAN_LEGAL_DOMAINS,
                include_answer=True,
            )

            # Format results
            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "summary": item.get("content", ""),
                    "published_date": item.get("published_date", ""),
                    "source": item.get("url", "").split("/")[2] if item.get("url") else "unknown",
                })

            # Deduplicate results by similar titles
            deduplicated_results = self._deduplicate_results(results)

            return {
                "success": True,
                "query": query,
                "ai_summary": response.get("answer", ""),
                "results": deduplicated_results,
                "total_results": len(deduplicated_results),
                "raw_results_count": len(results),
                "duplicates_removed": len(results) - len(deduplicated_results),
                "source": "tavily_web_search",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "domains_searched": INDIAN_LEGAL_DOMAINS,
            }

        except TimeoutError:
            logger.error(f"Web search timeout for query: {query}")
            return {
                "success": False,
                "error": f"Search timeout after {self.timeout}s",
                "query": query,
                "results": [],
                "source": "error"
            }
        except Exception as e:
            logger.error(f"Web search failed for query '{query}': {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "query": query,
                "results": [],
                "source": "error"
            }

    def search_court_judgements(
        self, query: str, court: str = "Supreme Court", max_results: int = 5
    ) -> dict[str, Any]:
        """
        Search for specific court judgements in India.

        Args:
            query: Legal topic or case name (e.g., "tenant eviction", "GST section 143")
            court: Court level ("Supreme Court", "High Court", "All")
            max_results: Number of results to return

        Returns:
            Dictionary with judgement results and metadata
        """
        if not self.enabled or not self.client:
            return {
                "success": False,
                "error": "Web search unavailable",
                "query": query,
                "court": court,
                "judgements": [],
            }

        try:
            # Build court-specific query
            if court == "All":
                full_query = f"court judgement {query} India"
            else:
                full_query = f"{court} of India judgement {query}"

            response = self.client.search(
                query=full_query,
                search_depth="advanced",
                max_results=min(max_results, 10),
                include_domains=INDIAN_LEGAL_DOMAINS,
                include_answer=True,
            )

            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "summary": item.get("content", ""),
                    "published_date": item.get("published_date", ""),
                    "court": court,
                    "source": item.get("url", "").split("/")[2] if item.get("url") else "unknown",
                })

            # Deduplicate results by similar titles
            deduplicated_results = self._deduplicate_results(results)

            return {
                "success": True,
                "query": query,
                "court": court,
                "ai_summary": response.get("answer", ""),
                "judgements": deduplicated_results,
                "total_found": len(deduplicated_results),
                "raw_results_count": len(results),
                "duplicates_removed": len(results) - len(deduplicated_results),
                "source": "tavily_web_search",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        except TimeoutError:
            logger.error(f"Judgement search timeout for query: {query}")
            return {
                "success": False,
                "error": f"Search timeout after {self.timeout}s",
                "query": query,
                "court": court,
                "judgements": [],
            }
        except Exception as e:
            logger.error(f"Judgement search failed for query '{query}': {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "query": query,
                "court": court,
                "judgements": [],
            }

    def enrich_rag_context(self, query: str, max_web_results: int = 3) -> dict[str, Any]:
        """
        Search web and return context formatted for RAG pipeline injection.

        This is used to enrich the RAG context with fresh legal information
        before passing to the LLM for answer generation.

        Args:
            query: User's legal question
            max_web_results: Number of web results to include in context

        Returns:
            Dictionary with formatted context and metadata
        """
        if not self.enabled or not self.client:
            return {
                "context": "",
                "success": False,
                "error": "Web search unavailable",
                "query": query,
                "metadata": {"source": "error"}
            }

        try:
            india_query = f"{query} India legal"

            response = self.client.search(
                query=india_query,
                search_depth="advanced",
                max_results=min(max_web_results, 5),
                include_domains=INDIAN_LEGAL_DOMAINS,
                include_answer=True,
            )

            # Format and deduplicate results first
            raw_results = response.get("results", [])
            formatted_results = []
            for item in raw_results:
                formatted_results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                    "published_date": item.get("published_date", "Unknown"),
                    "source": item.get("url", "").split("/")[2] if item.get("url") else "unknown",
                })

            deduplicated_results = self._deduplicate_results(formatted_results)

            # Build context text for RAG injection
            context_parts = [
                "=== LIVE WEB SEARCH CONTEXT ===",
                f"Query: {query}",
                f"Fetched: {datetime.now(timezone.utc).isoformat()}",
                "",
            ]

            # Add Tavily's AI answer first
            if response.get("answer"):
                context_parts.append(f"AI Summary: {response['answer']}")
                context_parts.append("")

            # Add individual results with proper formatting
            for i, item in enumerate(deduplicated_results, 1):
                context_parts.append(f"Source {i}:")
                context_parts.append(f"  Title: {item.get('title', '')}")
                context_parts.append(f"  URL: {item.get('url', '')}")
                context_parts.append(f"  Date: {item.get('published_date', 'Unknown')}")
                context_parts.append(f"  Content: {item.get('content', '')}")

                # Add alternative sources if available
                if item.get("alternative_sources"):
                    context_parts.append(f"  Also found on:")
                    for alt_source in item["alternative_sources"]:
                        context_parts.append(f"    - {alt_source['source']}: {alt_source['url']}")

                context_parts.append("")

            context_text = "\n".join(context_parts)

            return {
                "context": context_text,
                "success": True,
                "query": query,
                "num_sources": len(deduplicated_results),
                "raw_sources_count": len(raw_results),
                "duplicates_removed": len(raw_results) - len(deduplicated_results),
                "metadata": {
                    "source": "tavily_web_search",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "domains_searched": INDIAN_LEGAL_DOMAINS,
                    "freshness": "real-time"
                }
            }

        except TimeoutError:
            logger.warning(f"Web search timeout for RAG enrichment: {query}")
            return {
                "context": "",
                "success": False,
                "error": f"Search timeout after {self.timeout}s",
                "query": query,
                "metadata": {"source": "timeout"}
            }
        except Exception as e:
            logger.error(f"Web search failed for RAG enrichment '{query}': {e}", exc_info=True)
            return {
                "context": "",
                "success": False,
                "error": str(e),
                "query": query,
                "metadata": {"source": "error"}
            }


# Global instance
legal_web_search = LegalWebSearchService()
