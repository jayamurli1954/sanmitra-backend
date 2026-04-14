"""
Legal Web Search Service — Tavily Integration

Provides live web search for Indian legal news, judgements, and amendments.
Designed to enrich LegalMitra's RAG pipeline with real-time legal information.
"""

import logging
from datetime import datetime, timezone
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

            return {
                "success": True,
                "query": query,
                "ai_summary": response.get("answer", ""),
                "results": results,
                "total_results": len(results),
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

            return {
                "success": True,
                "query": query,
                "court": court,
                "ai_summary": response.get("answer", ""),
                "judgements": results,
                "total_found": len(results),
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
            for i, item in enumerate(response.get("results", []), 1):
                context_parts.append(f"Source {i}:")
                context_parts.append(f"  Title: {item.get('title', '')}")
                context_parts.append(f"  URL: {item.get('url', '')}")
                context_parts.append(f"  Date: {item.get('published_date', 'Unknown')}")
                context_parts.append(f"  Content: {item.get('content', '')}")
                context_parts.append("")

            context_text = "\n".join(context_parts)

            return {
                "context": context_text,
                "success": True,
                "query": query,
                "num_sources": len(response.get("results", [])),
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
