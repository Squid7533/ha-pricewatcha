"""Provider-neutral web search for `/ask`.

The model cannot browse on its own. DeepSeek exposes server-side search, but Ollama,
LM Studio, Anthropic, OpenAI, OpenRouter and Groq need the results handed to them.
This module fetches those results before the model call:

- **SearXNG** (JSON) when a self-hosted instance is configured,
- **DuckDuckGo HTML** otherwise — free, no key, no setup, but brittle.

Only titles, URLs and snippets are returned. Fetching the pages themselves is left for
a later version; a failed search is an empty list, never an exception that breaks the
Telegram reply.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

SEARCH_TIMEOUT = 8
DDG_URL = "https://html.duckduckgo.com/html/"
# DDG's HTML result markup; class and href order is not guaranteed, so both are
# matched with lookaheads on the same anchor.
_DDG_RESULT_RE = re.compile(
    r'<a\b(?=[^>]*class="[^"]*result__a[^"]*")(?=[^>]*href="([^"]+)")[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a\b(?=[^>]*class="[^"]*result__snippet[^"]*")[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class SearchResult:
    """One web result, already reduced to what the prompt needs."""

    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}

    def to_citation(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title or self.url}


def build_query(product: str | None, question: str | None) -> str:
    """A search phrase from the deal context and the user's question."""
    parts = [str(product or "").strip(), str(question or "").strip()]
    text = re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()
    return text[:220]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html.unescape(fragment))).strip()


def _clean_url(href: str) -> str:
    """Unwrap DuckDuckGo's ``/l/?uddg=…`` redirect into the real URL."""
    value = html.unescape(href)
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/"):
        value = "https://duckduckgo.com" + value
    parsed = urlparse(value)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            value = target
            parsed = urlparse(value)
    # DDG's sponsored results are wrapped in its own /y.js adclick redirect;
    # they are not useful product pages and their URLs are enormous.
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/y.js"):
        return ""
    return value


def parse_duckduckgo(body: str, limit: int = 5) -> list[SearchResult]:
    """Parse DDG's HTML result page; tolerant of markup changes."""
    titles = [
        (_clean_url(match.group(1)), _text(match.group(2)))
        for match in _DDG_RESULT_RE.finditer(body)
    ]
    snippets = [_text(match.group(1)) for match in _DDG_SNIPPET_RE.finditer(body)]

    results: list[SearchResult] = []
    for index, (url, title) in enumerate(titles):
        if not url.startswith("http") or not title:
            continue
        snippet = snippets[index] if index < len(snippets) else ""
        results.append(SearchResult(title=title, url=url, snippet=snippet[:400]))
        if len(results) >= limit:
            break
    return results


def parse_searxng(payload: Any, limit: int = 5) -> list[SearchResult]:
    """Read SearXNG's JSON API."""
    if not isinstance(payload, dict):
        return []
    results: list[SearchResult] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = _text(str(item.get("title") or ""))
        if not url.startswith("http") or not title:
            continue
        snippet = _text(str(item.get("content") or ""))[:400]
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


async def _fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str] | None = None,
) -> str | None:
    try:
        async with session.get(
            url, params=params, headers=headers or {}, timeout=SEARCH_TIMEOUT
        ) as response:
            if response.status != 200:
                _LOGGER.debug("Search %s answered HTTP %s", url, response.status)
                return None
            return await response.text()
    except Exception as err:  # noqa: BLE001 - search is best effort
        _LOGGER.debug("Search %s failed: %s", url, err)
        return None


async def _fetch_duckduckgo(query: str, language: str) -> str | None:
    """Fetch DDG with browser TLS impersonation.

    Plain aiohttp gets a 202 challenge page (the TLS fingerprint gives it away);
    curl_cffi is the same tool the price_watch integration uses for this. Imported
    lazily so the module stays importable when the requirement is not installed.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:  # pragma: no cover - the manifest installs it
        _LOGGER.warning(
            "DuckDuckGo search needs curl_cffi; install the integration requirements"
        )
        return None

    try:
        async with AsyncSession() as client:
            response = await client.get(
                DDG_URL,
                params={"q": query, "kl": "de-de" if language == "de" else "us-en"},
                headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"},
                impersonate="chrome",
                timeout=SEARCH_TIMEOUT,
            )
            if response.status_code != 200:
                _LOGGER.debug("DuckDuckGo answered HTTP %s", response.status_code)
                return None
            return response.text
    except Exception as err:  # noqa: BLE001 - search is best effort
        _LOGGER.debug("DuckDuckGo search failed: %s", err)
        return None


async def search_web(
    session: aiohttp.ClientSession,
    query: str,
    *,
    provider: str = "auto",
    searxng_url: str = "",
    limit: int = 5,
    language: str = "de",
) -> list[SearchResult]:
    """Run one search. Returns an empty list on any failure."""
    query = query.strip()
    if not query or provider == "none":
        return []

    if provider == "searxng":
        base = searxng_url.rstrip("/")
        if not base:
            return []
        body = await _fetch(
            session,
            f"{base}/search",
            params={
                "q": query,
                "format": "json",
                "language": language,
                "safesearch": "0",
            },
            headers={"Accept": "application/json"},
        )
        if not body:
            return []
        try:
            return parse_searxng(json.loads(body), limit)
        except ValueError:
            _LOGGER.debug("SearXNG returned invalid JSON")
            return []

    body = await _fetch_duckduckgo(query, language)
    return parse_duckduckgo(body, limit) if body else []
