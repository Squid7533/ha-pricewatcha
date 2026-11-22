"""Message formatting for the Telegram front-end.

Plain functions with no Home Assistant imports, so they are unit-testable and the
output is identical whether it comes from a command, free text or a price change.

Messages are HTML: Telegram then renders bold prices and tappable "See offer" links
instead of a wall of raw URLs. HTML has its own footgun — an unescaped ``&`` or
``<`` in a product name is a parse error — so every dynamic value goes through
:func:`_esc`, and :func:`truncate` closes the tags it cuts through.
"""

from __future__ import annotations

import html
import re
from typing import Any

MAX_MESSAGE = 4000  # Telegram caps messages at 4096; leave room for closing tags

_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)[^>]*>")
_LINK_RE = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.DOTALL)
_STRIP_TAG_RE = re.compile(r"</?[a-zA-Z][\w-]*[^>]*>")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _esc_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def truncate(text: str, limit: int = MAX_MESSAGE) -> str:
    """Keep a message inside Telegram's limit without breaking its HTML.

    Cuts at the last space outside a tag and re-opens nothing: the tags that were
    still open at the cut are closed after the ellipsis, so Telegram never sees a
    dangling ``<b>`` and rejects the whole message.
    """
    if len(text) <= limit:
        return text

    open_tags: list[str] = []
    last_safe: tuple[int, tuple[str, ...]] | None = None
    index = 0
    while index < len(text) and index < limit:
        char = text[index]
        if char == "<":
            close = text.find(">", index)
            if close == -1 or close >= limit:
                break
            tag = text[index : close + 1]
            match = _TAG_RE.match(tag)
            if match:
                name = match.group(2).lower()
                if tag.startswith("</"):
                    if open_tags:
                        open_tags.pop()
                elif not tag.endswith("/>"):
                    open_tags.append(name)
            index = close + 1
            continue
        if char == " ":
            last_safe = (index, tuple(open_tags))
        index += 1

    if last_safe is None:
        # No word boundary: cut before a tag that would be split, and still close
        # whatever was open at that point.
        cut = text.rfind("<", 0, limit)
        cut = limit if cut <= 0 else cut
        closers = "".join(f"</{tag}>" for tag in reversed(open_tags))
    else:
        cut, tags = last_safe
        closers = "".join(f"</{tag}>" for tag in reversed(tags))

    return text[:cut].rstrip() + "…" + closers


def to_plain(text: str) -> str:
    """Strip the HTML for the fallback send, keeping link targets readable."""
    text = _LINK_RE.sub(
        lambda match: f"{match.group(2)} ({html.unescape(match.group(1))})", text
    )
    text = _STRIP_TAG_RE.sub("", text)
    return html.unescape(text)


def _money(value: Any, currency: str | None) -> str:
    """A price a human reads at a glance: ``669 €``, ``1 019 €``, ``949.50 €``."""
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    if number == int(number):
        shown = f"{int(number):,}"
    else:
        shown = f"{number:,.2f}"
    shown = shown.replace(",", " ")
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper())
    if symbol:
        return f"{shown} {symbol}"
    return f"{shown} {_esc(currency)}".strip()


def _whole(value: Any) -> Any:
    """Averages are estimates; 852.33 € reads worse than 852 €."""
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return value


def _date(value: Any) -> str:
    parts = str(value or "").split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return f"{parts[2]}.{parts[1]}."
    return _esc(value)


def _percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    return f"{number:.1f}".rstrip("0").rstrip(".") + "%"


def format_help() -> str:
    return (
        "Pricewatcha commands:\n\n"
        "/deal iphone 18 pro — best value, ranked against each product's own price history\n"
        "/ask is 669 a good price? — ask the AI about your last deal\n"
        "/search iphone 18 pro — raw catalog matches\n"
        "/track <product url> — track a URL and watch it\n"
        "/watch <product_id> — watch a catalog product\n"
        "/alerts — your price alerts\n\n"
        "Or just type what you are looking for."
    )


def _deal_lines(index: int, deal: dict[str, Any]) -> list[str]:
    currency = deal.get("currency")
    title = f"{index}. {_money(deal.get('price'), currency)} · {_esc(deal.get('shop') or '?')}"
    if deal.get("condition"):
        title += f" · {_esc(deal['condition'])}"
    lines = [f"<b>{title}</b>"]
    if deal.get("name"):
        lines.append(_esc(deal["name"]))

    meta: list[str] = []
    discount = deal.get("vs_average_pct")
    if discount is not None:
        average = deal.get("average_price")
        direction = "below" if float(discount) >= 0 else "above"
        meta.append(
            f"{_percent(abs(float(discount)))} {direction} its own average "
            f"({_money(_whole(average), currency)})"
        )
    low = deal.get("historical_low")
    if low is not None:
        if deal.get("vs_low_pct") is not None and float(deal["vs_low_pct"]) >= 0:
            meta.append("lowest recorded price")
        else:
            meta.append(f"low was {_money(low, currency)}")
    if deal.get("trend") == "downward":
        meta.append("price falling")
    if meta:
        lines.append(" · ".join(meta))

    history = deal.get("history") or []
    if index == 1 and history:
        points = " → ".join(
            f"{_date(point.get('date'))} {_money(point.get('price'), currency)}"
            for point in history
        )
        lines.append(f"Price history: {points}")

    if deal.get("url"):
        lines.append(f'<a href="{_esc_attr(deal["url"])}">See offer</a>')
    return lines


