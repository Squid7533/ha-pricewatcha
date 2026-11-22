"""Deal ranking for Pricewatcha.

"Find the best deal for X" is not a search. Search is keyword AND and returns up to 50
rows, mostly variants of the same product plus accessories that happen to match. A
useful answer needs four steps:

1. drop accessories (cases, glass, cables — they match "iphone 15 pro" too),
2. keep the cheapest candidates, since only those can be a deal,
3. look up each candidate's price history and rank by what the price is worth
   *relative to that product's own history*, not by the absolute number,
4. look up the product page: the search endpoint carries no URL, and some merchants
   (asgoodasnew) put the condition ("sehr gut", "wie neu") only into that URL.

The result carries the ranked ``deals`` plus ``groups`` — the same offers bucketed by
capacity and condition. That is what turns three prices into "very good 669 €, good
689 €, like new 699 €, new 889 €", which is the question behind "is it worth paying
more for a better condition?".
"""

from __future__ import annotations

import asyncio
import re
import statistics
from typing import Any, Coroutine

from .api import PricewatchaRateLimitError
from .const import ACCESSORY_PRICE_FLOOR, ACCESSORY_TERMS, SEARCH_EXCLUDES

CAPACITY_RE = re.compile(r"\b(\d{1,4})\s?(gb|tb)\b", re.IGNORECASE)
GRADE_RE = re.compile(r"\bgrade\s?([a-c]\+?)\b", re.IGNORECASE)

# Condition words appear in merchant names ("Grade B") or, for some shops, only in
# the product URL ("…-wolkenweiss-sehr-gut.html"). Order matters: "sehr gut" also
# contains "gut", and "wie neu" also contains "neu", so specifics are tested first.
_CONDITION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), label)
    for pattern, label in (
        (r"\bsehr gut\b", "very good"),
        (r"\bwie neu\b", "like new"),
        (r"\bneuwertig\b", "like new"),
        (r"\bgrade\s?a\+?\b", "grade A"),
        (r"\bgrade\s?b\+?\b", "grade B"),
        (r"\bgrade\s?c\+?\b", "grade C"),
        (r"\brefurbished\b", "refurbished"),
        (r"\brefurb\b", "refurbished"),
        (r"\bgebraucht\b", "used"),
        (r"\bused\b", "used"),
        (r"\bneu\b", "new"),
        (r"\bnew\b", "new"),
        (r"\bgut\b", "good"),
    )
)


def looks_like_accessory(name: str) -> bool:
    """True for cases, protectors and cables rather than the device itself."""
    lowered = name.lower()
    return any(term in lowered for term in ACCESSORY_TERMS)


def capacity_of(name: str) -> str | None:
    """The storage variant in a product name, e.g. ``"256 GB"``."""
    match = CAPACITY_RE.search(name or "")
    if match is None:
        return None
    return f"{int(match.group(1))} {match.group(2).upper()}"


def condition_of(name: str, url: str | None = None) -> str | None:
    """The condition a merchant states in its name or URL slug.

    ``None`` when neither says anything — better than guessing "new".
    """
    for value in (name or "", _slug(url)):
        text = _normalise(value)
        for pattern, label in _CONDITION_PATTERNS:
            if pattern.search(text):
                return label
    return None


def _normalise(value: str) -> str:
    return value.lower().replace("-", " ").replace("_", " ")


def _slug(url: str | None) -> str:
    """The last path segment: where merchants put the condition, without the host.

    Looking at the whole URL would read "neu" out of a category path such as
    ``/neu/`` and call a used phone new.
    """
    if not url:
        return ""
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1]


def variant_of(name: str) -> str:
    """A short variant label from the name, e.g. ``"256 GB · Grade B"``."""
    parts: list[str] = []
    if capacity := capacity_of(name):
        parts.append(capacity)
    if match := GRADE_RE.search(name):
        parts.append(f"Grade {match.group(1).upper()}")
    if "refurb" in name.lower():
        parts.append("refurbished")
    return " · ".join(parts)


def _discount(price: float, reference: float | None) -> float | None:
    if not reference or reference <= 0:
        return None
    return round((reference - price) / reference * 100, 1)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


async def _empty() -> dict[str, Any]:
    return {}


