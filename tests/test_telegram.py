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
        self.chat_fields = {"chat_id"}

    def has_service(self, domain: str, service: str) -> bool:
        return self.available

    def async_services(self):
        markers = {field: None for field in self.chat_fields}
        return {
            "telegram_bot": {
                "send_message": SimpleNamespace(schema=SimpleNamespace(schema=markers))
            }
        }

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


class FakeConfigEntries:
    def __init__(self) -> None:
        self.entries: list = []

    def async_entries(self, domain: str):
        return [entry for entry in self.entries if getattr(entry, "domain", "telegram_bot") == domain]


class FakeEntry:
    def __init__(self, subentries=None, data=None, title="Bot") -> None:
        self.subentries = subentries or {}
        self.data = data or {"api_key": "tok"}
        self.title = title
        self.domain = "telegram_bot"


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()
        self.data: dict = {}

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
    # no real HTTP session exists in these fakes; the Bot API calls are stubbed per test
    monkeypatch.setattr(telegram_module, "async_get_clientsession", lambda hass: None)


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
        assert data["chat_id"] == [42]
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
        assert last["chat_id"] == [200]
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


def test_registers_the_command_menu(monkeypatch):
    calls = []

    async def fake_set_commands(session, token, commands):
        calls.append((token, commands))

    monkeypatch.setattr(telegram_module, "set_bot_commands", fake_set_commands)
    monkeypatch.setattr(telegram_module.ir, "async_create_issue", lambda *a, **k: None)
    monkeypatch.setattr(telegram_module.ir, "async_delete_issue", lambda *a, **k: None)

    async def scenario():
        hass = FakeHass()
        hass.config_entries.entries = [FakeEntry(subentries={"a": object()})]
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        await asyncio.sleep(0.05)

    run(scenario())

    assert len(calls) == 1
    token, commands = calls[0]
    assert token == "tok"
    names = [name for name, _ in commands]
    assert names == ["deal", "search", "track", "watch", "alerts", "help"]


def test_repair_issue_when_no_allowed_chats(monkeypatch):
    created = []
    deleted = []
    monkeypatch.setattr(
        telegram_module.ir, "async_create_issue", lambda *a, **k: created.append(k)
    )
    monkeypatch.setattr(
        telegram_module.ir, "async_delete_issue", lambda *a, **k: deleted.append(a)
    )

    async def fake_set_commands(session, token, commands):
        return None

    monkeypatch.setattr(telegram_module, "set_bot_commands", fake_set_commands)

    async def scenario():
        hass = FakeHass()
        hass.config_entries.entries = [FakeEntry(subentries={})]
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        await asyncio.sleep(0.05)

    run(scenario())

    assert len(created) == 1
    assert created[0]["translation_key"] == "telegram_no_allowed_chat"
    assert created[0]["is_fixable"] is False


def test_repair_issue_cleared_when_a_chat_exists(monkeypatch):
    created = []
    deleted = []
    monkeypatch.setattr(
        telegram_module.ir, "async_create_issue", lambda *a, **k: created.append(k)
    )
    monkeypatch.setattr(
        telegram_module.ir, "async_delete_issue", lambda *a, **k: deleted.append(a)
    )

    async def fake_set_commands(session, token, commands):
        return None

    monkeypatch.setattr(telegram_module, "set_bot_commands", fake_set_commands)

    async def scenario():
        hass = FakeHass()
        hass.config_entries.entries = [FakeEntry(subentries={"chat": object()})]
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        await asyncio.sleep(0.05)

    run(scenario())

    assert created == []
    assert len(deleted) == 1


def test_no_telegram_entry_means_no_issue(monkeypatch):
    created = []
    monkeypatch.setattr(
        telegram_module.ir, "async_create_issue", lambda *a, **k: created.append(k)
    )
    monkeypatch.setattr(
        telegram_module.ir, "async_delete_issue", lambda *a, **k: created.append(k)
    )

    async def scenario():
        hass = FakeHass()
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        await asyncio.sleep(0.05)

    run(scenario())

    assert created == []


def test_uses_chat_id_when_the_service_supports_it():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_command", {"chat_id": 5, "command": "/help", "args": []})
        await asyncio.sleep(0.05)
        payload = hass.services.calls[0][2]
        assert payload["chat_id"] == [5]
        assert "target" not in payload

    run(scenario())


def test_falls_back_to_target_on_older_home_assistant():
    async def scenario():
        hass = FakeHass()
        hass.services.chat_fields = {"target"}
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        hass.bus.fire("telegram_command", {"chat_id": 5, "command": "/help", "args": []})
        await asyncio.sleep(0.05)

        payload = hass.services.calls[0][2]
        assert payload["target"] == [5]
        assert "chat_id" not in payload

    run(scenario())
