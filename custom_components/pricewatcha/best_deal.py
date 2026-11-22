"""Deal ranking for Pricewatcha.

"Find the best deal for X" is not a search. Search is keyword AND and returns up to 50
rows, mostly variants of the same product plus accessories that happen to match. A
useful answer needs three steps:

1. drop accessories (cases, glass, cables — they match "iphone 15 pro" too),
2. keep the cheapest candidates, since only those can be a deal,
3. look up each candidate's price history and rank by what the price is worth
   *relative to that product's own history*, not by the absolute number.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from .const import ACCESSORY_PRICE_FLOOR, ACCESSORY_TERMS, SEARCH_EXCLUDES

CAPACITY_RE = re.compile(r"\b(\d{1,4})\s?(gb|tb)\b", re.IGNORECASE)
GRADE_RE = re.compile(r"\bgrade\s?([a-c]\+?)\b", re.IGNORECASE)


def looks_like_accessory(name: str) -> bool:
    """True for cases, protectors and cables rather than the device itself."""
    lowered = name.lower()
    return any(term in lowered for term in ACCESSORY_TERMS)


def variant_of(name: str) -> str:
    """A short variant label from the name, e.g. ``"256 GB · Grade B"``."""
    parts: list[str] = []
    if match := CAPACITY_RE.search(name):
        parts.append(f"{match.group(1)} {match.group(2).upper()}")
    if match := GRADE_RE.search(name):
        parts.append(f"Grade {match.group(1).upper()}")
    if "refurb" in name.lower():
        parts.append("refurbished")
    return " · ".join(parts)


def _discount(price: float, reference: float | None) -> float | None:
    if not reference or reference <= 0:
        return None
    return round((reference - price) / reference * 100, 1)


def build_query(query: str, *, include_accessories: bool) -> str:
    """Append minus-prefixed exclude terms unless accessories were requested.

    The catalog is accessory-heavy, so without this the 50-row window fills up with
    cases and the ranking compares almost nothing. Terms already present are kept once.
    """
    if include_accessories:
        return query
    present = {token.lower() for token in query.split()}
    extras = [term for term in SEARCH_EXCLUDES if term.lower() not in present]
    return " ".join([query, *extras])


async def find_best_deals(
    client: Any,
    query: str,
    *,
    limit: int = 5,
    max_price: float | None = None,
    candidates: int = 10,
    include_accessories: bool = False,
    with_history: bool = True,
) -> dict[str, Any]:
    """Rank catalog matches by value against their own price history.

    Returns a dict with ``query``, ``candidates`` (how many were compared) and
    ``deals`` — each carrying price, historical low/average, discount and URL.
    """
    # 200 is the API maximum. It matters: the exclude-filtered window at 50 returned
    # 2 device listings for "iphone 15 pro", at 200 the same query returned 144.
    results = await client.search(
        build_query(query, include_accessories=include_accessories), limit=200
    )

    rows: list[dict[str, Any]] = []
    for row in results:
        price = row.get("current_price")
        if price is None:
            continue
        name = str(row.get("name") or "")
        if not include_accessories and looks_like_accessory(name):
            continue
        if max_price is not None and float(price) > max_price:
            continue
        rows.append(row)

    rows.sort(key=lambda row: float(row.get("current_price") or 0))

    if rows and not include_accessories:
        median = statistics.median(float(row["current_price"]) for row in rows)
        floor = median * ACCESSORY_PRICE_FLOOR
        rows = [row for row in rows if float(row["current_price"]) >= floor]

    # The catalog lists the same offer under several URLs; keep one row per offer.
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        signature = (row.get("name"), row.get("current_price"), row.get("shop"))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(row)
    rows = unique

    shortlist = rows[: max(candidates, limit)]

    deals: list[dict[str, Any]] = []
    for row in shortlist:
        price = float(row["current_price"])
        deal: dict[str, Any] = {
            "product_id": row.get("product_id"),
            "name": row.get("name"),
            "shop": row.get("shop"),
            "price": price,
            "currency": row.get("currency") or "EUR",
            "url": row.get("product_url"),
            "variant": variant_of(str(row.get("name") or "")),
        }
        if with_history and deal["product_id"]:
            try:
                history = await client.price_history(str(deal["product_id"]))
            except Exception:  # noqa: BLE001 - history is an enrichment, never fatal
                history = {}
            low = history.get("historical_low")
            average = history.get("average_price")
            deal["historical_low"] = low
            deal["average_price"] = average
            deal["trend"] = history.get("trend")
            deal["vs_low_pct"] = _discount(price, float(low) if low is not None else None)
            deal["vs_average_pct"] = _discount(price, float(average) if average is not None else None)
        deals.append(deal)

    # Sort by value against the product's own history; products without history fall
    # back to price, so a cheap unknown listing cannot outrank a proven discount.
    def score(deal: dict[str, Any]) -> tuple[float, float]:
        value = deal.get("vs_average_pct")
        if value is None:
            value = deal.get("vs_low_pct")
        return (float(value) if value is not None else float("-inf"), -float(deal["price"]))

    deals.sort(key=score, reverse=True)
    return {
        "query": query,
        "compared": len(shortlist),
        "matched": len(rows),
        "deals": deals[:limit],
    }
