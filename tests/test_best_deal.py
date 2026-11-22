"""Tests for the deal ranking logic.

These import the integration package, so Home Assistant must be installed in the
environment (CI installs it before running pytest).
"""

from __future__ import annotations

import asyncio

from custom_components.pricewatcha.best_deal import (
    build_query,
    capacity_of,
    condition_of,
    find_best_deals,
    group_by_capacity,
    looks_like_accessory,
    variant_of,
)


class FakeClient:
    """Minimal stand-in for PricewatchaClient."""

    def __init__(
        self, rows, history=None, details=None, product_error=False, history_error=False
    ):
        self.rows = rows
        self.history = history or {}
        self.details = details or {}
        self.product_error = product_error
        self.history_error = history_error
        self.last_query = None
        self.product_calls: list[str] = []

    async def search(self, query, limit=50):
        self.last_query = query
        return self.rows

    async def product(self, product_id):
        self.product_calls.append(product_id)
        if self.product_error:
            raise RuntimeError("product endpoint down")
        return self.details.get(
            product_id,
            {"product_id": product_id, "product_url": f"https://example.com/{product_id}"},
        )

    async def price_history(self, product_id):
        if self.history_error:
            raise RuntimeError("history endpoint down")
        return self.history.get(product_id, {})


def device(product_id, name, price, shop="Shop", url=None):
    row = {
        "product_id": product_id,
        "name": name,
        "shop": shop,
        "current_price": price,
        "currency": "EUR",
    }
    if url is not None:
        row["product_url"] = url
    return row


def test_accessory_detection():
    assert looks_like_accessory("iPhone 17 Pro Hülle MagSafe")
    assert looks_like_accessory("Panzerglas für iPhone 17 Pro")
    assert looks_like_accessory("ONEFLOW Urban Sling iPhone 17 Pro")
    assert not looks_like_accessory("iPhone 17 Pro • 128 GB • Titan Schwarz")
    assert not looks_like_accessory("Apple iPhone 17 Pro 256GB Tiefblau")


def test_variant_of():
    assert variant_of("iPhone 15 Pro • 256 GB • Grade B") == "256 GB · Grade B"
    assert variant_of("Apple iPhone 15 1TB titan schwarz") == "1 TB"
    assert variant_of("iPhone 17 Pro refurbished") == "refurbished"
    assert variant_of("Something else") == ""


def test_capacity_of():
    assert capacity_of("Apple iPhone Air 256GB wolkenweiß") == "256 GB"
    assert capacity_of("iPhone 15 Pro • 1 TB • Titan") == "1 TB"
    assert capacity_of("iPhone 15 Pro") is None


def test_condition_from_name_and_url_slug():
    assert condition_of("iPhone 15 Pro • Grade B") == "grade B"
    assert condition_of("iPhone 15 Pro refurbished") == "refurbished"
    base = "https://asgoodasnew.de/Handys/Apple/iPhone-Air/"
    assert condition_of("Apple iPhone Air 256GB", base + "x-256GB-wolkenweiss-sehr-gut.html") == "very good"
    assert condition_of("Apple iPhone Air 256GB", base + "x-256GB-wolkenweiss-gut.html") == "good"
    assert condition_of("Apple iPhone Air 256GB", base + "x-256GB-wolkenweiss-wie-neu.html") == "like new"
    assert condition_of("Apple iPhone Air 256GB", base + "x-256GB-wolkenweiss-neu.html") == "new"
    assert condition_of("Apple iPhone Air 256GB") is None


def test_build_query_appends_excludes():
    query = build_query("iphone 17 pro", include_accessories=False)
    assert "-cover" in query and "-hülle" in query
    assert query.startswith("iphone 17 pro")


def test_build_query_does_not_duplicate():
    query = build_query("iphone 17 pro -cover", include_accessories=False)
    assert query.count("-cover") == 1


def test_build_query_untouched_when_accessories_wanted():
    assert build_query("iphone 17 pro", include_accessories=True) == "iphone 17 pro"


def test_filters_accessories_dedupes_and_ranks_by_value():
    rows = [
        device("a", "iPhone 17 Pro • 256 GB", 900, "ShopA", "https://example.com/a"),
        device("b", "iPhone 17 Pro • 128 GB", 500, "ShopB", "https://example.com/b"),
        device("c", "iPhone 17 Pro Hülle", 20, "ShopC", "https://example.com/c"),
        device("b-dup", "iPhone 17 Pro • 128 GB", 500, "ShopB", "https://example.com/b2"),
    ]
    history = {
        "a": {"historical_low": 1000, "average_price": 1000},
        "b": {"historical_low": 480, "average_price": 700},
    }
    client = FakeClient(rows, history)

    result = asyncio.run(
        find_best_deals(client, "iphone 17 pro", limit=3, candidates=5)
    )

    assert result["matched"] == 2, "accessory and duplicate should be gone"
    assert "-cover" in client.last_query
    assert [deal["product_id"] for deal in result["deals"]] == ["b", "a"]
    assert result["deals"][0]["vs_average_pct"] == 28.6


