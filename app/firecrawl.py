"""Firecrawl client (v2 API): clean page scraping, web search and site crawling.

Optional. With FIRECRAWL_API_KEY set, agents read pages through Firecrawl,
which renders JavaScript and returns clean Markdown. Without it, the tools
fall back to the built-in fetcher, DuckDuckGo and a basic crawler.
"""
import logging
import time

import httpx

from .config import settings

log = logging.getLogger("hivemind.firecrawl")

# Firecrawl normally answers in 1-3 seconds. A rare call stalls for close to a
# minute, so give up early and let the caller fall back to the built-in tools.
_http = httpx.Client(timeout=httpx.Timeout(20, connect=5))


class FirecrawlError(Exception):
    """Carries a message that is safe to hand back to the model."""


def configured() -> bool:
    return bool(settings.firecrawl_api_key)


def _request(method: str, path: str, body: dict | None = None) -> dict:
    try:
        r = _http.request(
            method, f"{settings.firecrawl_api_url}/v2{path}", json=body,
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
        )
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Firecrawl %s failed: %s", path, type(e).__name__)
        raise FirecrawlError("Firecrawl did not respond in time.")
    if r.status_code >= 400 or data.get("success") is False:
        reason = str(data.get("error") or f"HTTP {r.status_code}")[:200]
        log.warning("Firecrawl %s error %s: %s", path, r.status_code, reason)
        if r.status_code in (401, 403):
            raise FirecrawlError("Firecrawl rejected the API key.")
        if r.status_code == 402:
            raise FirecrawlError("The Firecrawl account is out of credits.")
        if r.status_code == 429:
            raise FirecrawlError("Firecrawl is rate-limiting requests right now.")
        raise FirecrawlError(f"Firecrawl could not do that: {reason}")
    return data


def scrape(url: str) -> dict:
    """One page as Markdown: {"title", "url", "markdown"}."""
    data = _request("POST", "/scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True}).get("data") or {}
    meta = data.get("metadata") or {}
    return {"title": meta.get("title") or "", "url": meta.get("sourceURL") or url, "markdown": data.get("markdown") or ""}


def search(query: str, limit: int = 5) -> list[dict]:
    """Web results: [{"title", "url", "description"}]."""
    data = _request("POST", "/search", {"query": query, "limit": limit}).get("data") or {}
    hits = data.get("web", []) if isinstance(data, dict) else data  # v1 returned a bare list
    return [{"title": h.get("title") or "", "url": h.get("url") or "", "description": h.get("description") or ""} for h in hits]


def crawl(url: str, limit: int = 5, wait: float = 100) -> list[dict]:
    """Follow links from `url` and return up to `limit` pages as Markdown."""
    job = _request("POST", "/crawl", {
        "url": url, "limit": limit,
        "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
    })
    if not job.get("id"):
        raise FirecrawlError("Firecrawl did not start the crawl.")
    deadline = time.monotonic() + wait
    status: dict = {}
    while time.monotonic() < deadline:
        time.sleep(2)
        status = _request("GET", f"/crawl/{job['id']}")
        if status.get("status") in ("completed", "failed", "cancelled"):
            break
    pages = status.get("data") or []
    if not pages:
        raise FirecrawlError("The crawl failed." if status.get("status") == "failed" else "The crawl found no pages in time.")
    out = []
    for page in pages[:limit]:
        meta = page.get("metadata") or {}
        out.append({"title": meta.get("title") or "", "url": meta.get("sourceURL") or "", "markdown": page.get("markdown") or ""})
    return out