def _group_lines(groups: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for group in groups:
        offers = group.get("offers") or []
        if not offers:
            continue
        parts = [
            f"{_esc(offer.get('condition') or 'unrated')} "
            f"{_money(offer.get('price'), offer.get('currency'))}"
            for offer in offers
        ]
        capacity = group.get("capacity") or "other"
        label = "Other" if capacity == "other" else _esc(capacity)
        lines.append(f"{label}: " + " · ".join(parts))
    return lines


def _first_price(group: dict[str, Any]) -> float:
    offers = group.get("offers") or []
    if not offers:
        return float("inf")
    try:
        return float(offers[0].get("price"))
    except (TypeError, ValueError):
        return float("inf")


def _verdict(deals: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str | None:
    """One data-backed sentence about the ranked best deal, not the absolute cheapest.

    A grade-C bargain is not the answer to "best deal"; the ranking already says which
    offer is the best value. The verdict explains that pick and what a better condition
    would add, using the same-capacity group behind it.
    """
    if not deals:
        return None

    top = deals[0]
    currency = top.get("currency")
    text = f"Best value: {_money(top.get('price'), currency)}"
    if top.get("condition"):
        text += f" ({_esc(top['condition'])})"
    if top.get("shop"):
        text += f" at {_esc(top['shop'])}"

    discount = top.get("vs_average_pct")
    try:
        if discount is not None and float(discount) >= 0:
            text += f", {_percent(discount)} below its own average"
        if float(top.get("vs_low_pct")) >= 0:
            text += ", the lowest price recorded for this variant"
    except (TypeError, ValueError):
        pass
    text += "."

    group = next(
        (item for item in groups if item.get("capacity") == top.get("capacity")), None
    )
    offers = group.get("offers", []) if group else []

    def premium(condition: str) -> float | None:
        offer = next((item for item in offers if item.get("condition") == condition), None)
        if offer is None:
            return None
        try:
            extra = float(offer["price"]) - float(top["price"])
        except (TypeError, ValueError):
            return None
        return extra if extra > 0 else None

    new_extra = premium("new")
    like_new_extra = premium("like new")
    if new_extra is not None:
        text += f" New costs {_money(_whole(new_extra), currency)} more"
        if like_new_extra is not None:
            text += f", like new {_money(_whole(like_new_extra), currency)} more"
        text += "."
    elif like_new_extra is not None:
        text += f" Like new costs {_money(_whole(like_new_extra), currency)} more."

    if (
        new_extra is not None
        and like_new_extra is not None
        and top.get("condition") not in ("new", "like new")
    ):
        text += " Like new is usually the best compromise."

    return text


def format_deals(payload: dict[str, Any]) -> str:
    query = payload.get("query") or ""
    deals = payload.get("deals") or []
    if not deals:
        return f'Nothing matched "{_esc(query)}".'

    compared = payload.get("compared") or len(deals)
    matched = payload.get("matched")
    counts = f"{compared} compared"
    if isinstance(matched, int) and matched > compared:
        counts = f"{matched} matches · {compared} compared"

    lines = [f"<b>{_esc(query)}</b> — {counts}", ""]
    for index, deal in enumerate(deals, start=1):
        lines.extend(_deal_lines(index, deal))
        if index < len(deals):
            lines.append("")

    groups = payload.get("groups") or []
    group_lines = _group_lines(groups)
    if group_lines:
        lines += ["", "<b>Prices by condition</b>", *group_lines]

    verdict = _verdict(deals, groups)
    if verdict:
        lines += ["", f"<b>Verdict:</b> {verdict}"]

    partial = payload.get("partial") or {}
    missing = []
    if partial.get("details"):
        missing.append("offer links")
    if partial.get("history"):
        missing.append("price history")
    if missing:
        reason = " (rate limit)" if partial.get("rate_limited") else ""
        lines += [
            "",
            f"<i>Some {' and '.join(missing)} could not be loaded{reason}; "
            "try again in a moment.</i>",
        ]

    lines += ["", "<i>Prices from the Pricewatcha catalog; the shop page is authoritative.</i>"]
    return "\n".join(lines)


def format_search(payload: dict[str, Any]) -> str:
    query = payload.get("query") or ""
    results = payload.get("results") or []
    if not results:
        return f'Nothing matched "{_esc(query)}".'

    lines = [f'Search "{_esc(query)}" — {payload.get("count") or len(results)} matches:']
    for result in results:
        name = _esc(result.get("name") or result.get("product_id") or "?")
        price = _money(result.get("current_price"), result.get("currency"))
        shop = _esc(result.get("shop") or "?")
        lines.append(f"{name} — {price} · {shop}")
        if result.get("product_url"):
            url = result["product_url"]
            lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
        lines.append("")
    return "\n".join(lines).strip()


def format_alerts(payload: dict[str, Any]) -> str:
    alerts = payload.get("alerts") or []
    if not alerts:
        return "No price alerts set."

    lines = ["Your price alerts:"]
    for alert in alerts:
        name = _esc(alert.get("name") or alert.get("product_id") or "?")
        parts = []
        if alert.get("notify_on_drop"):
            parts.append("on any drop")
        if alert.get("notify_on_rise"):
            parts.append("on any rise")
        if alert.get("min_threshold_price"):
            parts.append(f"min {_money(alert['min_threshold_price'], alert.get('currency'))}")
        if alert.get("max_threshold_price"):
            parts.append(f"max {_money(alert['max_threshold_price'], alert.get('currency'))}")
        state = "" if alert.get("is_active", True) else " (paused)"
        detail = ", ".join(parts) if parts else "no condition"
        lines.append(f"#{alert.get('alert_id')} {name} — {detail}{state}")
    return "\n".join(lines)


def format_track(payload: dict[str, Any]) -> str:
    status = payload.get("status") or "unknown"
    product = payload.get("product") or {}
    error = payload.get("error") or {}

    if status == "completed" and product:
        lines = [f"Tracking submitted: {_esc(status)}"]
        lines.append(
            f"{_esc(product.get('name') or '?')} — "
            f"{_money(product.get('current_price'), product.get('currency'))}"
        )
        if product.get("product_url"):
            url = product["product_url"]
            lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
        return "\n".join(lines)

    if status == "failed":
        return (
            f"Tracking failed: {_esc(error.get('code') or 'unknown')} — "
            f"{_esc(error.get('message') or '')}"
        ).strip()

    return (
        f"Tracking submitted: {_esc(status)}\n"
        f"Job {_esc(payload.get('job_id') or '?')} is still running; the price appears "
        "on the next poll."
    )


def format_price_drop(data: dict[str, Any]) -> str:
    name = _esc(data.get("name") or data.get("product_id") or "?")
    currency = data.get("currency")
    lines = [
        f"Price drop: {name}",
        f"{_money(data.get('new_price'), currency)} "
        f"(was {_money(data.get('old_price'), currency)}, {_percent(data.get('change_pct'))}) "
        f"at {_esc(data.get('shop') or '?')}",
    ]
    if data.get("product_url"):
        url = data["product_url"]
        lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
    return "\n".join(lines)


def format_usage(command: str, example: str) -> str:
    return f"Usage: {command} {example}"


def format_ai_section(
    result: Any,
    language: str = "de",
    *,
    answer: bool = False,
) -> str:
    """The optional AI paragraph: label, grounded text, and what it cost."""
    german = language == "de"
    if answer:
        label = "Antwort" if german else "Answer"
    else:
        label = "Einschätzung" if german else "AI take"
    model = getattr(result, "model", "") or "AI"
    spent = float(getattr(result, "spent_today", 0.0) or 0.0)
    footer = (
        f"{model} · {spent:.4f} $ heute" if german else f"{model} · ${spent:.4f} today"
    )
    return (
        f"<b>{label}</b>\n{_esc(getattr(result, 'text', ''))}"
        f"\n\n<i>{_esc(footer)}</i>"
    )


_AI_REASONS = {
    "de": {
        "not_configured": "Die KI ist noch nicht eingerichtet.",
        "budget_daily": "Das Tagesbudget reicht für diese Anfrage nicht mehr.",
        "budget_monthly": "Das Monatsbudget reicht für diese Anfrage nicht mehr.",
        "auth": "Der API-Schlüssel wurde vom Anbieter abgelehnt.",
        "rate_limit": "Der Anbieter begrenzt gerade die Anfragen. Später erneut versuchen.",
        "timeout": "Der Anbieter hat nicht rechtzeitig geantwortet.",
        "empty": "Das Modell hat keine Antwort geliefert.",
        "unknown_price": "Die Antwort nannte einen Preis, der nicht in den Angebotsdaten steht.",
        "provider_error": "Der Anbieter hat die Anfrage abgelehnt.",
    },
    "en": {
        "not_configured": "AI is not configured yet.",
        "budget_daily": "The daily budget does not cover this request any more.",
        "budget_monthly": "The monthly budget does not cover this request any more.",
        "auth": "The provider rejected the API key.",
        "rate_limit": "The provider is rate limiting requests. Try again later.",
        "timeout": "The provider did not answer in time.",
        "empty": "The model returned no answer.",
        "unknown_price": "The answer mentioned a price that is not in the offer data.",
        "provider_error": "The provider rejected the request.",
    },
}


def format_ai_unavailable(reason: str, language: str = "de") -> str:
    german = language == "de"
    label = "KI nicht verfügbar" if german else "AI unavailable"
    text = _AI_REASONS["de" if german else "en"].get(reason, reason)
    return f"<b>{label}</b>\n{_esc(text)}"
