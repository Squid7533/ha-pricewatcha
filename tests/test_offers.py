"""Tests for the normalized Offer model and source adapters."""

from __future__ import annotations

from custom_components.pricewatcha.offers import (
    Offer,
    OfferSet,
    normalize_condition,
    normalize_title,
)
from custom_components.pricewatcha.sources import build_offer_view


def test_condition_normalization():
    assert normalize_condition("sehr gut") == "refurbished"
    assert normalize_condition("wie neu") == "refurbished"
    assert normalize_condition("gebraucht") == "used"
    assert normalize_condition("Neu") == "new"
    assert normalize_condition(None) == "unknown"


def test_title_key_keeps_variant_tokens():
    assert normalize_title("APPLE iPhone 17 Pro 5G 256 GB Silber") == (
        "iphone 17 pro 256 gb silber"
    )
    assert normalize_title("iPhone 17 Pro • 256 GB") == "iphone 17 pro 256 gb"


def test_offer_total_is_only_computed_when_shipping_is_known():
    known = Offer(
        source="web",
        title="iPhone",
        url="https://shop.example/p",
        shop="Shop",
        price=799,
        shipping=0,
    )
    assert known.total == 799
    assert known.shipping_known is True

    unknown = Offer(
        source="web",
        title="iPhone",
        url="https://shop.example/p",
        shop="Shop",
        price=799,
    )
    assert unknown.total is None
    assert unknown.shipping_known is False
    assert unknown.effective_total == 799


def test_offer_set_dedupes_shop_and_condition_and_keeps_cheapest():
    offers = OfferSet(
        [
            Offer(source="web", title="iPhone Air", url="https://a", shop="Shop", price=799),
            Offer(source="web", title="Apple iPhone Air", url="https://b", shop="shop", price=749),
            Offer(source="web", title="iPhone Air", url="https://c", shop="Shop", price=699, condition="used"),
        ]
    )
    assert len(offers) == 2
    selected = offers.best_by_condition()
    assert selected["unknown"].price == 749
    assert selected["used"].price == 699


def test_build_offer_view_ranks_catalog_and_web_by_condition():
    payload = {
        "query": "iphone air",
        "deals": [
            {
                "product_id": "prod_a",
                "name": "iPhone Air 256 GB",
                "shop": "Asgoodasnew",
                "price": 669,
                "currency": "EUR",
                "condition": "sehr gut",
                "url": "https://asgood.example/a",
            }
        ],
        "groups": [],
    }
    web = [
        {
            "title": "Apple iPhone Air 256 GB",
            "url": "https://mediamarkt.example/a",
            "shop": "MediaMarkt",
            "price": 799,
            "currency": "EUR",
            "condition": "new",
        }
    ]
    comparisons = [
        {
            "title": "Apple iPhone Air",
            "url": "https://idealo.example/a",
            "source": "idealo",
            "new_price": 884.99,
            "new_currency": "EUR",
            "new_count": 28,
            "used_price": 639.13,
            "used_currency": "EUR",
        }
    ]

    offers = build_offer_view(payload, web, comparisons)

    assert len(offers) == 4
    assert payload["best_by_condition"][0]["condition"] == "new"
    assert payload["best_by_condition"][0]["offer"]["price"] == 799
    assert payload["best_by_condition"][1]["condition"] == "refurbished"
    assert payload["best_by_condition"][1]["offer"]["price"] == 669
    assert payload["best_by_condition"][2]["condition"] == "used"
    assert payload["best_by_condition"][2]["offer"]["price"] == 639.13
    assert payload["source_status"][0]["source"] == "pricewatcha"
    assert payload["source_status"][1]["count"] == 1
