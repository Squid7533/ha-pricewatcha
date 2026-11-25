"""Message formatting for the Telegram front-end.

Plain functions with no Home Assistant imports, so they are unit-testable and the
output is identical whether it comes from a command, free text or a price change.

Messages are HTML: Telegram then renders bold prices and tappable "See offer" links
instead of a wall of raw URLs. HTML has its own footgun — an unescaped ``&`` or
``<`` in a product name is a parse error — so every dynamic value goes through
:func:`_esc`, and :func:`truncate` closes the tags it cuts through.

Every user-facing message takes a ``language`` (``en``/``de``); the deterministic
table and the AI paragraph therefore speak the same language.
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


_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "help_header": "Pricewatcha commands:",
        "help_deal": "/deal iphone 18 pro — best value, ranked against each product's own price history",
        "help_ask": "/ask is 669 a good price? — ask the AI about your last deal",
        "help_search": "/search iphone 18 pro — raw catalog matches",
        "help_track": "/track <product url> — track a URL and watch it",
        "help_untrack": "/untrack #id — stop tracking a web offer",
        "help_trackalert": "/trackalert 1 target 700 — alert for a tracked web offer",
        "help_trackalerts": "/trackalerts — tracked web-offer alerts",
        "help_watch": "/watch 1 — watch a result from /deal or /search",
        "help_unwatch": "/unwatch 1 — stop watching a product",
        "help_watchlist": "/watchlist — watched products with numbers",
        "help_history": "/history 1 — price history of a result",
        "help_product": "/product 1 — details of a result",
        "help_status": "/status — watchlist, last poll, AI spend",
        "help_refresh": "/refresh — poll now",
        "help_alerts": "/alerts — your price alerts",
        "help_alert": "/alert 1324 min 620 | drop only — change a price alert",
        "help_reset": "/reset — forget the current conversation",
        "help_free": "Or just type what you are looking for.",
        "reset_done": "Conversation cleared. The next /deal starts fresh.",
        "nothing_matched": 'Nothing matched "{query}".',
        "matches_compared": "{matched} matches · {compared} compared",
        "compared": "{compared} compared",
        "see_offer": "See offer",
        "price_history": "Price history",
        "prices_by_condition": "Prices by condition",
        "web_deals_header": "Web finds (unverified)",
        "web_deals_hint": "Web prices are unverified; /track 1 watches one.",
        "best_offers_header": "Best offers",
        "best_new": "Best new price",
        "best_refurbished": "Best refurbished price",
        "best_used": "Best used price",
        "best_unknown": "Best price (condition unknown)",
        "best_plus_shipping": "plus shipping",
        "best_aggregate_prefix": "from",
        "comparison_header": "Price comparison (unverified)",
        "comparison_new": "new from {price}",
        "comparison_used": "B-Ware/used from {price}",
        "comparison_count": "{count} offers",
        "comparison_hint": "Aggregate price excluding shipping; not trackable.",
        "comparison_verdict_note": "A cheaper comparison price is listed above (unverified).",
        "verdict": "Verdict",
        "below_average": "{pct} below its own average ({average})",
        "above_average": "{pct} above its own average ({average})",
        "below_average_short": "{pct} below its own average",
        "above_average_short": "{pct} above its own average",
        "lowest_recorded": "lowest recorded price",
        "lowest_variant": "the lowest price recorded for this variant",
        "web_verdict_note": "A cheaper web find is listed above (unverified).",
        "low_was": "low was {price}",
        "price_falling": "price falling",
        "new_costs_more": "New costs {amount} more",
        "like_new_costs_more": "Like new costs {amount} more",
        "like_new_short": "like new {amount} more",
        "like_new_compromise": "Like new is usually the best compromise.",
        "unrated": "unrated",
        "footer": "Prices from the Pricewatcha catalog; the shop page is authoritative.",
        "search_header": 'Search "{query}" — {count} matches:',
        "alerts_header": "Your price alerts:",
        "alerts_empty": "No price alerts set.",
        "alert_updated": "Alert #{id} updated",
        "watchlist_header": "Your watchlist:",
        "watchlist_empty": "Nothing on the watchlist yet.",
        "history_header": "Price history: {name}",
        "history_stats": "Low {low} · average {average} · high {high}",
        "history_trend": "Trend: {trend}",
        "history_empty": "No price history for this product.",
        "product_header": "Product: {name}",
        "product_line": "{price} · {shop} · {status}",
        "product_checked": "Last checked: {when}",
        "status_header": "Pricewatcha status",
        "status_watchlist": "Watchlist: {count} products",
        "status_tracked": "Tracked web offers: {count}",
        "status_last_update": "Last update: {when}",
        "status_ai": "AI spend: {today} today · {month} this month",
        "status_ai_off": "AI: off",
        "status_api": "Pricewatcha API: {state}",
        "status_ok": "reachable",
        "status_failed": "not reachable",
        "alert_created": "Alert #{id} created",
        "alert_details": "Alert #{id}",
        "unwatched": "No longer watching {name}.",
        "refreshed": "Watchlist refreshed.",
        "ref_not_found": "Not found in the last results. Run /deal or /search first.",
        "ref_ambiguous": "That matches several entries; use a number from the list.",
        "alert_deleted": "Alert #{id} deleted.",
        "alert_not_found": "Alert #{id} not found.",
        "action_tools_unsupported": "This AI provider cannot call tools. Use /watch, /alert, /history and the other commands instead.",

        "on_any_drop": "on any drop",
        "on_any_rise": "on any rise",
        "min": "min {price}",
        "max": "max {price}",
        "no_condition": "no condition",
        "paused": "(paused)",
        "tracking_submitted": "Tracking submitted: {status}",
        "tracking_failed": "Tracking failed: {code} — {message}",
        "tracking_running": "Job {job} is still running; the price appears on the next poll.",
        "price_drop": "Price drop: {name}",
        "was": "was {price}",
        "at": "at {shop}",
        "usage": "Usage: {command} {example}",
        "ai_label": "AI take",
        "ai_answer": "Answer",
        "ai_unavailable": "AI unavailable",
        "sources": "Sources",
        "track_hint": "To watch one: /track 1",
        "offers_header": "Web offers from the last answer:",
        "offers_empty": "No web offers stored. Run /ask first, then /track <number>.",
        "offers_hint": "Watch one with /track <number>, or paste a URL.",
        "track_not_trackable": "That is an aggregate price, not a shop offer; open the source instead.",
        "tracked_added": "Tracked as #{id}",
        "tracked_exists": "Already tracked as #{id}",
        "tracked_none": "No external offers tracked yet. Use /track 1 after /deal.",
        "tracked_list_header": "Tracked external offers:",
        "tracked_alerts_empty": "No alerts on tracked web offers.",
        "tracked_target": "target {price}",
        "tracked_drop": "any drop",
        "tracked_untracked": "Stopped tracking #{id}.",
        "tracked_history_header": "Tracked history #{id}",
        "tracked_history_empty": "No snapshots yet.",
        "tracked_alert_set": "Alert for #{id}: {detail}",
        "tracked_alert_removed": "Alert for #{id} removed.",
        "tracked_drop_title": "Price alert: {title}",
        "tracked_drop_target": "target {target} reached",
        "tracked_drop_fall": "price dropped",
        "tracked_last_checked": "last checked {when}",

        "today": "today",
    },
    "de": {
        "help_header": "Pricewatcha-Befehle:",
        "help_deal": "/deal iphone 18 pro — bester Preis, am eigenen Preisverlauf gemessen",
        "help_ask": "/ask ist 669 ein guter Preis? — die KI zum letzten Deal fragen",
        "help_search": "/search iphone 18 pro — rohe Katalogtreffer",
        "help_track": "/track <produkt-url> — URL tracken und beobachten",
        "help_untrack": "/untrack #id — Web-Angebot nicht mehr tracken",
        "help_trackalert": "/trackalert 1 target 700 — Alarm für ein getracktes Angebot",
        "help_trackalerts": "/trackalerts — Alarme für Web-Angebote",
        "help_watch": "/watch 1 — Treffer aus /deal oder /search beobachten",
        "help_unwatch": "/unwatch 1 — Beobachtung beenden",
        "help_watchlist": "/watchlist — beobachtete Produkte mit Nummern",
        "help_history": "/history 1 — Preisverlauf eines Treffers",
        "help_product": "/product 1 — Details eines Treffers",
        "help_status": "/status — Watchlist, letzter Poll, KI-Ausgaben",
        "help_refresh": "/refresh — jetzt aktualisieren",
        "help_alerts": "/alerts — deine Preisalarme",
        "help_alert": "/alert 1324 min 620 | drop only — Preisalarm ändern",
        "help_reset": "/reset — das aktuelle Gespräch vergessen",
        "help_free": "Oder einfach tippen, was du suchst.",
        "reset_done": "Gespräch gelöscht. Das nächste /deal beginnt von vorn.",
        "nothing_matched": 'Nichts gefunden für „{query}“.',
        "matches_compared": "{matched} Treffer · {compared} verglichen",
        "compared": "{compared} verglichen",
        "see_offer": "Zum Angebot",
        "price_history": "Preisverlauf",
        "prices_by_condition": "Preise nach Zustand",
        "web_deals_header": "Web-Funde (ungeprüft)",
        "web_deals_hint": "Web-Preise sind ungeprüft; /track 1 beobachtet eines.",
        "best_offers_header": "Beste Angebote",
        "best_new": "Bester Neupreis",
        "best_refurbished": "Bester Refurbished-Preis",
        "best_used": "Bester Gebrauchtpreis",
        "best_unknown": "Bester Preis (Zustand unbekannt)",
        "best_plus_shipping": "zzgl. Versand",
        "best_aggregate_prefix": "ab",
        "comparison_header": "Preisvergleich (ungeprüft)",
        "comparison_new": "neu ab {price}",
        "comparison_used": "B-Ware/Gebraucht ab {price}",
        "comparison_count": "{count} Angebote",
        "comparison_hint": "Aggregatpreis ohne Versand; nicht trackbar.",
        "comparison_verdict_note": "Ein günstigerer Vergleichspreis steht oben (ungeprüft).",
        "verdict": "Fazit",
        "below_average": "{pct} unter dem eigenen Schnitt ({average})",
        "above_average": "{pct} über dem eigenen Schnitt ({average})",
        "below_average_short": "{pct} unter dem eigenen Schnitt",
        "above_average_short": "{pct} über dem eigenen Schnitt",
        "lowest_recorded": "niedrigster erfasster Preis",
        "lowest_variant": "der niedrigste erfasste Preis für diese Variante",
        "web_verdict_note": "Ein günstigerer Web-Fund steht oben (ungeprüft).",
        "low_was": "Tief war {price}",
        "price_falling": "Preis fallend",
        "new_costs_more": "Neu kostet {amount} mehr",
        "like_new_costs_more": "Wie neu kostet {amount} mehr",
        "like_new_short": "wie neu {amount} mehr",
        "like_new_compromise": "Wie neu ist meist der beste Kompromiss.",
        "unrated": "ohne Zustand",
        "footer": "Preise aus dem Pricewatcha-Katalog; maßgeblich ist die Händlerseite.",
        "search_header": 'Suche „{query}“ — {count} Treffer:',
        "alerts_header": "Deine Preisalarme:",
        "alerts_empty": "Keine Preisalarme eingerichtet.",
        "alert_updated": "Preisalarm #{id} aktualisiert",
        "watchlist_header": "Deine Watchlist:",
        "watchlist_empty": "Noch nichts auf der Watchlist.",
        "history_header": "Preisverlauf: {name}",
        "history_stats": "Tief {low} · Ø {average} · Hoch {high}",
        "history_trend": "Trend: {trend}",
        "history_empty": "Kein Preisverlauf für dieses Produkt.",
        "product_header": "Produkt: {name}",
        "product_line": "{price} · {shop} · {status}",
        "product_checked": "Zuletzt geprüft: {when}",
        "status_header": "Pricewatcha-Status",
        "status_watchlist": "Watchlist: {count} Produkte",
        "status_tracked": "Getrackte Web-Angebote: {count}",
        "status_last_update": "Letzte Aktualisierung: {when}",
        "status_ai": "KI-Ausgaben: {today} heute · {month} diesen Monat",
        "status_ai_off": "KI: aus",
        "status_api": "Pricewatcha-API: {state}",
        "status_ok": "erreichbar",
        "status_failed": "nicht erreichbar",
        "alert_created": "Preisalarm #{id} angelegt",
        "alert_details": "Preisalarm #{id}",
        "unwatched": "{name} wird nicht mehr beobachtet.",
        "refreshed": "Watchlist aktualisiert.",
        "ref_not_found": "In den letzten Ergebnissen nicht gefunden. Zuerst /deal oder /search ausführen.",
        "ref_ambiguous": "Das passt auf mehrere Einträge; bitte eine Nummer aus der Liste verwenden.",
        "alert_deleted": "Preisalarm #{id} gelöscht.",
        "alert_not_found": "Preisalarm #{id} nicht gefunden.",
        "action_tools_unsupported": "Dieser KI-Anbieter kann keine Tools aufrufen. Nutze stattdessen /watch, /alert, /history und die anderen Befehle.",

        "on_any_drop": "bei jedem Preisrutsch",
        "on_any_rise": "bei jedem Anstieg",
        "min": "min. {price}",
        "max": "max. {price}",
        "no_condition": "keine Bedingung",
        "paused": "(pausiert)",
        "tracking_submitted": "Tracking gestartet: {status}",
        "tracking_failed": "Tracking fehlgeschlagen: {code} — {message}",
        "tracking_running": "Job {job} läuft noch; der Preis erscheint beim nächsten Abruf.",
        "price_drop": "Preisrutsch: {name}",
        "was": "war {price}",
        "at": "bei {shop}",
        "usage": "Nutzung: {command} {example}",
        "ai_label": "Einschätzung",
        "ai_answer": "Antwort",
        "ai_unavailable": "KI nicht verfügbar",
        "sources": "Quellen",
        "track_hint": "Zum Beobachten: /track 1",
        "offers_header": "Web-Angebote aus der letzten Antwort:",
        "offers_empty": "Keine Web-Angebote gespeichert. Zuerst /ask ausführen, dann /track <Nummer>.",
        "offers_hint": "Eines mit /track <Nummer> beobachten oder eine URL einfügen.",
        "track_not_trackable": "Das ist ein Aggregatpreis, kein Shop-Angebot; öffne stattdessen die Quelle.",
        "tracked_added": "Getrackt als #{id}",
        "tracked_exists": "Bereits getrackt als #{id}",
        "tracked_none": "Noch keine externen Angebote getrackt. Nach /deal mit /track 1 starten.",
        "tracked_list_header": "Getrackte Web-Angebote:",
        "tracked_alerts_empty": "Keine Alarme für Web-Angebote.",
        "tracked_target": "Ziel {price}",
        "tracked_drop": "jede Senkung",
        "tracked_untracked": "Tracking von #{id} beendet.",
        "tracked_history_header": "Verlauf #{id}",
        "tracked_history_empty": "Noch keine Snapshots.",
        "tracked_alert_set": "Alarm für #{id}: {detail}",
        "tracked_alert_removed": "Alarm für #{id} entfernt.",
        "tracked_drop_title": "Preisalarm: {title}",
        "tracked_drop_target": "Ziel {target} erreicht",
        "tracked_drop_fall": "Preis gefallen",
        "tracked_last_checked": "zuletzt geprüft {when}",

        "today": "heute",
    },
}

_CONDITIONS: dict[str, dict[str, str]] = {
    "very good": {"en": "very good", "de": "sehr gut"},
    "like new": {"en": "like new", "de": "wie neu"},
    "good": {"en": "good", "de": "gut"},
    "new": {"en": "new", "de": "neu"},
    "grade A": {"en": "grade A", "de": "Zustand A"},
    "grade B": {"en": "grade B", "de": "Zustand B"},
    "grade C": {"en": "grade C", "de": "Zustand C"},
    "refurbished": {"en": "refurbished", "de": "generalüberholt"},
    "used": {"en": "used", "de": "gebraucht"},
    "damaged": {"en": "damaged", "de": "beschädigt"},
}


def _lang(language: str | None) -> str:
    return language if language in _STRINGS else "en"


def _t(language: str | None, key: str, **values: Any) -> str:
    text = _STRINGS[_lang(language)][key]
    return text.format(**values) if values else text


def _condition(value: Any, language: str | None) -> str:
    label = str(value or "")
    if not label:
        return _t(language, "unrated")
    return _CONDITIONS.get(label, {}).get(_lang(language), label)


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


def _money(value: Any, currency: str | None, language: str | None = "en") -> str:
    """A price a human reads at a glance: ``669 €``, ``1 019 €``, ``949,50 €``."""
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
    if _lang(language) == "de" and "." in shown:
        shown = shown.replace(".", ",")
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


def _percent(value: Any, language: str | None = "en") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    shown = f"{number:.1f}".rstrip("0").rstrip(".")
    if _lang(language) == "de":
        return shown.replace(".", ",") + " %"
    return shown + "%"


def format_help(language: str = "en") -> str:
    lines = [
        _t(language, "help_header"),
        "",
        _t(language, "help_deal"),
        _t(language, "help_ask"),
        _t(language, "help_search"),
        _t(language, "help_track"),
        _t(language, "help_untrack"),
        _t(language, "help_trackalert"),
        _t(language, "help_trackalerts"),
        _t(language, "help_watch"),
        _t(language, "help_unwatch"),
        _t(language, "help_watchlist"),
        _t(language, "help_history"),
        _t(language, "help_product"),
        _t(language, "help_status"),
        _t(language, "help_refresh"),
        _t(language, "help_alerts"),
        _t(language, "help_alert"),
        _t(language, "help_reset"),
        "",
        _t(language, "help_free"),
    ]
    return "\n".join(lines)


def _deal_lines(index: int, deal: dict[str, Any], language: str) -> list[str]:
    currency = deal.get("currency")
    title = (
        f"{index}. {_money(deal.get('price'), currency, language)} · "
        f"{_esc(deal.get('shop') or '?')}"
    )
    if deal.get("condition"):
        title += f" · {_esc(_condition(deal['condition'], language))}"
    lines = [f"<b>{title}</b>"]
    if deal.get("name"):
        lines.append(_esc(deal["name"]))

    meta: list[str] = []
    discount = deal.get("vs_average_pct")
    if discount is not None:
        average = deal.get("average_price")
        key = "below_average" if float(discount) >= 0 else "above_average"
        meta.append(
            _t(
                language,
                key,
                pct=_percent(abs(float(discount)), language),
                average=_money(_whole(average), currency, language),
            )
        )
    low = deal.get("historical_low")
    if low is not None:
        if deal.get("vs_low_pct") is not None and float(deal["vs_low_pct"]) >= 0:
            meta.append(_t(language, "lowest_recorded"))
        else:
            meta.append(_t(language, "low_was", price=_money(low, currency, language)))
    if deal.get("trend") == "downward":
        meta.append(_t(language, "price_falling"))
    if meta:
        lines.append(" · ".join(meta))

    history = deal.get("history") or []
    if index == 1 and history:
        points = " → ".join(
            f"{_date(point.get('date'))} {_money(point.get('price'), currency, language)}"
            for point in history
        )
        lines.append(f"{_t(language, 'price_history')}: {points}")

    if deal.get("url"):
        lines.append(
            f'<a href="{_esc_attr(deal["url"])}">{_t(language, "see_offer")}</a>'
        )
    return lines


def _group_lines(groups: list[dict[str, Any]], language: str) -> list[str]:
    lines: list[str] = []
    for group in groups:
        offers = group.get("offers") or []
        if not offers:
            continue
        parts = [
            f"{_esc(_condition(offer.get('condition'), language))} "
            f"{_money(offer.get('price'), offer.get('currency'), language)}"
            for offer in offers
        ]
        capacity = group.get("capacity") or "other"
        label = "Other" if capacity == "other" and _lang(language) == "en" else capacity
        if capacity == "other" and _lang(language) == "de":
            label = "Andere"
        lines.append(f"{_esc(label)}: " + " · ".join(parts))
    return lines


def _verdict(
    deals: list[dict[str, Any]], groups: list[dict[str, Any]], language: str
) -> str | None:
    """One data-backed sentence about the ranked best deal, not the absolute cheapest.

    A grade-C bargain is not the answer to "best deal"; the ranking already says which
    offer is the best value. The verdict explains that pick and what a better condition
    would add, using the same-capacity group behind it.
    """
    if not deals:
        return None

    top = deals[0]
    currency = top.get("currency")
    text = _money(top.get("price"), currency, language)
    if top.get("condition"):
        text += f" ({_esc(_condition(top['condition'], language))})"
    if top.get("shop"):
        text += f" {_t(language, 'at', shop=_esc(top['shop']))}"

    discount = top.get("vs_average_pct")
    try:
        if discount is not None and float(discount) >= 0:
            text += ", " + _t(
                language,
                "below_average_short",
                pct=_percent(discount, language),
            )
        if float(top.get("vs_low_pct")) >= 0:
            text += ", " + _t(language, "lowest_variant")
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
        text += " " + _t(
            language, "new_costs_more", amount=_money(_whole(new_extra), currency, language)
        )
        if like_new_extra is not None:
            text += ", " + _t(
                language,
                "like_new_short",
                amount=_money(_whole(like_new_extra), currency, language),
            )
        text += "."
    elif like_new_extra is not None:
        text += " " + _t(
            language,
            "like_new_costs_more",
            amount=_money(_whole(like_new_extra), currency, language),
        ) + "."

    if (
        new_extra is not None
        and like_new_extra is not None
        and top.get("condition") not in ("new", "like new")
    ):
        text += " " + _t(language, "like_new_compromise")

    return text


def format_verdict(payload: dict[str, Any], language: str = "en") -> str | None:
    """The deterministic verdict on its own, used when the AI paragraph fails."""
    verdict = _verdict(
        payload.get("deals") or [], payload.get("groups") or [], language
    )
    if not verdict:
        return None
    return f"<b>{_t(language, 'verdict')}:</b> {verdict}"


def format_deals(
    payload: dict[str, Any],
    language: str = "en",
    *,
    include_verdict: bool = True,
) -> str:
    query = payload.get("query") or ""
    deals = payload.get("deals") or []
    if not deals:
        return _t(language, "nothing_matched", query=_esc(query))

    compared = payload.get("compared") or len(deals)
    matched = payload.get("matched")
    if isinstance(matched, int) and matched > compared:
        counts = _t(
            language, "matches_compared", matched=matched, compared=compared
        )
    else:
        counts = _t(language, "compared", compared=compared)

    lines = [f"<b>{_esc(query)}</b> — {counts}", ""]
    best = [
        item for item in (payload.get("best_by_condition") or []) if isinstance(item, dict)
    ]
    if best:
        labels = {
            "new": "best_new",
            "refurbished": "best_refurbished",
            "used": "best_used",
            "unknown": "best_unknown",
        }
        lines.append(f"<b>{_t(language, 'best_offers_header')}</b>")
        for item in best:
            offer = item.get("offer") if isinstance(item, dict) else None
            if not isinstance(offer, dict) or offer.get("price") is None:
                continue
            label = _t(language, labels.get(str(item.get("condition")), "best_unknown"))
            prefix = ""
            extra = offer.get("extra") or {}
            if isinstance(extra, dict) and extra.get("aggregate"):
                prefix = f"{_t(language, 'best_aggregate_prefix')} "
            detail = (
                f"{prefix}{_money(offer.get('price'), offer.get('currency'), language)}"
                f" · {_esc(offer.get('shop') or '?')}"
            )
            if not offer.get("shipping_known"):
                detail += f" · {_t(language, 'best_plus_shipping')}"
            lines.append(f"{label}: <b>{detail}</b>")
        lines.append("")

    for index, deal in enumerate(deals, start=1):
        lines.extend(_deal_lines(index, deal, language))
        if index < len(deals):
            lines.append("")

    groups = payload.get("groups") or []
    group_lines = _group_lines(groups, language)
    if group_lines:
        lines += ["", f"<b>{_t(language, 'prices_by_condition')}</b>", *group_lines]

    web_deals = [
        item for item in (payload.get("web_deals") or []) if isinstance(item, dict)
    ]
    if web_deals:
        lines += ["", f"<b>{_t(language, 'web_deals_header')}</b>"]
        for index, offer in enumerate(web_deals[:3], start=1):
            price = _money(offer.get("price"), offer.get("currency"), language)
            shop = _esc(offer.get("shop") or "?")
            detail = f"{price} · {shop}"
            if offer.get("condition"):
                detail += f" · {_esc(_condition(offer['condition'], language))}"
            lines.append(f"{index}. <b>{detail}</b>")
            title = _esc(offer.get("title") or offer.get("url") or "")
            url = str(offer.get("url") or "")
            if url.startswith("http"):
                lines.append(f'<a href="{_esc_attr(url)}">{title}</a>')
            else:
                lines.append(title)
        lines += ["", f"<i>{_t(language, 'web_deals_hint')}</i>"]

    comparisons = [
        item for item in (payload.get("comparisons") or []) if isinstance(item, dict)
    ]
    for comparison in comparisons[:2]:
        lines += ["", f"<b>{_t(language, 'comparison_header')}</b>"]
        url = str(comparison.get("url") or "")
        parts = []
        if comparison.get("new_price") is not None:
            part = _t(
                language,
                "comparison_new",
                price=_money(
                    comparison.get("new_price"),
                    comparison.get("new_currency"),
                    language,
                ),
            )
            if comparison.get("new_count"):
                part += " · " + _t(
                    language,
                    "comparison_count",
                    count=comparison["new_count"],
                )
            parts.append(part)
        if comparison.get("used_price") is not None:
            parts.append(
                _t(
                    language,
                    "comparison_used",
                    price=_money(
                        comparison.get("used_price"),
                        comparison.get("used_currency"),
                        language,
                    ),
                )
            )
        label = _esc(str(comparison.get("source") or "idealo"))
        if url.startswith("http"):
            lines.append(
                f'<a href="{_esc_attr(url)}">{label}</a>: ' + " · ".join(parts)
            )
        else:
            lines.append(f"{label}: " + " · ".join(parts))
        lines += ["", f"<i>{_t(language, 'comparison_hint')}</i>"]

    if include_verdict:
        verdict = _verdict(deals, groups, language)
        if verdict:
            catalog_prices = [
                float(deal["price"])
                for deal in deals
                if deal.get("price") is not None
            ] + [
                float(offer["price"])
                for group in groups
                for offer in (group.get("offers") or [])
                if offer.get("price") is not None
            ]
            web_prices = [
                float(offer["price"])
                for offer in web_deals
                if offer.get("price") is not None
            ]
            comparison_prices = [
                float(comparison[key])
                for comparison in comparisons
                for key in ("new_price", "used_price")
                if comparison.get(key) is not None
            ]
            if (
                (web_prices or comparison_prices)
                and catalog_prices
                and min(web_prices + comparison_prices) < min(catalog_prices)
            ):
                verdict += " " + (
                    _t(language, "web_verdict_note")
                    if web_prices
                    else _t(language, "comparison_verdict_note")
                )
            lines += ["", f"<b>{_t(language, 'verdict')}:</b> {verdict}"]

    partial = payload.get("partial") or {}
    missing = []
    if partial.get("details"):
        missing.append("offer links" if _lang(language) == "en" else "Angebotslinks")
    if partial.get("history"):
        missing.append("price history" if _lang(language) == "en" else "Preisverlauf")
    if missing:
        if partial.get("rate_limited"):
            reason = (
                " (Limit erreicht)" if _lang(language) == "de" else " (rate limit)"
            )
        else:
            reason = ""
        if _lang(language) == "de":
            note = f"Ein Teil ({', '.join(missing)}) konnte nicht geladen werden{reason}; bitte gleich noch einmal versuchen."
        else:
            note = f"Some {' and '.join(missing)} could not be loaded{reason}; try again in a moment."
        lines += ["", f"<i>{_esc(note)}</i>"]

    lines += ["", f"<i>{_t(language, 'footer')}</i>"]
    return "\n".join(lines)


def format_search(payload: dict[str, Any], language: str = "en") -> str:
    query = payload.get("query") or ""
    results = payload.get("results") or []
    if not results:
        return _t(language, "nothing_matched", query=_esc(query))

    lines = [
        _t(
            language,
            "search_header",
            query=_esc(query),
            count=payload.get("count") or len(results),
        )
    ]
    for index, result in enumerate(results, start=1):
        name = _esc(result.get("name") or result.get("product_id") or "?")
        price = _money(result.get("current_price"), result.get("currency"), language)
        shop = _esc(result.get("shop") or "?")
        lines.append(f"{index}. {name} — {price} · {shop}")
        if result.get("product_url"):
            url = result["product_url"]
            lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
        lines.append("")
    return "\n".join(lines).strip()


def format_alerts(payload: dict[str, Any], language: str = "en") -> str:
    alerts = payload.get("alerts") or []
    if not alerts:
        return _t(language, "alerts_empty")

    lines = [_t(language, "alerts_header")]
    for alert in alerts:
        name = _esc(alert.get("name") or alert.get("product_id") or "?")
        parts = []
        if alert.get("notify_on_drop"):
            parts.append(_t(language, "on_any_drop"))
        if alert.get("notify_on_rise"):
            parts.append(_t(language, "on_any_rise"))
        if alert.get("min_threshold_price"):
            parts.append(
                _t(
                    language,
                    "min",
                    price=_money(
                        alert["min_threshold_price"], alert.get("currency"), language
                    ),
                )
            )
        if alert.get("max_threshold_price"):
            parts.append(
                _t(
                    language,
                    "max",
                    price=_money(
                        alert["max_threshold_price"], alert.get("currency"), language
                    ),
                )
            )
        state = "" if alert.get("is_active", True) else f" {_t(language, 'paused')}"
        detail = ", ".join(parts) if parts else _t(language, "no_condition")
        lines.append(f"#{alert.get('alert_id')} {name} — {detail}{state}")
    return "\n".join(lines)


def format_track(payload: dict[str, Any], language: str = "en") -> str:
    status = payload.get("status") or "unknown"
    product = payload.get("product") or {}
    error = payload.get("error") or {}

    if status == "completed" and product:
        lines = [_t(language, "tracking_submitted", status=_esc(status))]
        lines.append(
            f"{_esc(product.get('name') or '?')} — "
            f"{_money(product.get('current_price'), product.get('currency'), language)}"
        )
        if product.get("product_url"):
            url = product["product_url"]
            lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
        return "\n".join(lines)

    if status == "failed":
        return _t(
            language,
            "tracking_failed",
            code=_esc(error.get("code") or "unknown"),
            message=_esc(error.get("message") or ""),
        ).strip()

    return (
        _t(language, "tracking_submitted", status=_esc(status))
        + "\n"
        + _t(language, "tracking_running", job=_esc(payload.get("job_id") or "?"))
    )


def format_price_drop(data: dict[str, Any], language: str = "en") -> str:
    name = _esc(data.get("name") or data.get("product_id") or "?")
    currency = data.get("currency")
    lines = [
        _t(language, "price_drop", name=name),
        f"{_money(data.get('new_price'), currency, language)} "
        f"({_t(language, 'was', price=_money(data.get('old_price'), currency, language))}, "
        f"{_percent(data.get('change_pct'), language)}) "
        f"{_t(language, 'at', shop=_esc(data.get('shop') or '?'))}",
    ]
    if data.get("product_url"):
        url = data["product_url"]
        lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
    return "\n".join(lines)


def format_usage(command: str, example: str, language: str = "en") -> str:
    return _t(language, "usage", command=command, example=example)


def format_ai_section(
    result: Any,
    language: str = "de",
    *,
    answer: bool = False,
) -> str:
    """The optional AI paragraph: label, grounded text, and what it cost."""
    german = _lang(language) == "de"
    label = _t(language, "ai_answer" if answer else "ai_label")
    model = getattr(result, "model", "") or "AI"
    spent = float(getattr(result, "spent_today", 0.0) or 0.0)
    spent_text = f"{spent:.4f}".replace(".", ",") if german else f"{spent:.4f}"
    footer = f"{model} · {spent_text} $ {_t(language, 'today')}"

    lines = [
        f"<b>{label}</b>",
        _esc(getattr(result, "text", "")),
    ]
    citations = [
        citation
        for citation in (getattr(result, "citations", None) or [])
        if isinstance(citation, dict) and str(citation.get("url") or "").startswith("http")
    ]
    if citations:
        lines += ["", f"<b>{_t(language, 'sources')}</b>"]
        seen: set[str] = set()
        number = 0
        for citation in citations[:4]:
            url = str(citation["url"])
            if url in seen:
                continue
            seen.add(url)
            number += 1
            title = str(citation.get("title") or url)
            lines.append(f'{number}. <a href="{_esc_attr(url)}">{_esc(title)}</a>')
        if number:
            lines += ["", f"<i>{_t(language, 'track_hint')}</i>"]
    lines += ["", f"<i>{_esc(footer)}</i>"]
    return "\n".join(lines)


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
        "tools_unsupported": "Dieser Anbieter kann keine Tools aufrufen; nutze die Befehle.",
        "action_tools_unsupported": "Dieser Anbieter kann keine Tools aufrufen; nutze /watch, /alert, /history und die anderen Befehle.",
        "unknown_action": "Diese Aktion habe ich nicht verstanden.",
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
        "tools_unsupported": "This provider cannot call tools; use the commands.",
        "action_tools_unsupported": "This provider cannot call tools; use /watch, /alert, /history and the other commands.",
        "unknown_action": "I did not understand that action.",
    },
}


def format_ai_unavailable(reason: str, language: str = "de") -> str:
    german = _lang(language) == "de"
    label = _t(language, "ai_unavailable")
    text = _AI_REASONS["de" if german else "en"].get(reason, reason)
    return f"<b>{label}</b>\n{_esc(text)}"


def format_photo_caption(
    deal: dict[str, Any],
    language: str = "en",
    page_condition: str | None = None,
) -> str:
    """Short caption under the product photo: price, shop, condition, name."""
    currency = deal.get("currency")
    title = f"{_money(deal.get('price'), currency, language)} · {_esc(deal.get('shop') or '?')}"
    condition = deal.get("condition") or page_condition
    if condition:
        title += f" · {_esc(_condition(condition, language))}"
    lines = [f"<b>{title}</b>"]
    if deal.get("name"):
        lines.append(_esc(deal["name"]))
    return truncate("\n".join(lines), 900)


def format_reset(language: str = "en") -> str:
    return _t(language, "reset_done")


def _alert_body(alert: dict[str, Any], language: str) -> str:
    name = _esc(alert.get("name") or alert.get("product_id") or "?")
    parts = []
    if alert.get("notify_on_drop"):
        parts.append(_t(language, "on_any_drop"))
    if alert.get("notify_on_rise"):
        parts.append(_t(language, "on_any_rise"))
    if alert.get("min_threshold_price"):
        parts.append(
            _t(
                language,
                "min",
                price=_money(
                    alert["min_threshold_price"], alert.get("currency"), language
                ),
            )
        )
    if alert.get("max_threshold_price"):
        parts.append(
            _t(
                language,
                "max",
                price=_money(
                    alert["max_threshold_price"], alert.get("currency"), language
                ),
            )
        )
    detail = ", ".join(parts) if parts else _t(language, "no_condition")
    if not alert.get("is_active", True):
        detail += f" {_t(language, 'paused')}"
    return f"{name} — {detail}"


def format_alert_updated(alert: dict[str, Any], language: str = "en") -> str:
    """Confirmation of an edited alert, using whatever the API returned."""
    alert_id = alert.get("alert_id") or alert.get("id") or "?"
    header = _t(language, "alert_updated", id=_esc(alert_id))
    return f"<b>{header}</b>\n{_alert_body(alert, language)}"


def format_alert_created(alert: dict[str, Any], language: str = "en") -> str:
    alert_id = alert.get("alert_id") or alert.get("id") or "?"
    header = _t(language, "alert_created", id=_esc(alert_id))
    return f"<b>{header}</b>\n{_alert_body(alert, language)}"


def format_alert_details(alert: dict[str, Any], language: str = "en") -> str:
    alert_id = alert.get("alert_id") or alert.get("id") or "?"
    header = _t(language, "alert_details", id=_esc(alert_id))
    return f"<b>{header}</b>\n{_alert_body(alert, language)}"


def format_offer_list(offers: list[dict[str, Any]], language: str = "en") -> str:
    """The web offers stored after /ask, numbered for /track N."""
    usable = [offer for offer in offers if str(offer.get("url") or "").startswith("http")]
    if not usable:
        return _t(language, "offers_empty")
    lines = [f"<b>{_t(language, 'offers_header')}</b>"]
    for index, offer in enumerate(usable[:10], start=1):
        url = str(offer["url"])
        title = str(offer.get("title") or url)
        lines.append(f'{index}. <a href="{_esc_attr(url)}">{_esc(title)}</a>')
    lines += ["", f"<i>{_t(language, 'offers_hint')}</i>"]
    return "\n".join(lines)


def format_watchlist(products: list[dict[str, Any]], language: str = "en") -> str:
    if not products:
        return _t(language, "watchlist_empty")
    lines = [f"<b>{_t(language, 'watchlist_header')}</b>"]
    for index, product in enumerate(products, start=1):
        name = _esc(product.get("name") or product.get("product_id") or "?")
        price = _money(
            product.get("current_price", product.get("price")),
            product.get("currency"),
            language,
        )
        shop = _esc(product.get("shop") or "?")
        lines.append(f"{index}. {name} — {price} · {shop}")
    return "\n".join(lines)


def format_history(
    payload: dict[str, Any], name: str, language: str = "en"
) -> str:
    points = payload.get("data_points") or []
    if not points:
        return _t(language, "history_empty")
    currency = payload.get("currency")
    lines = [f"<b>{_t(language, 'history_header', name=_esc(name))}</b>"]
    lines.append(
        _t(
            language,
            "history_stats",
            low=_money(payload.get("historical_low"), currency, language),
            average=_money(
                _whole(payload.get("average_price")), currency, language
            ),
            high=_money(payload.get("historical_high"), currency, language),
        )
    )
    trend = str(payload.get("trend") or "unknown")
    trend_labels = {
        "downward": "fallend" if _lang(language) == "de" else "falling",
        "upward": "steigend" if _lang(language) == "de" else "rising",
        "stable": "stabil" if _lang(language) == "de" else "stable",
    }
    lines.append(
        _t(
            language,
            "history_trend",
            trend=trend_labels.get(trend, trend),
        )
    )
    series = " → ".join(
        f"{_date(point.get('date'))} {_money(point.get('price'), currency, language)}"
        for point in points[-6:]
        if isinstance(point, dict)
    )
    if series:
        lines.append(series)
    return "\n".join(lines)


def format_product(product: dict[str, Any], language: str = "en") -> str:
    name = _esc(product.get("name") or product.get("product_id") or "?")
    lines = [f"<b>{_t(language, 'product_header', name=name)}</b>"]
    lines.append(
        _t(
            language,
            "product_line",
            price=_money(product.get("current_price"), product.get("currency"), language),
            shop=_esc(product.get("shop") or "?"),
            status=_esc(product.get("status") or "?"),
        )
    )
    checked = str(product.get("last_checked_at") or "").replace("T", " ")[:16]
    if checked:
        lines.append(_t(language, "product_checked", when=_esc(checked)))
    if product.get("product_url"):
        url = product["product_url"]
        lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
    return "\n".join(lines)


def format_status(data: dict[str, Any], language: str = "en") -> str:
    lines = [f"<b>{_t(language, 'status_header')}</b>"]
    lines.append(_t(language, "status_watchlist", count=data.get("watchlist", 0)))
    if data.get("tracked") is not None:
        lines.append(_t(language, "status_tracked", count=data.get("tracked", 0)))
    if data.get("last_update"):
        lines.append(
            _t(language, "status_last_update", when=_esc(data["last_update"]))
        )
    if data.get("ai_enabled"):
        lines.append(
            _t(
                language,
                "status_ai",
                today=data.get("ai_today") or "0",
                month=data.get("ai_month") or "0",
            )
        )
    else:
        lines.append(_t(language, "status_ai_off"))
    state = (
        _t(language, "status_ok")
        if data.get("api_ok")
        else _t(language, "status_failed")
    )
    lines.append(_t(language, "status_api", state=state))
    return "\n".join(lines)


def format_unwatched(name: str, language: str = "en") -> str:
    return _t(language, "unwatched", name=_esc(name))


def format_refreshed(language: str = "en") -> str:
    return _t(language, "refreshed")


def format_ref_not_found(language: str = "en") -> str:
    return _t(language, "ref_not_found")


def format_ref_ambiguous(language: str = "en") -> str:
    return _t(language, "ref_ambiguous")


def format_alert_deleted(alert_id: Any, language: str = "en") -> str:
    return _t(language, "alert_deleted", id=_esc(alert_id))


def format_alert_not_found(alert_id: Any, language: str = "en") -> str:
    return _t(language, "alert_not_found", id=_esc(alert_id))


def format_chat_reply(text: str, language: str = "de") -> str:
    label = "Antwort" if _lang(language) == "de" else "Answer"
    return f"<b>{label}</b>\n{_esc(text)}"


def format_action_error(reason: str, language: str = "de") -> str:
    return format_ai_unavailable(reason, language)


def format_not_trackable(language: str = "en") -> str:
    return _t(language, "track_not_trackable")


def _tracked_line(offer: dict[str, Any], language: str, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    title = _esc(offer.get("title") or offer.get("url") or "?")
    price = _money(offer.get("last_total"), offer.get("currency"), language)
    shop = _esc(offer.get("shop") or "?")
    condition = _esc(_condition(offer.get("condition"), language))
    details = []
    if offer.get("target_total") is not None:
        details.append(
            _t(
                language,
                "tracked_target",
                price=_money(offer.get("target_total"), offer.get("currency"), language),
            )
        )
    if offer.get("notify_on_drop"):
        details.append(_t(language, "tracked_drop"))
    suffix = f" · {' · '.join(details)}" if details else ""
    return (
        f"{prefix}<code>#{_esc(offer.get('id'))}</code> {title} — "
        f"{price} · {shop} · {condition}{suffix}"
    )


def format_tracked_added(offer: dict[str, Any], language: str = "en") -> str:
    return (
        f"<b>{_t(language, 'tracked_added', id=_esc(offer.get('id')))}</b>\n"
        + _tracked_line(offer, language)
    )


def format_tracked_exists(offer: dict[str, Any], language: str = "en") -> str:
    return _t(language, "tracked_exists", id=_esc(offer.get("id")))


def format_tracked_list(offers: list[dict[str, Any]], language: str = "en") -> str:
    if not offers:
        return _t(language, "tracked_none")
    lines = [f"<b>{_t(language, 'tracked_list_header')}</b>"]
    for index, offer in enumerate(offers[:10], start=1):
        lines.append(_tracked_line(offer, language, index))
    return "\n".join(lines)


def format_tracked_untracked(offer_id: str, language: str = "en") -> str:
    return _t(language, "tracked_untracked", id=_esc(offer_id))


def format_tracked_history(offer: dict[str, Any], language: str = "en") -> str:
    snapshots = offer.get("snapshots") or []
    lines = [
        f"<b>{_t(language, 'tracked_history_header', id=_esc(offer.get('id')))}</b>",
        _tracked_line(offer, language),
    ]
    if not snapshots:
        lines.append(_t(language, "tracked_history_empty"))
        return "\n".join(lines)
    points = [
        f"{str(item.get('at') or '')[:10]} "
        f"{_money(item.get('total', item.get('price')), offer.get('currency'), language)}"
        for item in snapshots[-10:]
        if isinstance(item, dict)
    ]
    lines.append(" → ".join(points))
    return "\n".join(lines)


def format_tracked_alert(offer: dict[str, Any], language: str = "en") -> str:
    details = []
    if offer.get("target_total") is not None:
        details.append(
            _t(
                language,
                "tracked_target",
                price=_money(offer.get("target_total"), offer.get("currency"), language),
            )
        )
    if offer.get("notify_on_drop"):
        details.append(_t(language, "tracked_drop"))
    detail = " · ".join(details) if details else _t(language, "no_condition")
    return _t(language, "tracked_alert_set", id=_esc(offer.get("id")), detail=detail)


def format_tracked_drop(
    event: dict[str, Any], language: str = "en"
) -> str:
    offer = event.get("offer") or {}
    old = _money(event.get("old_total"), offer.get("currency"), language)
    new = _money(event.get("new_total"), offer.get("currency"), language)
    reason = (
        _t(
            language,
            "tracked_drop_target",
            target=_money(offer.get("target_total"), offer.get("currency"), language),
        )
        if event.get("reason") == "target"
        else _t(language, "tracked_drop_fall")
    )
    lines = [
        f"<b>{_t(language, 'tracked_drop_title', title=_esc(offer.get('title') or '?'))}</b>",
        f"{old} → <b>{new}</b> · {_esc(offer.get('shop') or '?')}",
        f"<i>{reason}</i>",
    ]
    url = str(offer.get("url") or "")
    if url.startswith("http"):
        lines.append(f'<a href="{_esc_attr(url)}">{_esc(url)}</a>')
    return "\n".join(lines)


def format_tracked_alerts(offers: list[dict[str, Any]], language: str = "en") -> str:
    if not offers:
        return _t(language, "tracked_alerts_empty")
    return format_tracked_list(offers, language)
