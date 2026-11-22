"""Tests for the Telegram front-end: routing, authorisation and thresholds.

The front-end only touches a small part of Home Assistant (event bus, services,
task creation, Store), so it is driven here with minimal fakes instead of a full hass.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.pricewatcha import telegram as telegram_module
from custom_components.pricewatcha.telegram import TelegramFrontend

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
        }
    ],
}


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.available = True

    def has_service(self, domain: str, service: str) -> bool:
        return self.available

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


class FakeBus:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type: str, callback):
        self.listeners.setdefault(event_type, []).append(callback)

        def unsubscribe() -> None:
            self.listeners.get(event_type, []).remove(callback)

        return unsubscribe

    def fire(self, event_type: str, data: dict) -> None:
        for callback in list(self.listeners.get(event_type, [])):
            callback(SimpleNamespace(data=data))


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.services = FakeServices()

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class FakeStore:
    data: dict | None = None

    def __init__(self, hass, version: int, key: str) -> None:
        self.key = key

    async def async_load(self):
        return FakeStore.data

    async def async_save(self, data):
        FakeStore.data = data


class FakeClient:
    def __init__(self) -> None:
        self.tracked: list[str] = []
        self.watched: list[str] = []

    async def search(self, query, limit=50):
        return [
            {
                "name": "iPhone 17 Pro",
                "current_price": 931,
                "currency": "EUR",
                "shop": "Backmarket",
                "product_url": "https://example.com/a",
            }
        ]

    async def track(self, url, watch=False, refresh=False):
        self.tracked.append(url)
        return {
            "status": "completed",
            "product": {"name": "iPhone 15 Pro", "current_price": 489, "currency": "EUR"},
        }

    async def watch(self, product_id):
        self.watched.append(product_id)
        return {"product_id": product_id, "watching": True}

    async def alerts(self):
        return []


class FakeCoordinator:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.refreshes = 0

    async def async_find_best_deals(self, query, **kwargs):
        return {**DEALS, "query": query}

    async def async_request_refresh(self):
        self.refreshes += 1


@pytest.fixture(autouse=True)
def patch_store(monkeypatch):
    FakeStore.data = None
    monkeypatch.setattr(telegram_module, "Store", FakeStore)


def run(coro):
    return asyncio.run(coro)


async def make_frontend(hass=None, min_drop=2.0):
    hass = hass or FakeHass()
    coordinator = FakeCoordinator()
    frontend = TelegramFrontend(hass, coordinator, min_drop)
    await frontend.async_start()
    return hass, coordinator, frontend


def test_deal_command_answers_in_telegram():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 42, "command": "/deal", "args": ["iphone", "17", "pro"]},
        )
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 1
        domain, service, data = hass.services.calls[0]
        assert (domain, service) == ("telegram_bot", "send_message")
        assert data["target"] == [42]
        assert 'Best deals for "iphone 17 pro"' in data["message"]

    run(scenario())


def test_free_text_becomes_deal_query():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_text", {"chat_id": 7, "text": "iphone 17 pro"})
        await asyncio.sleep(0.05)

        assert "Best deals" in hass.services.calls[0][2]["message"]

    run(scenario())


def test_unknown_command_returns_help():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/nonsense", "args": []})
        await asyncio.sleep(0.05)

        assert "/deal" in hass.services.calls[0][2]["message"]

    run(scenario())


def test_track_watches_the_url():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/track", "args": ["https://shop.example/p"]},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.tracked == ["https://shop.example/p"]
        assert "Tracking submitted: completed" in hass.services.calls[-1][2]["message"]
        assert coordinator.refreshes >= 1

    run(scenario())


def test_watch_refreshes_the_coordinator():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/watch", "args": ["prod_abc"]},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.watched == ["prod_abc"]
        assert "Now watching prod_abc" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_first_chat_is_remembered_second_is_refused():
    async def scenario():
        hass, _, frontend = await make_frontend()

        hass.bus.fire("telegram_text", {"chat_id": 100, "text": "iphone"})
        await asyncio.sleep(0.05)
        assert frontend.chat_ids == [100]
        assert FakeStore.data == {"chat_ids": [100]}

        hass.bus.fire("telegram_text", {"chat_id": 200, "text": "iphone"})
        await asyncio.sleep(0.05)

        last = hass.services.calls[-1][2]
        assert last["target"] == [200]
        assert "already connected" in last["message"]
        assert frontend.chat_ids == [100]

    run(scenario())


def test_small_drop_is_not_notified():
    async def scenario():
        hass, _, _ = await make_frontend(min_drop=5.0)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "hello"})
        await asyncio.sleep(0.05)
        hass.services.calls.clear()

        hass.bus.fire(
            "pricewatcha_price_changed",
            {"is_drop": True, "change_pct": -1.2, "name": "iPhone Air", "new_price": 900},
        )
        await asyncio.sleep(0.05)
        assert hass.services.calls == []

    run(scenario())


def test_large_drop_is_notified():
    async def scenario():
        hass, _, _ = await make_frontend(min_drop=2.0)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "hello"})
        await asyncio.sleep(0.05)
        hass.services.calls.clear()

        hass.bus.fire(
            "pricewatcha_price_changed",
            {
                "is_drop": True,
                "change_pct": -8.4,
                "name": "iPhone Air",
                "new_price": 899,
                "old_price": 981,
                "currency": "EUR",
                "shop": "Backmarket",
                "product_url": "https://example.com/x",
            },
        )
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 1
        message = hass.services.calls[0][2]["message"]
        assert message.startswith("Price drop: iPhone Air")
        assert "899 EUR" in message

    run(scenario())


def test_rise_is_never_notified():
    async def scenario():
        hass, _, _ = await make_frontend(min_drop=0.0)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "hello"})
        await asyncio.sleep(0.05)
        hass.services.calls.clear()

        hass.bus.fire(
            "pricewatcha_price_changed",
            {"is_drop": False, "change_pct": 12.0, "name": "iPhone Air", "new_price": 1000},
        )
        await asyncio.sleep(0.05)
        assert hass.services.calls == []

    run(scenario())


def test_missing_telegram_service_does_not_crash():
    async def scenario():
        hass, _, frontend = await make_frontend()
        hass.services.available = False
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/help", "args": []})
        await asyncio.sleep(0.05)

        assert hass.services.calls == []
        assert frontend.chat_ids == [1]

    run(scenario())


def test_stop_removes_listeners():
    async def scenario():
        hass, _, frontend = await make_frontend()
        frontend.async_stop()
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/help", "args": []})
        await asyncio.sleep(0.05)
        assert hass.services.calls == []

    run(scenario())
