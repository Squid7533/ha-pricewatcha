"""Tests for the Telegram message formatting."""

from __future__ import annotations

from custom_components.pricewatcha.format import (
    format_alerts,
    format_deals,
    format_help,
    format_price_drop,
    format_search,
    format_track,
    format_usage,
    truncate,
)

DEALS = {
    "query": "iphone 17 pro",
    "compared": 6,
    "matched": 141,
    "deals": [
        {
            "product_id": "prod_a",
            "name": "iPhone 17 Pro • 256 GB",
            "price": 931.0,
            "currency": "EUR",
            "shop": "Backmarket",
            "vs_average_pct": 7.5,
            "url": "https://example.com/a",
        },
        {
            "product_id": "prod_b",
            "name": "Apple iPhone 17 Pro 256GB",
            "price": 949.5,
            "currency": "EUR",
            "shop": "Asgoodasnew",
            "vs_average_pct": None,
            "url": "https://example.com/b",
        },
    ],
}


def test_deals_message_layout():
    text = format_deals(DEALS)
    lines = text.split("\n")
    assert lines[0] == 'Best deals for "iphone 17 pro" (6 compared):'
    assert lines[1] == "1. iPhone 17 Pro • 256 GB — 931 EUR at Backmarket (7.5% below its average)"
    assert lines[2] == "https://example.com/a"
    assert "2. Apple iPhone 17 Pro 256GB — 949.50 EUR at Asgoodasnew" in text
    assert "below its average)" not in lines[3]


def test_deals_empty():
    assert format_deals({"query": "nothing", "deals": []}) == 'Nothing matched "nothing".'


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
    assert "iPhone 17 Pro — 931 EUR · Backmarket" in text
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
    assert "min 499 EUR" in text
    assert "max 599 EUR" in text
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
    assert "iPhone 15 Pro — 489 EUR" in text


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
    assert lines[1] == "899 EUR (was 981 EUR, -8.4%) at Backmarket"
    assert lines[2] == "https://example.com/x"


def test_truncate_keeps_words_and_marks_cut():
    text = "word " * 2000
    cut = truncate(text, limit=100)
    assert len(cut) <= 100
    assert cut.endswith("…")
    assert "\n" not in cut


def test_truncate_leaves_short_text_alone():
    assert truncate("short", limit=100) == "short"


def test_help_lists_commands():
    text = format_help()
    for command in ("/deal", "/search", "/track", "/watch", "/alerts"):
        assert command in text


def test_usage():
    assert format_usage("/deal", "iphone 18 pro") == "Usage: /deal iphone 18 pro"
