"""Source adapters that turn each provider's payload into ``Offer`` objects.

The network clients stay in ``websearch``/``best_deal``; this module is deliberately
pure so it can be tested without HTTP and so a new source only has to produce Offers.
"""

from __future__ import annotations

from typing import Any, Iterable

from .offers import CONDITIONS, Offer, OfferSet

SOURCE_CATALOG = "pricewatcha"
SOURCE_WEB = "web"
SOURCE_IDEALO = "idealo"
SOURCE_GEIZHALS = "geizhals"


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def catalog_offers(payload: dict[str, Any]) -> list[Offer]:
    """Catalog deals as normalized offers."""
    offers: list[Offer] = []
    for deal in payload.get("deals") or []:
        if not isinstance(deal, dict) or deal.get("price") is None:
            continue
        product_id = str(deal.get("product_id") or "") or None
        offers.append(
            Offer(
                source=SOURCE_CATALOG,
                source_id=product_id,
                title=str(deal.get("name") or deal.get("product_id") or "?"),
                url=str(deal.get("url") or ""),
                shop=str(deal.get("shop") or "?"),
                price=float(deal["price"]),
                currency=str(deal.get("currency") or "EUR"),
                condition=str(deal.get("condition") or "unknown"),
                trackable=bool(product_id),
                verified=True,
                extra={
                    "capacity": deal.get("capacity"),
                    "product_id": product_id,
                },
            )
        )
    return offers


def web_offers(deals: Iterable[Any]) -> list[Offer]:
    """Direct shop offers from the web-search adapter."""
    offers: list[Offer] = []
    for deal in deals:
        price = _value(deal, "price")
        url = str(_value(deal, "url") or "")
        if price is None or not url.startswith("http"):
            continue
        offers.append(
            Offer(
                source=SOURCE_WEB,
                title=str(_value(deal, "title") or url),
                url=url,
                shop=str(_value(deal, "shop") or "?"),
                price=float(price),
                currency=str(_value(deal, "currency") or "EUR"),
                condition=str(_value(deal, "condition") or "unknown"),
                trackable=True,
                verified=True,
            )
        )
    return offers


def comparison_offers(comparisons: Iterable[Any]) -> list[Offer]:
    """Idealo/Geizhals aggregate prices as non-trackable reference offers."""
    offers: list[Offer] = []
    for comparison in comparisons:
        source = str(_value(comparison, "source") or SOURCE_IDEALO)
        title = str(_value(comparison, "title") or source)
        url = str(_value(comparison, "url") or "")
        new_price = _value(comparison, "new_price")
        used_price = _value(comparison, "used_price")
        if new_price is not None:
            offers.append(
                Offer(
                    source=source,
                    title=title,
                    url=url,
                    shop=source,
                    price=float(new_price),
                    currency=str(_value(comparison, "new_currency") or "EUR"),
                    condition="new",
                    trackable=False,
                    verified=False,
                    extra={
                        "offer_count": _value(comparison, "new_count"),
                        "aggregate": True,
                    },
                )
            )
        if used_price is not None:
            offers.append(
                Offer(
                    source=source,
                    title=title,
                    url=url,
                    shop=source,
                    price=float(used_price),
                    currency=str(_value(comparison, "used_currency") or "EUR"),
                    condition="used",
                    trackable=False,
                    verified=False,
                    extra={"aggregate": True, "b_ware": True},
                )
            )
    return offers


def build_offer_view(
    payload: dict[str, Any],
    web_deals: Iterable[Any] = (),
    comparisons: Iterable[Any] = (),
) -> OfferSet:
    """Attach normalized offers and best-by-condition to a /deal payload."""
    offer_set = OfferSet()
    catalog = catalog_offers(payload)
    web = web_offers(web_deals)
    compared = comparison_offers(comparisons)
    offer_set.extend(catalog)
    offer_set.extend(web)
    offer_set.extend(compared)

    best = offer_set.best_by_condition()
    payload["offers"] = offer_set.to_dicts()
    payload["best_by_condition"] = [
        {"condition": condition, "offer": best[condition].to_dict()}
        for condition in CONDITIONS
        if condition in best
    ]
    overall = offer_set.best_overall()
    payload["best_overall"] = overall.to_dict() if overall else None
    payload["source_status"] = [
        {"source": SOURCE_CATALOG, "count": len(catalog), "ok": True},
        {"source": SOURCE_WEB, "count": len(web), "ok": bool(web)},
        *(
            {"source": source, "count": count, "ok": bool(count)}
            for source, count in _comparison_counts(compared)
        ),
    ]
    return offer_set


def _comparison_counts(offers: list[Offer]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for offer in offers:
        counts[offer.source] = counts.get(offer.source, 0) + 1
    return list(counts.items())
