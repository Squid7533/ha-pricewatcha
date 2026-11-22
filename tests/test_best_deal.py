"""Tests for the deal ranking logic.

These import the integration package, so Home Assistant must be installed in the
environment (CI installs it before running pytest).
"""

from __future__ import annotations

import asyncio

from custom_components.pricewatcha.best_deal import (
    build_query,
    find_best_deals,
    looks_like_accessory,
    variant_of,
)


class FakeClient:
    """Minimal stand-in for PricewatchaClient."""

    def __init__(self, rows, history=None):
        self.rows = rows
        self.history = history or {}
        self.last_query = None

    async def search(self, query, limit=50):
        self.last_query = query
        return self.rows

    async def price_history(self, product_id):
        return self.history.get(product_id, {})


def device(product_id, name, price, shop="Shop"):
    return {
        "product_id": product_id,
        "name": name,
        "shop": shop,
        "current_price": price,
        "currency": "EUR",
        "product_url": f"https://example.com/{product_id}",
    }


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
        device("a", "iPhone 17 Pro • 256 GB", 900, "ShopA"),
        device("b", "iPhone 17 Pro • 128 GB", 500, "ShopB"),
        device("c", "iPhone 17 Pro Hülle", 20, "ShopC"),
        device("b-dup", "iPhone 17 Pro • 128 GB", 500, "ShopB"),
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
