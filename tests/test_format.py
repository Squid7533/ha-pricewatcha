"""Tests for the Telegram message formatting."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.pricewatcha.format import (
    format_ai_section,
    format_alert_updated,
    format_offer_list,
    format_photo_caption,
    format_ai_unavailable,
    format_alerts,
    format_deals,
    format_help,
    format_price_drop,
    format_search,
    format_track,
    format_usage,
    to_plain,
    truncate,
)

DEALS = {
    "query": "iphone air wolkenweiß",
    "compared": 10,
    "matched": 14,
    "deals": [
        {
            "product_id": "prod_a",
            "name": "Apple iPhone Air 256GB wolkenweiß",
            "price": 669.0,
            "currency": "EUR",
            "shop": "Asgoodasnew",
            "condition": "very good",
            "capacity": "256 GB",
            "vs_average_pct": 4.3,
            "average_price": 699.0,
            "historical_low": 669.0,
            "vs_low_pct": 0.0,
            "trend": "downward",
            "url": "https://example.com/a?x=1&y=2",
            "history": [
                {"date": "2026-10-13", "price": 739.0},
                {"date": "2026-11-18", "price": 669.0},
            ],
        },
        {
            "product_id": "prod_b",
            "name": "Apple iPhone Air 256GB wolkenweiß",
            "price": 699.0,
            "currency": "EUR",
            "shop": "Asgoodasnew",
            "condition": "like new",
            "capacity": "256 GB",
            "vs_average_pct": 3.9,
            "average_price": 727.0,
            "historical_low": 699.0,
            "vs_low_pct": 0.0,
            "trend": "downward",
            "url": "https://example.com/b",
            "history": [],
        },
    ],
    "groups": [
        {
            "capacity": "256 GB",
            "offers": [
                {"condition": "very good", "price": 669.0, "currency": "EUR", "shop": "Asgoodasnew", "historical_low": 669.0},
                {"condition": "good", "price": 689.0, "currency": "EUR"},
                {"condition": "like new", "price": 699.0, "currency": "EUR"},
                {"condition": "new", "price": 889.0, "currency": "EUR"},
            ],
        },
        {
            "capacity": "512 GB",
            "offers": [
                {"condition": "like new", "price": 819.0, "currency": "EUR"},
            ],
        },
    ],
}


def test_deals_header_counts_matches_and_shortlist():
    text = format_deals(DEALS)
    assert text.split("\n")[0] == "<b>iphone air wolkenweiß</b> — 14 matches · 10 compared"


def test_deals_show_condition_price_and_shop():
    text = format_deals(DEALS)
    assert "<b>1. 669 € · Asgoodasnew · very good</b>" in text
    assert "<b>2. 699 € · Asgoodasnew · like new</b>" in text


def test_deals_show_value_against_own_history():
    text = format_deals(DEALS)
    assert "4.3% below its own average (699 €)" in text
    assert "lowest recorded price" in text
    assert "price falling" in text


def test_deals_show_the_dated_history_for_the_first_deal_only():
    text = format_deals(DEALS)
    assert "Price history: 13.10. 739 € → 18.11. 669 €" in text
    assert text.count("Price history:") == 1


def test_deals_escape_the_url_and_render_a_tappable_offer():
    text = format_deals(DEALS)
    assert 'href="https://example.com/a?x=1&amp;y=2">See offer</a>' in text
    assert "https://example.com/a?x=1&y=2\">" not in text


def test_deals_group_by_capacity_and_condition():
    text = format_deals(DEALS)
    assert "256 GB: very good 669 € · good 689 € · like new 699 € · new 889 €" in text
    assert "512 GB: like new 819 €" in text


def test_deals_verdict_names_the_upcharge_for_new_and_like_new():
    text = format_deals(DEALS)
    assert "<b>Verdict:</b>" in text
    assert "669 € (very good) at Asgoodasnew" in text
    assert "4.3% below its own average" in text
    assert "the lowest price recorded for this variant" in text
    assert "New costs 220 € more, like new 30 € more." in text
    assert "Like new is usually the best compromise." in text


def test_deals_without_history_still_format():
    payload = {
        "query": "iphone 17 pro",
        "compared": 1,
        "matched": 1,
        "deals": [
            {
                "product_id": "prod_x",
                "name": "iPhone 17 Pro",
                "price": 949.5,
                "currency": "EUR",
                "shop": "Backmarket",
                "condition": None,
                "capacity": None,
                "url": None,
            }
        ],
        "groups": [],
    }
    text = format_deals(payload)
    assert "1. 949.50 € · Backmarket" in text
    assert "<b>Verdict:</b> 949.50 € at Backmarket." in text
    assert "Price history" not in text


def test_deals_empty():
    assert format_deals({"query": "nothing", "deals": []}) == 'Nothing matched "nothing".'


def test_names_and_shops_are_escaped():
    payload = {
        "query": "a < b",
        "deals": [
            {
                "name": "iPhone <b>Pro</b> & Co",
                "price": 900,
                "currency": "EUR",
                "shop": "Shop & Sons",
            }
        ],
    }
    text = format_deals(payload)
    assert "iPhone &lt;b&gt;Pro&lt;/b&gt; &amp; Co" in text
    assert "Shop &amp; Sons" in text
    assert "<b>Pro</b>" not in text


def test_search_message():
    payload = {
        "query": "iphone",
        "count": 2,
        "results": [
            {
                "name": "iPhone 17 Pro",
                "current_price": 931,
                "currency": "EUR",
                "shop": "Backmarket",
                "product_url": "https://example.com/a",
            }
        ],
    }
    text = format_search(payload)
    assert text.startswith('Search "iphone" — 2 matches:')
    assert "iPhone 17 Pro — 931 € · Backmarket" in text
    assert "https://example.com/a" in text


def test_alerts_message_with_thresholds_and_paused():
    payload = {
        "alerts": [
            {
                "alert_id": 77,
                "product_id": "prod_x",
                "name": "Deal range",
                "notify_on_drop": True,
                "notify_on_rise": False,
                "min_threshold_price": 499.0,
                "max_threshold_price": 599.0,
                "currency": "EUR",
                "is_active": False,
            }
        ]
    }
    text = format_alerts(payload)
    assert "#77 Deal range" in text
    assert "on any drop" in text
    assert "min 499 €" in text
    assert "max 599 €" in text
    assert text.endswith("(paused)")


def test_alerts_empty():
    assert format_alerts({"alerts": []}) == "No price alerts set."


def test_track_completed():
    payload = {
        "status": "completed",
        "product": {
            "name": "iPhone 15 Pro",
            "current_price": 489,
            "currency": "EUR",
            "product_url": "https://example.com/p",
        },
    }
    text = format_track(payload)
    assert text.startswith("Tracking submitted: completed")
    assert "iPhone 15 Pro — 489 €" in text


def test_track_failed():
    text = format_track(
        {"status": "failed", "error": {"code": "scrape_timeout", "message": "timed out"}}
    )
    assert text == "Tracking failed: scrape_timeout — timed out"


def test_track_running():
    text = format_track({"status": "running", "job_id": "job_1"})
    assert "still running" in text and "job_1" in text


def test_price_drop():
    text = format_price_drop(
        {
            "name": "iPhone Air",
            "new_price": 899,
            "old_price": 981,
            "change_pct": -8.4,
            "currency": "EUR",
            "shop": "Backmarket",
            "product_url": "https://example.com/x",
        }
    )
    lines = text.split("\n")
    assert lines[0] == "Price drop: iPhone Air"
    assert lines[1] == "899 € (was 981 €, -8.4%) at Backmarket"
    assert "https://example.com/x" in lines[2]


def test_truncate_keeps_words_and_marks_cut():
    text = "word " * 2000
    cut = truncate(text, limit=100)
    assert len(cut) <= 100
    assert cut.endswith("…")
    assert "\n" not in cut


def test_truncate_closes_open_tags():
    text = "<b>" + ("word " * 200) + "</b>"
    cut = truncate(text, limit=60)
    assert cut.endswith("…</b>")
    assert cut.count("<b>") == cut.count("</b>")


def test_truncate_leaves_short_text_alone():
    assert truncate("short", limit=100) == "short"


def test_to_plain_strips_tags_and_keeps_the_link_target():
    text = '<b>Hi</b> &amp; <a href="https://x.example/y?z=1&amp;q=2">See offer</a>'
    assert to_plain(text) == "Hi & See offer (https://x.example/y?z=1&q=2)"


def test_help_lists_commands():
    text = format_help()
    for command in (
        "/deal",
        "/ask",
        "/search",
        "/track",
        "/watch",
        "/alerts",
        "/alert",
        "/reset",
    ):
        assert command in text


def test_usage():
    assert format_usage("/deal", "iphone 18 pro") == "Usage: /deal iphone 18 pro"


def test_truncate_closes_tags_without_any_space_to_cut_at():
    cut = truncate("<b>" + "x" * 200, limit=50)
    assert cut.endswith("</b>")
    assert cut.count("<b>") == cut.count("</b>")


def test_partial_data_is_announced():
    payload = {
        "query": "iphone air",
        "compared": 1,
        "matched": 1,
        "deals": [
            {"name": "Apple iPhone Air 256GB", "price": 669, "currency": "EUR", "shop": "Asgoodasnew"}
        ],
        "groups": [],
        "partial": {"details": 1, "history": 0, "rate_limited": True},
    }
    text = format_deals(payload)
    assert "Some offer links could not be loaded (rate limit); try again in a moment." in text


def test_ai_section_is_german_and_escaped():
    result = SimpleNamespace(
        text="669 € sind gut & <b>fair</b>.", model="deepseek-flash", spent_today=0.0021
    )
    text = format_ai_section(result, "de")
    assert "<b>Einschätzung</b>" in text
    assert "669 € sind gut &amp; &lt;b&gt;fair&lt;/b&gt;." in text
    assert "deepseek-flash · 0,0021 $ heute" in text


def test_ai_section_english_and_answer_label():
    result = SimpleNamespace(text="Good.", model="m", spent_today=0.01)
    text = format_ai_section(result, "en", answer=True)
    assert "<b>Answer</b>" in text
    assert "m · 0.0100 $ today" in text


def test_ai_unavailable_is_explained():
    assert "KI nicht verfügbar" in format_ai_unavailable("kein Budget", "de")
    assert "AI unavailable" in format_ai_unavailable("no budget", "en")


def test_german_deal_message_is_fully_translated():
    text = format_deals(DEALS, "de")
    assert "<b>iphone air wolkenweiß</b> — 14 Treffer · 10 verglichen" in text
    assert "<b>1. 669 € · Asgoodasnew · sehr gut</b>" in text
    assert "4,3 % unter dem eigenen Schnitt (699 €)" in text
    assert "niedrigster erfasster Preis" in text
    assert "Preisverlauf: 13.10. 739 € → 18.11. 669 €" in text
    assert "Zum Angebot" in text
    assert "Preise nach Zustand" in text
    assert "256 GB: sehr gut 669 € · gut 689 € · wie neu 699 € · neu 889 €" in text
    assert "<b>Fazit:</b> 669 € (sehr gut) bei Asgoodasnew" in text
    assert "Neu kostet 220 € mehr, wie neu 30 € mehr." in text
    assert "Wie neu ist meist der beste Kompromiss." in text


def test_verdict_can_be_sent_on_its_own():
    from custom_components.pricewatcha.format import format_verdict

    text = format_verdict(DEALS, "en")
    assert text.startswith("<b>Verdict:</b> 669 €")
    assert "New costs 220 € more" in text


def test_ai_section_lists_sources():
    result = SimpleNamespace(
        text="Bei MediaMarkt gab es 799 €.",
        model="deepseek-flash",
        spent_today=0.002,
        citations=[
            {"url": "https://example.com/a", "title": "GIGA"},
            {"url": "https://example.com/b", "title": "mydealz"},
        ],
    )
    text = format_ai_section(result, "de", answer=True)
    assert "<b>Quellen</b>" in text
    assert '1. <a href="https://example.com/a">GIGA</a>' in text
    assert '2. <a href="https://example.com/b">mydealz</a>' in text
    assert "Zum Beobachten: /track 1" in text


def test_photo_caption_has_price_shop_condition_and_name():
    text = format_photo_caption(DEALS["deals"][0], "de")
    assert "<b>669 € · Asgoodasnew · sehr gut</b>" in text
    assert "Apple iPhone Air 256GB wolkenweiß" in text


def test_photo_caption_uses_the_page_condition_as_fallback():
    deal = dict(DEALS["deals"][0])
    deal["condition"] = None
    text = format_photo_caption(deal, "en", page_condition="refurbished")
    assert "refurbished" in text


def test_alert_updated_confirmation():
    text = format_alert_updated(
        {
            "alert_id": 77,
            "name": "Deal range",
            "currency": "EUR",
            "min_threshold_price": 620,
            "is_active": False,
        },
        "de",
    )
    assert "Preisalarm #77 aktualisiert" in text
    assert "620 €" in text
    assert "(pausiert)" in text


def test_offer_list_is_numbered_for_track():
    text = format_offer_list(
        [
            {"url": "https://example.com/a", "title": "Better offer"},
            {"url": "https://example.com/b", "title": "Other"},
        ],
        "de",
    )
    assert "Web-Angebote aus der letzten Antwort" in text
    assert '1. <a href="https://example.com/a">Better offer</a>' in text
    assert "Eines mit /track <Nummer>" in text


def test_empty_offer_list_explains_the_order():
    assert "Zuerst /ask" in format_offer_list([], "de")


def test_deal_shows_web_finds_and_a_cheaper_note():
    payload = {
        "query": "iphone",
        "matched": 2,
        "compared": 2,
        "deals": [
            {
                "product_id": "prod_a",
                "name": "iPhone 17 Pro",
                "price": 931,
                "currency": "EUR",
                "shop": "Backmarket",
                "condition": "refurbished",
                "url": "https://example.com/a",
                "history": [],
            }
        ],
        "groups": [],
        "web_deals": [
            {
                "title": "Apple iPhone 17 Pro 256 GB",
                "url": "https://www.mediamarkt.de/x",
                "price": 869,
                "currency": "EUR",
                "shop": "MediaMarkt",
                "condition": "new",
            }
        ],
    }
    text = format_deals(payload, "de")
    assert "Web-Funde (ungeprüft)" in text
    assert "869 € · MediaMarkt" in text
    assert "günstigerer Web-Fund" in text
    assert "/track 1" in text


def test_deal_shows_idealo_comparison_with_used_price():
    payload = {
        "query": "iphone",
        "matched": 2,
        "compared": 2,
        "deals": [
            {
                "product_id": "prod_a",
                "name": "iPhone Air",
                "price": 669,
                "currency": "EUR",
                "shop": "Asgoodasnew",
                "condition": "refurbished",
                "url": "https://example.com/a",
                "history": [],
            }
        ],
        "groups": [],
        "comparisons": [
            {
                "title": "Apple iPhone Air",
                "url": "https://www.idealo.de/preisvergleich/OffersOfProduct/1.html",
                "new_price": 884.99,
                "new_currency": "EUR",
                "new_count": 28,
                "used_price": 639.13,
                "used_currency": "EUR",
            }
        ],
    }
    text = format_deals(payload, "de")
    assert "Preisvergleich (ungeprüft)" in text
    assert "neu ab 884,99 €" in text
    assert "B-Ware/Gebraucht ab 639,13 €" in text
    assert "28 Angebote" in text
    assert "günstigerer Vergleichspreis" in text
