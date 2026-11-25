"""Provider-neutral web search for `/ask`.

The model cannot browse on its own. DeepSeek exposes server-side search, but Ollama,
LM Studio, Anthropic, OpenAI, OpenRouter and Groq need the results handed to them.
This module fetches those results before the model call:

- **SearXNG** (JSON) when a self-hosted instance is configured,
- **DuckDuckGo HTML** otherwise — free, no key, no setup, but brittle.

`/ask` gets titles, URLs and snippets. `/deal` additionally fetches the first direct
shop results and reads their Schema.org Product offer, so a web find carries the shop's
confirmed price instead of a snippet guess. A failed search is an empty list, never an
exception that breaks the Telegram reply.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .media import fetch_page_html, fetch_page_offer

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


_PRICE_RE = re.compile(
    r"(\d{1,4}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*(?:€|EUR\b|euro\b)",
    re.IGNORECASE,
)
_COMPARISON_HOSTS = (
    "idealo.",
    "billiger.",
    "geizhals.",
    "preisvergleich.",
    "guenstiger.",
    "google.",
    "chippy.",
    "priceradar.",
    "check24.",
    "sparhandy.",
)
_SHOPS = {
    "mediamarkt.de": "MediaMarkt",
    "saturn.de": "Saturn",
    "amazon.de": "Amazon",
    "otto.de": "Otto",
    "coolblue.de": "Coolblue",
    "notebooksbilliger.de": "notebooksbilliger.de",
    "alternate.de": "Alternate",
    "cyberport.de": "Cyberport",
    "conrad.de": "Conrad",
    "ebay.de": "eBay",
    "backmarket.de": "Back Market",
    "asgoodasnew.de": "Asgoodasnew",
    "rebuy.de": "Rebuy",
    "shifter.at": "Shifter",
    "shifter.de": "Shifter",
}


def parse_price(text: str) -> float | None:
    """First plausible euro amount in a title or snippet."""
    match = _PRICE_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        left, _, right = raw.partition(",")
        raw = left + right if len(right) == 3 else f"{left}.{right}"
    try:
        return float(raw)
    except ValueError:
        return None


def shop_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, name in _SHOPS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "?"


def is_comparison_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(part in host for part in _COMPARISON_HOSTS)


@dataclass(slots=True)
class WebDeal:
    """A cheaper offer found on the open web, not in the catalog."""

    title: str
    url: str
    price: float
    currency: str
    shop: str
    condition: str | None = None
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "price": self.price,
            "currency": self.currency,
            "shop": self.shop,
            "condition": self.condition,
            "snippet": self.snippet,
        }


@dataclass(slots=True)
class ComparisonDeal:
    """Idealo's aggregate new/used price for one product page."""

    title: str
    url: str
    new_price: float | None = None
    new_currency: str = "EUR"
    new_count: int | None = None
    used_price: float | None = None
    used_currency: str = "EUR"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "new_price": self.new_price,
            "new_currency": self.new_currency,
            "new_count": self.new_count,
            "used_price": self.used_price,
            "used_currency": self.used_currency,
        }


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


async def find_web_deals(
    session: aiohttp.ClientSession,
    query: str,
    *,
    max_price: float,
    provider: str = "duckduckgo",
    searxng_url: str = "",
    language: str = "de",
    limit: int = 3,
) -> list[WebDeal]:
    """Search the open web, confirm shop prices and keep plausible offers.

    Search snippets alone rarely contain the price, and comparison sites are not a
    shop. The first two direct results are fetched and their Schema.org Product offer
    is used; a snippet price is only a fallback when the page could not be read.
    """
    search_query = f"{query} kaufen Preis Angebot"
    results = await search_web(
        session,
        search_query,
        provider=provider,
        searxng_url=searxng_url,
        limit=8,
        language=language,
    )
    direct = [result for result in results if not is_comparison_site(result.url)][:3]
    pages = await asyncio.gather(
        *(fetch_page_offer(result.url, query) for result in direct),
        return_exceptions=True,
    )
    minimum = max_price * 0.35
    deals: list[WebDeal] = []
    seen: set[tuple[str, int]] = set()
    for result, page in zip(direct, pages):
        price = None
        currency = "EUR"
        title = result.title
        url = result.url
        condition = None
        if page is not None and not isinstance(page, BaseException):
            price = page.price
            currency = page.currency or "EUR"
            title = page.title or title
            url = page.url or url
            condition = page.condition
        if price is None:
            price = parse_price(f"{result.title} {result.snippet}")
        if price is None or not (minimum <= price <= max_price):
            continue
        shop = shop_from_url(url)
        signature = (shop, int(round(price)))
        if signature in seen:
            continue
        seen.add(signature)
        deals.append(
            WebDeal(
                title=title,
                url=url,
                price=price,
                currency=currency,
                shop=shop,
                condition=condition,
                snippet=result.snippet,
            )
        )
        if len(deals) >= limit:
            break
    deals.sort(key=lambda deal: deal.price)
    return deals


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


_USED_PRICE_RE = re.compile(
    r"B-Ware\s*(?:&amp;|&)\s*Gebraucht.*?ab\s*(?:</span>\s*)?"
    r"<strong>\s*([\d.,]+)\s*€",
    re.IGNORECASE | re.DOTALL,
)


def parse_idealo(body: str, page_url: str) -> ComparisonDeal | None:
    """Read the AggregateOffer and the B-Ware/Gebraucht low price from idealo."""
    title = "idealo"
    new_price = None
    currency = "EUR"
    count = None
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        body,
        re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(html.unescape(match.group(1)).strip())
        except ValueError:
            continue
        for node in _walk_json(data):
            offers = node.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                price = offer.get("lowPrice") or offer.get("price")
                if price is None:
                    continue
                try:
                    price_value = float(str(price).replace(",", "."))
                except ValueError:
                    continue
                if new_price is None or price_value < new_price:
                    new_price = price_value
                    currency = str(offer.get("priceCurrency") or "EUR")
                    if isinstance(node.get("name"), str) and node["name"]:
                        title = node["name"]
                    try:
                        count = int(offer.get("offerCount"))
                    except (TypeError, ValueError):
                        count = None
    used_match = _USED_PRICE_RE.search(body)
    used_price = parse_price(used_match.group(1) + " €") if used_match else None
    if new_price is None and used_price is None:
        return None
    return ComparisonDeal(
        title=title,
        url=page_url,
        new_price=new_price,
        new_currency=currency,
        new_count=count,
        used_price=used_price,
    )


async def find_idealo_comparison(
    session: aiohttp.ClientSession,
    query: str,
    *,
    provider: str = "duckduckgo",
    searxng_url: str = "",
    language: str = "de",
) -> ComparisonDeal | None:
    """Find the idealo product page for a query and read its aggregate prices."""
    results = await search_web(
        session,
        f"{query} idealo",
        provider=provider,
        searxng_url=searxng_url,
        limit=6,
        language=language,
    )
    url = next(
        (
            result.url
            for result in results
            if "idealo." in urlparse(result.url).netloc.lower()
            and "/preisvergleich/" in result.url
        ),
        None,
    )
    if not url:
        return None
    body = await fetch_page_html(url)
    if not body:
        return None
    return parse_idealo(body, url)