async def _fetch(
    coroutine: Coroutine[Any, Any, Any],
    semaphore: asyncio.Semaphore,
    failures: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    """Run an enrichment request; a failure only costs that field.

    The failure is counted so the reply can say that links or history are missing
    instead of silently presenting a thinner shortlist as the whole picture.
    """
    async with semaphore:
        try:
            payload = await coroutine
        except Exception as err:  # noqa: BLE001 - enrichment is never fatal
            failures[kind] += 1
            if isinstance(err, PricewatchaRateLimitError):
                failures["rate_limited"] = True
            return {}
    return payload if isinstance(payload, dict) else {}


async def _enrich(
    client: Any,
    row: dict[str, Any],
    *,
    with_history: bool,
    semaphore: asyncio.Semaphore,
    failures: dict[str, Any],
) -> dict[str, Any]:
    """Turn a search row into a deal: URL, condition, history and value figures."""
    product_id = str(row.get("product_id") or "")
    name = str(row.get("name") or "")
    price = float(row["current_price"])

    detail_task = (
        _fetch(client.product(product_id), semaphore, failures, "details")
        if product_id and not row.get("product_url")
        else _empty()
    )
    history_task = (
        _fetch(client.price_history(product_id), semaphore, failures, "history")
        if product_id and with_history
        else _empty()
    )
    detail, history = await asyncio.gather(detail_task, history_task)

    url = detail.get("product_url") or row.get("product_url")
    deal: dict[str, Any] = {
        "product_id": row.get("product_id"),
        "name": row.get("name"),
        "shop": row.get("shop"),
        "price": price,
        "currency": row.get("currency") or "EUR",
        "url": url,
        "capacity": capacity_of(name),
        "condition": condition_of(name, url),
        "variant": variant_of(name),
    }

    if with_history:
        low = _as_float(history.get("historical_low"))
        average = _as_float(history.get("average_price"))
        deal["historical_low"] = low
        deal["historical_high"] = _as_float(history.get("historical_high"))
        deal["average_price"] = average
        deal["trend"] = history.get("trend")
        deal["vs_low_pct"] = _discount(price, low)
        deal["vs_average_pct"] = _discount(price, average)
        points = history.get("data_points")
        if isinstance(points, list) and points:
            deal["history"] = [
                {"date": point.get("date"), "price": _as_float(point.get("price"))}
                for point in points[-6:]
                if isinstance(point, dict) and point.get("price") is not None
            ]

    return deal


def _capacity_sort_key(capacity: str) -> int:
    if not capacity or capacity == "other":
        return 10**9
    number, _, unit = capacity.partition(" ")
    try:
        value = int(number)
    except ValueError:
        return 10**8
    return value * (1024 if unit.upper() == "TB" else 1)


def group_by_capacity(deals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket offers by capacity, one row per condition, cheapest first.

    Only the cheapest offer per condition is kept: the group answers "what does each
    condition cost?", not "list every shop".
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for deal in deals:
        buckets.setdefault(deal.get("capacity") or "other", []).append(deal)

    groups: list[dict[str, Any]] = []
    for capacity, offers in buckets.items():
        cheapest: dict[str, dict[str, Any]] = {}
        for deal in offers:
            key = deal.get("condition") or ""
            current = cheapest.get(key)
            if current is None or float(deal["price"]) < float(current["price"]):
                cheapest[key] = deal
        ordered = sorted(cheapest.values(), key=lambda deal: float(deal["price"]))
        groups.append(
            {
                "capacity": capacity,
                "offers": [
                    {
                        "product_id": deal.get("product_id"),
                        "condition": deal.get("condition"),
                        "price": deal["price"],
                        "shop": deal.get("shop"),
                        "url": deal.get("url"),
                        "currency": deal.get("currency"),
                        "historical_low": deal.get("historical_low"),
                    }
                    for deal in ordered
                ],
            }
        )

    groups.sort(key=lambda group: _capacity_sort_key(str(group["capacity"])))
    return groups


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

    Returns a dict with ``query``, ``matched`` (how many offers survived the
    filters), ``compared`` (the shortlist that was enriched), ``deals`` (the ranked
    top ``limit``) and ``groups`` (offers by capacity and condition).
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
    semaphore = asyncio.Semaphore(5)
    failures: dict[str, Any] = {"details": 0, "history": 0, "rate_limited": False}
    deals = await asyncio.gather(
        *(
            _enrich(
                client,
                row,
                with_history=with_history,
                semaphore=semaphore,
                failures=failures,
            )
            for row in shortlist
        )
    )

    # Sort by value against the product's own history; products without history fall
    # back to price, so a cheap unknown listing cannot outrank a proven discount.
    def score(deal: dict[str, Any]) -> tuple[float, float]:
        value = deal.get("vs_average_pct")
        if value is None:
            value = deal.get("vs_low_pct")
        return (float(value) if value is not None else float("-inf"), -float(deal["price"]))

    ranked = sorted(deals, key=score, reverse=True)
    result: dict[str, Any] = {
        "query": query,
        "compared": len(shortlist),
        "matched": len(rows),
        "deals": ranked[:limit],
        "groups": group_by_capacity(ranked),
    }
    if failures["details"] or failures["history"]:
        result["partial"] = dict(failures)
    return result
