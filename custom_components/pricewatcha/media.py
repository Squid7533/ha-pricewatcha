"""Product photo, condition and web price from a merchant page (3c/0.6.0).

The catalog has no image field, so the only way to show a photo in Telegram is to
read the merchant page the offer links to. This module fetches that one page (top
deal only), pulls the main image and, if the catalog did not state one, the
Schema.org condition, then downloads the image bytes for the Bot API upload.

DuckDuckGo needed browser impersonation; merchant pages are the same story, so the
fetch reuses `curl_cffi`. A missing image or a blocked shop is normal: the caller
simply sends no photo and keeps the text reply.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

_LOGGER = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 9 * 1024 * 1024
FETCH_TIMEOUT = 10

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_META_IMAGE_RE = re.compile(
    r'<meta\b(?=[^>]*(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\'])'
    r'(?=[^>]*content=["\']([^"\']+)["\'])[^>]*>',
    re.IGNORECASE | re.DOTALL,
)
_CONDITIONS = {
    "newcondition": "new",
    "usedcondition": "used",
    "refurbishedcondition": "refurbished",
    "damagedcondition": "damaged",
}


@dataclass(slots=True)
class PageMedia:
    """What a product page revealed."""

    image_url: str | None = None
    condition: str | None = None
    title: str | None = None


@dataclass(slots=True)
class PageOffer:
    """The product name, price and condition a shop page exposes."""

    title: str
    price: float
    currency: str
    condition: str | None = None
    url: str | None = None


@dataclass(slots=True)
class ProductMedia:
    """Parsed page plus the downloaded image, ready for the Telegram upload."""

    image: bytes | None = None
    content_type: str = "image/jpeg"
    condition: str | None = None
    title: str | None = None
    image_url: str | None = None

    @property
    def suffix(self) -> str:
        if self.content_type == "image/png":
            return ".png"
        if self.content_type == "image/webp":
            return ".webp"
        return ".jpg"


def _image_from_jsonld(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            found = _image_from_jsonld(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key in ("url", "contentUrl", "@id"):
            if isinstance(value.get(key), str):
                return value[key]
    return None


def _condition_from_jsonld(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.rstrip("/").rsplit("/", 1)[-1].lower()
    return _CONDITIONS.get(name)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(" ", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _query_tokens(query: str) -> list[str]:
    stop = {
        "kaufen", "preis", "preise", "angebot", "angebote", "shop", "online",
        "der", "die", "das", "und", "für", "fur", "mit", "bei", "von", "the",
    }
    tokens = []
    for raw in re.split(r"[^0-9a-zäöüß]+", query.casefold().replace("ß", "ss")):
        if len(raw) >= 2 and raw not in stop:
            tokens.append(raw)
    return tokens


def _matches(name: str, tokens: list[str]) -> int:
    normalized = re.sub(r"[^0-9a-zäöüß]+", "", name.casefold().replace("ß", "ss"))
    return sum(1 for token in tokens if token in normalized)


def parse_page_offer(body: str, page_url: str, query: str = "") -> PageOffer | None:
    """Best matching Product offer on a shop page.

    Retailer pages often list every colour and storage size; the query tokens pick
    the variant the user actually asked for instead of the first one in the JSON-LD.
    """
    tokens = _query_tokens(query)
    candidates: list[tuple[int, float, PageOffer]] = []
    for match in _JSONLD_RE.finditer(body):
        raw = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(str(kind).lower() == "product" for kind in types):
                continue
            name = node.get("name")
            if not isinstance(name, str) or not name:
                continue
            offers = node.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                price = _price(offer.get("price") or offer.get("lowPrice"))
                if price is None or price <= 0:
                    continue
                score = _matches(name, tokens)
                if tokens and score < len(tokens):
                    continue
                url = offer.get("url")
                if isinstance(url, str) and url:
                    url = urljoin(page_url, url)
                else:
                    url = page_url
                candidates.append(
                    (
                        score,
                        price,
                        PageOffer(
                            title=name,
                            price=price,
                            currency=str(offer.get("priceCurrency") or "EUR"),
                            condition=_condition_from_jsonld(
                                offer.get("itemCondition")
                            )
                            or _condition_from_jsonld(node.get("itemCondition")),
                            url=url,
                        ),
                    )
                )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


async def fetch_page_offer(
    page_url: str, query: str = "", *, timeout: int = 7
) -> PageOffer | None:
    """Fetch one shop page and read its Schema.org Product offer."""
    if not page_url.startswith("http"):
        return None
    response = await _curl_get(page_url, timeout=timeout)
    if response is None or response.status_code != 200:
        return None
    return parse_page_offer(response.text, page_url, query)


def parse_page_media(body: str, page_url: str) -> PageMedia:
    """Read JSON-LD Product first, then Open Graph/Twitter image tags."""
    media = PageMedia()
    for match in _JSONLD_RE.finditer(body):
        raw = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(str(kind).lower() == "product" for kind in types):
                continue
            if not media.title and isinstance(node.get("name"), str):
                media.title = node["name"]
            if not media.image_url:
                media.image_url = _image_from_jsonld(node.get("image"))
            if not media.condition:
                media.condition = _condition_from_jsonld(node.get("itemCondition"))
            offers = node.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                if not media.condition:
                    media.condition = _condition_from_jsonld(offer.get("itemCondition"))
                if not media.image_url:
                    media.image_url = _image_from_jsonld(offer.get("image"))
            if media.image_url and media.condition:
                break

    if not media.image_url:
        match = _META_IMAGE_RE.search(body)
        if match:
            media.image_url = match.group(1)

    if media.image_url:
        media.image_url = urljoin(page_url, html.unescape(media.image_url))
        if media.image_url.startswith("data:"):
            media.image_url = None
    return media


async def _curl_get(url: str, *, timeout: int = FETCH_TIMEOUT):
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:  # pragma: no cover - the manifest installs it
        return None
    try:
        async with AsyncSession() as client:
            return await client.get(
                url, impersonate="chrome", timeout=timeout, allow_redirects=True
            )
    except Exception as err:  # noqa: BLE001 - a blocked shop is expected
        _LOGGER.debug("Media fetch failed for %s: %s", url, err)
        return None


async def fetch_product_media(page_url: str) -> ProductMedia | None:
    """One page fetch plus one image download. ``None`` when nothing usable came back."""
    if not page_url.startswith("http"):
        return None
    response = await _curl_get(page_url)
    if response is None or response.status_code != 200:
        return None
    parsed = parse_page_media(response.text, page_url)
    if not parsed.image_url:
        return None

    image_response = await _curl_get(parsed.image_url)
    if image_response is None or image_response.status_code != 200:
        return ProductMedia(
            condition=parsed.condition,
            title=parsed.title,
            image_url=parsed.image_url,
        )
    content = image_response.content
    if not content or len(content) > MAX_IMAGE_BYTES:
        return ProductMedia(
            condition=parsed.condition,
            title=parsed.title,
            image_url=parsed.image_url,
        )
    content_type = (image_response.headers.get("content-type") or "image/jpeg").split(";")[0]
    return ProductMedia(
        image=content,
        content_type=content_type if content_type.startswith("image/") else "image/jpeg",
        condition=parsed.condition,
        title=parsed.title,
        image_url=parsed.image_url,
    )
