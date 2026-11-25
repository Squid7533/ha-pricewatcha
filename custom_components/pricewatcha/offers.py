"""Normalized offers across catalog, shop pages and comparison portals (0.8.0).

Every source produces the same ``Offer`` so ranking, deduplication and the Telegram
view no longer need to know where a price came from. A missing shipping cost is never
turned into zero: ``total`` stays ``None`` and the UI says "plus shipping".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

CONDITIONS = ("new", "refurbished", "used", "unknown")

_CONDITION_WORDS: tuple[tuple[str, str], ...] = (
    ("refurb", "refurbished"),
    ("grade", "refurbished"),
    ("very good", "refurbished"),
    ("like new", "refurbished"),
    ("sehr gut", "refurbished"),
    ("wie neu", "refurbished"),
    ("neuwertig", "refurbished"),
    ("good", "refurbished"),
    ("gut", "refurbished"),
    ("gebraucht", "used"),
    ("used", "used"),
    ("new", "new"),
    ("neu", "new"),
)

_TITLE_STOP = {
    "apple",
    "samsung",
    "google",
    "smartphone",
    "handy",
    "mit",
    "ohne",
    "dual",
    "sim",
    "5g",
    "4g",
    "neu",
    "new",
    "refurbished",
    "gebraucht",
    "used",
    "angebot",
    "offer",
}


def normalize_condition(value: str | None) -> str:
    """Map catalog/web condition labels to the four ranking buckets."""
    text = str(value or "").casefold()
    for needle, condition in _CONDITION_WORDS:
        if needle in text:
            return condition
    return "unknown"


def normalize_title(title: str) -> str:
    """A conservative product key from a product title.

    Deliberately keeps storage and colour. Merging different variants is worse than
    showing the same offer twice, so only shop/brand noise is removed.
    """
    text = str(title or "").casefold().replace("ß", "ss")
    text = re.sub(r"[^0-9a-zäöü]+", " ", text)
    tokens = [token for token in text.split() if token and token not in _TITLE_STOP]
    return " ".join(tokens) or text.strip() or "unknown"


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class Offer:
    """One purchasable offer, normalized across sources."""

    source: str
    title: str
    url: str
    shop: str
    price: float | None
    currency: str = "EUR"
    shipping: float | None = None
    total: float | None = None
    condition: str = "unknown"
    availability: str | None = None
    seller: str | None = None
    source_id: str | None = None
    product_key: str = ""
    trackable: bool = False
    verified: bool = True
    observed_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.condition = normalize_condition(self.condition)
        if not self.product_key:
            self.product_key = normalize_title(self.title)
        if not self.observed_at:
            self.observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if self.total is None and self.price is not None and self.shipping is not None:
            self.total = round(float(self.price) + float(self.shipping), 2)

    @property
    def effective_total(self) -> float | None:
        """Total when shipping is known, otherwise the article price."""
        if self.total is not None:
            return self.total
        return self.price

    @property
    def shipping_known(self) -> bool:
        return self.shipping is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "product_key": self.product_key,
            "title": self.title,
            "url": self.url,
            "shop": self.shop,
            "seller": self.seller,
            "price": self.price,
            "shipping": self.shipping,
            "total": self.total,
            "effective_total": self.effective_total,
            "currency": self.currency,
            "condition": self.condition,
            "availability": self.availability,
            "trackable": self.trackable,
            "verified": self.verified,
            "observed_at": self.observed_at,
            "shipping_known": self.shipping_known,
            "extra": self.extra or None,
        }


class OfferSet:
    """Deduplicated offers plus condition-aware selection."""

    def __init__(self, offers: Iterable[Offer] | None = None) -> None:
        self._offers: list[Offer] = []
        for offer in offers or []:
            self.add(offer)

    def add(self, offer: Offer) -> None:
        signature = (
            offer.product_key,
            offer.shop.casefold(),
            offer.condition,
        )
        for index, current in enumerate(self._offers):
            if (
                current.product_key,
                current.shop.casefold(),
                current.condition,
            ) != signature:
                continue
            current_total = current.effective_total
            new_total = offer.effective_total
            if current_total is None or (
                new_total is not None and new_total < current_total
            ):
                self._offers[index] = offer
            return
        self._offers.append(offer)

    def extend(self, offers: Iterable[Offer]) -> None:
        for offer in offers:
            self.add(offer)

    def __len__(self) -> int:
        return len(self._offers)

    def __iter__(self):
        return iter(self._offers)

    def offers(self) -> list[Offer]:
        return list(self._offers)

    def _rank_key(self, offer: Offer) -> tuple[int, float, str]:
        # A known total is better than an unknown one at the same headline price.
        total = offer.effective_total
        return (
            0 if total is not None and offer.shipping_known else 1,
            total if total is not None else float("inf"),
            offer.shop.casefold(),
        )

    def best_by_condition(self) -> dict[str, Offer]:
        best: dict[str, Offer] = {}
        for offer in self._offers:
            current = best.get(offer.condition)
            if current is None or self._rank_key(offer) < self._rank_key(current):
                best[offer.condition] = offer
        return best

    def best_overall(self) -> Offer | None:
        candidates = [offer for offer in self._offers if offer.effective_total is not None]
        if not candidates:
            return None
        return min(candidates, key=self._rank_key)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [offer.to_dict() for offer in self._offers]


def condition_label(condition: str, language: str = "en") -> str:
    labels = {
        "new": ("neu", "new"),
        "refurbished": ("refurbished", "refurbished"),
        "used": ("gebraucht", "used"),
        "unknown": ("unbekannt", "unknown"),
    }
    de, en = labels.get(condition, labels["unknown"])
    return de if str(language).startswith("de") else en
