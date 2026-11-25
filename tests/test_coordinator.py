"""Watchlist paging: the sensor projection must see every watched product."""

from __future__ import annotations

import asyncio

from custom_components.pricewatcha.coordinator import PricewatchaCoordinator


class FakeClient:
    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[tuple[int, int]] = []

    async def watchlist(self, limit=200, offset=0):
        self.calls.append((limit, offset))
        return [
            {"product_id": f"prod_{index}", "product": {}}
            for index in range(offset, min(offset + limit, self.total))
        ]


def _coordinator(total: int) -> PricewatchaCoordinator:
    coordinator = object.__new__(PricewatchaCoordinator)
    coordinator.client = FakeClient(total)
    return coordinator


def test_watchlist_reads_every_page():
    coordinator = _coordinator(450)
    items = asyncio.run(coordinator._async_watchlist())

    assert len(items) == 450
    assert [offset for _, offset in coordinator.client.calls] == [0, 200, 400]


def test_watchlist_stops_after_a_short_page():
    coordinator = _coordinator(120)
    items = asyncio.run(coordinator._async_watchlist())

    assert len(items) == 120
    assert [offset for _, offset in coordinator.client.calls] == [0]
