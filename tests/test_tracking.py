"""Tests for the persistent tracked-offer store and alert engine."""

from __future__ import annotations

import asyncio

import custom_components.pricewatcha.tracking as tracking
from custom_components.pricewatcha.media import PageOffer
from custom_components.pricewatcha.tracking import TrackedOfferManager


class MemoryStore:
    def __init__(self, data=None):
        self.data = data
        self.saves = 0

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data
        self.saves += 1


class FakeClient:
    async def product(self, product_id):
        return {
            "product_id": product_id,
            "name": "Catalog product",
            "current_price": 80,
            "currency": "EUR",
            "product_url": "https://shop.example/catalog",
        }


def run(coro):
    return asyncio.run(coro)


def test_add_persists_across_a_reload():
    store = MemoryStore()

    async def scenario():
        manager = TrackedOfferManager(None, FakeClient(), store=store)
        await manager.async_load()
        offer, created = await manager.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone",
            shop="Shop",
            price=799,
        )
        assert created is True
        assert offer["id"] in store.data["offers"]

        restarted = TrackedOfferManager(None, FakeClient(), store=store)
        await restarted.async_load()
        assert restarted.find(1, "https://shop.example/p")["last_price"] == 799

    run(scenario())


def test_refresh_records_snapshot_and_notifies_only_once(monkeypatch):
    store = MemoryStore()
    notifications = []

    async def notify(chat_id, offer, event):
        notifications.append(event)

    async def fake_page(url, query=""):
        return PageOffer(
            title="iPhone", price=90, currency="EUR", condition="new", url=url
        )

    monkeypatch.setattr(tracking, "fetch_page_offer", fake_page)

    async def scenario():
        manager = TrackedOfferManager(None, FakeClient(), store=store)
        await manager.async_load()
        manager._notify = notify
        offer, _ = await manager.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone",
            shop="Shop",
            price=100,
            notify_on_drop=True,
        )
        event = await manager.async_refresh(offer["id"])
        assert event["reason"] == "drop"
        assert event["new_total"] == 90
        assert len(offer["snapshots"]) == 2

        assert await manager.async_refresh(offer["id"]) is None
        assert len(notifications) == 1

    run(scenario())


def test_target_alert_fires_at_target_and_on_a_further_drop(monkeypatch):
    store = MemoryStore()
    notifications = []

    async def notify(chat_id, offer, event):
        notifications.append(event)

    prices = iter([95, 89, 89, 84])
    async def fake_page(url, query=""):
        return PageOffer(title="iPhone", price=next(prices), currency="EUR", url=url)

    monkeypatch.setattr(tracking, "fetch_page_offer", fake_page)

    async def scenario():
        manager = TrackedOfferManager(None, FakeClient(), store=store)
        await manager.async_load()
        manager._notify = notify
        offer, _ = await manager.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone",
            shop="Shop",
            price=100,
            target_total=90,
        )
        assert await manager.async_refresh(offer["id"]) is None
        event = await manager.async_refresh(offer["id"])
        assert event["reason"] == "target"
        assert await manager.async_refresh(offer["id"]) is None
        event = await manager.async_refresh(offer["id"])
        assert event["reason"] == "target"
        assert len(notifications) == 2

    run(scenario())


def test_restart_does_not_repeat_a_notification(monkeypatch):
    store = MemoryStore()
    notifications = []

    async def notify(chat_id, offer, event):
        notifications.append(event)

    async def fake_page(url, query=""):
        return PageOffer(title="iPhone", price=90, currency="EUR", url=url)

    monkeypatch.setattr(tracking, "fetch_page_offer", fake_page)

    async def scenario():
        manager = TrackedOfferManager(None, FakeClient(), store=store)
        await manager.async_load()
        manager._notify = notify
        offer, _ = await manager.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone",
            shop="Shop",
            price=100,
            notify_on_drop=True,
        )
        await manager.async_refresh(offer["id"])

        restarted = TrackedOfferManager(None, FakeClient(), store=store)
        await restarted.async_load()
        restarted._notify = notify
        assert await restarted.async_refresh(offer["id"]) is None
        assert len(notifications) == 1

    run(scenario())


def test_remove_stops_refresh():
    store = MemoryStore()

    async def scenario():
        manager = TrackedOfferManager(None, FakeClient(), store=store)
        await manager.async_load()
        offer, _ = await manager.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone",
            shop="Shop",
            price=100,
        )
        assert await manager.async_remove(offer["id"], 1) is True
        assert manager.list(1) == []
        assert manager.get(offer["id"], 1) is None

    run(scenario())