def test_max_price_is_applied():
    rows = [
        device("a", "iPhone 17 Pro • 256 GB", 900),
        device("b", "iPhone 17 Pro • 128 GB", 500),
    ]
    result = asyncio.run(
        find_best_deals(FakeClient(rows), "iphone 17 pro", max_price=600)
    )
    assert [deal["product_id"] for deal in result["deals"]] == ["b"]


def test_survives_missing_history():
    rows = [device("a", "iPhone 17 Pro • 256 GB", 900, "ShopA")]
    result = asyncio.run(find_best_deals(FakeClient(rows), "iphone 17 pro"))
    assert result["deals"][0]["vs_average_pct"] is None
    assert result["deals"][0]["price"] == 900


def test_accessories_can_be_included_on_request():
    rows = [
        device("a", "iPhone 17 Pro • 256 GB", 900),
        device("c", "iPhone 17 Pro Hülle", 20, "ShopC"),
    ]
    result = asyncio.run(
        find_best_deals(FakeClient(rows), "iphone 17 pro", include_accessories=True)
    )
    assert result["matched"] == 2
    assert any("Hülle" in deal["name"] for deal in result["deals"])


def test_search_rows_get_their_url_and_condition_from_the_product_page():
    rows = [device("a", "Apple iPhone Air 256GB wolkenweiß", 669, "Asgoodasnew")]
    details = {
        "a": {
            "product_id": "a",
            "product_url": "https://asgoodasnew.de/x-wolkenweiss-sehr-gut.html",
        }
    }
    client = FakeClient(rows, details=details)

    result = asyncio.run(find_best_deals(client, "iphone air wolkenweiß"))

    assert client.product_calls == ["a"]
    assert result["deals"][0]["url"].endswith("sehr-gut.html")
    assert result["deals"][0]["condition"] == "very good"


def test_a_broken_product_lookup_does_not_lose_the_deal():
    rows = [device("a", "Apple iPhone Air 256GB", 669)]
    result = asyncio.run(
        find_best_deals(FakeClient(rows, product_error=True), "iphone air")
    )
    assert result["deals"][0]["price"] == 669
    assert result["deals"][0]["url"] is None
    assert result["partial"]["details"] == 1
    assert result["partial"]["rate_limited"] is False


def test_groups_keep_the_cheapest_offer_per_condition():
    deals = [
        {"product_id": "a", "capacity": "256 GB", "condition": "very good", "price": 669.0},
        {"product_id": "b", "capacity": "256 GB", "condition": "good", "price": 689.0},
        {"product_id": "c", "capacity": "256 GB", "condition": "like new", "price": 699.0},
        {"product_id": "d", "capacity": "256 GB", "condition": "like new", "price": 720.0},
        {"product_id": "e", "capacity": "256 GB", "condition": "new", "price": 889.0},
        {"product_id": "f", "capacity": "512 GB", "condition": "like new", "price": 819.0},
        {"product_id": "g", "capacity": None, "condition": None, "price": 500.0},
    ]

    groups = group_by_capacity(deals)

    assert [group["capacity"] for group in groups] == ["256 GB", "512 GB", "other"]
    offers = groups[0]["offers"]
    assert [offer["condition"] for offer in offers] == ["very good", "good", "like new", "new"]
    assert [offer["price"] for offer in offers] == [669.0, 689.0, 699.0, 889.0]
    assert [offer["price"] for offer in groups[1]["offers"]] == [819.0]


def test_deals_carry_the_dated_history_for_the_message():
    rows = [device("a", "Apple iPhone Air 256GB", 669)]
    history = {
        "a": {
            "historical_low": 669,
            "historical_high": 739,
            "average_price": 699,
            "trend": "downward",
            "data_points": [
                {"date": "2026-10-13", "price": 739},
                {"date": "2026-11-18", "price": 669},
            ],
        }
    }
    result = asyncio.run(find_best_deals(FakeClient(rows, history), "iphone air"))
    deal = result["deals"][0]
    assert deal["history"] == [
        {"date": "2026-10-13", "price": 739.0},
        {"date": "2026-11-18", "price": 669.0},
    ]
    assert deal["vs_low_pct"] == 0.0


def test_a_broken_history_is_reported_not_hidden():
    rows = [device("a", "Apple iPhone Air 256GB", 669, url="https://example.com/a")]
    result = asyncio.run(
        find_best_deals(FakeClient(rows, history_error=True), "iphone air")
    )
    assert result["deals"][0]["price"] == 669
    assert result["partial"]["history"] == 1


class RateLimitedClient(FakeClient):
    async def product(self, product_id):
        from custom_components.pricewatcha.api import PricewatchaRateLimitError

        raise PricewatchaRateLimitError("rate limited", 60)


def test_a_rate_limited_lookup_is_flagged():
    rows = [device("a", "Apple iPhone Air 256GB", 669)]
    result = asyncio.run(find_best_deals(RateLimitedClient(rows), "iphone air"))
    assert result["partial"]["details"] == 1
    assert result["partial"]["rate_limited"] is True
