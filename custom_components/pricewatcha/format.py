"""Message formatting for the Telegram front-end.

Plain functions with no Home Assistant imports, so they are unit-testable and the
output is identical whether it comes from a command, free text or a price change.
"""

from __future__ import annotations

from typing import Any

MAX_MESSAGE = 4000  # Telegram caps messages at 4096; leave room for a suffix


def truncate(text: str, limit: int = MAX_MESSAGE) -> str:
    """Keep a message inside Telegram's limit without cutting mid-word."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _price(value: Any, currency: str | None) -> str:
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        shown = str(int(number))
    else:
        shown = f"{number:.2f}"
    return f"{shown} {currency}".strip()


def format_help() -> str:
    return (
        "Pricewatcha commands:\n\n"
        "/deal iphone 18 pro — best value, ranked against each product's own price history\n"
        "/search iphone 18 pro — raw catalog matches\n"
        "/track <product url> — track a URL and watch it\n"
        "/watch <product_id> — watch a catalog product\n"
        "/alerts — your price alerts\n\n"
        "Or just type what you are looking for."
    )


def format_deals(payload: dict[str, Any]) -> str:
    query = payload.get("query") or ""
    deals = payload.get("deals") or []
    if not deals:
        return f'Nothing matched "{query}".'

    lines = [f'Best deals for "{query}" ({payload.get("compared") or len(deals)} compared):']
    for index, deal in enumerate(deals, start=1):
        name = deal.get("name") or deal.get("product_id") or "?"
        price = _price(deal.get("price"), deal.get("currency"))
        shop = deal.get("shop") or "?"
        line = f"{index}. {name} — {price} at {shop}"
        discount = deal.get("vs_average_pct")
        if discount is not None:
            line += f" ({discount}% below its average)"
        lines.append(line)
        if deal.get("url"):
            lines.append(deal["url"])
    return "\n".join(lines)


def format_search(payload: dict[str, Any]) -> str:
    query = payload.get("query") or ""
    results = payload.get("results") or []
    if not results:
        return f'Nothing matched "{query}".'

    lines = [f'Search "{query}" — {payload.get("count") or len(results)} matches:']
    for result in results:
        name = result.get("name") or result.get("product_id") or "?"
        price = _price(result.get("current_price"), result.get("currency"))
        shop = result.get("shop") or "?"
        lines.append(f"{name} — {price} · {shop}")
        if result.get("product_url"):
            lines.append(result["product_url"])
        lines.append("")
    return "\n".join(lines).strip()


def format_alerts(payload: dict[str, Any]) -> str:
    alerts = payload.get("alerts") or []
    if not alerts:
        return "No price alerts set."

    lines = ["Your price alerts:"]
    for alert in alerts:
        name = alert.get("name") or alert.get("product_id") or "?"
        parts = []
        if alert.get("notify_on_drop"):
            parts.append("on any drop")
        if alert.get("notify_on_rise"):
            parts.append("on any rise")
        if alert.get("min_threshold_price"):
            parts.append(f"min {_price(alert['min_threshold_price'], alert.get('currency'))}")
        if alert.get("max_threshold_price"):
            parts.append(f"max {_price(alert['max_threshold_price'], alert.get('currency'))}")
        state = "" if alert.get("is_active", True) else " (paused)"
        detail = ", ".join(parts) if parts else "no condition"
        lines.append(f"#{alert.get('alert_id')} {name} — {detail}{state}")
    return "\n".join(lines)


def format_track(payload: dict[str, Any]) -> str:
    status = payload.get("status") or "unknown"
    product = payload.get("product") or {}
    error = payload.get("error") or {}

    if status == "completed" and product:
        lines = [f"Tracking submitted: {status}"]
        lines.append(
            f"{product.get('name') or '?'} — "
            f"{_price(product.get('current_price'), product.get('currency'))}"
        )
        if product.get("product_url"):
            lines.append(product["product_url"])
        return "\n".join(lines)

    if status == "failed":
        return f"Tracking failed: {error.get('code') or 'unknown'} — {error.get('message') or ''}".strip()

    return (
        f"Tracking submitted: {status}\n"
        f"Job {payload.get('job_id') or '?'} is still running; the price appears on the next poll."
    )


def format_price_drop(data: dict[str, Any]) -> str:
    name = data.get("name") or data.get("product_id") or "?"
    lines = [
        f"Price drop: {name}",
        f"{_price(data.get('new_price'), data.get('currency'))} "
        f"(was {_price(data.get('old_price'), data.get('currency'))}, {data.get('change_pct')}%) "
        f"at {data.get('shop') or '?'}",
    ]
    if data.get("product_url"):
        lines.append(data["product_url"])
    return "\n".join(lines)


def format_usage(command: str, example: str) -> str:
    return f"Usage: {command} {example}"
