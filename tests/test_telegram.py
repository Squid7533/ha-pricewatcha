"""Tests for the Telegram front-end: routing, authorisation and thresholds.

The front-end only touches a small part of Home Assistant (event bus, services,
task creation, Store), so it is driven here with minimal fakes instead of a full hass.
"""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from custom_components.pricewatcha import telegram as telegram_module
from custom_components.pricewatcha.ai import AIPlan, AIResult, AIToolCall
from custom_components.pricewatcha.media import ProductMedia
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
        self.wrap_schema = False
        self.unreadable_schema = False
        self.reject_html = False

    def has_service(self, domain: str, service: str) -> bool:
        return self.available

    def async_services(self):
        if self.unreadable_schema:
            return {"telegram_bot": {"send_message": SimpleNamespace(schema=None)}}
        markers = {field: None for field in self.chat_fields}
        schema = SimpleNamespace(schema=markers)
        if self.wrap_schema:
            # `vol.All(cv.deprecated("timeout"), vol.Schema({...}))` and its probatio
            # twin: the field mapping sits one `.validators` hop down.
            schema = SimpleNamespace(validators=[SimpleNamespace(), schema])
        return {"telegram_bot": {"send_message": SimpleNamespace(schema=schema)}}

    async def async_call(self, domain, service, data, blocking=False):
        if self.reject_html and data.get("parse_mode") == "html":
            raise RuntimeError("Bad Request: can't parse entities")
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


class FakeConfig:
    def path(self, *parts):
        return os.path.join("/tmp/ha-pricewatcha-test", *parts)


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()
        self.config = FakeConfig()
        self.data: dict = {}

    def async_create_task(self, coro):
        return asyncio.create_task(coro)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeStore:
    values: dict[str, dict] = {}

    def __init__(self, hass, version: int, key: str) -> None:
        self.key = key

    async def async_load(self):
        return FakeStore.values.get(self.key)

    async def async_save(self, data):
        FakeStore.values[self.key] = data


class FakeClient:
    def __init__(self) -> None:
        self.tracked: list[str] = []
        self.watched: list[str] = []
        self.unwatched: list[str] = []
        self.updated_alerts: list[tuple[int, dict]] = []
        self.created_alerts: list[dict] = []
        self.deleted_alerts: list[int] = []
        self.alerts_list: list[dict] = []

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
            "product": {
                "product_id": "prod_tracked",
                "name": "iPhone 15 Pro",
                "current_price": 489,
                "currency": "EUR",
            },
        }

    async def watch(self, product_id):
        self.watched.append(product_id)
        return {"product_id": product_id, "watching": True}

    async def unwatch(self, product_id):
        self.unwatched.append(product_id)
        return {"product_id": product_id, "watching": False}

    async def price_history(self, product_id):
        return {
            "product_id": product_id,
            "currency": "EUR",
            "current_price": 669,
            "historical_low": 669,
            "historical_high": 739,
            "average_price": 699,
            "trend": "downward",
            "data_points": [
                {"date": "2026-10-13", "price": 739},
                {"date": "2026-11-18", "price": 669},
            ],
        }

    async def product(self, product_id):
        return {
            "product_id": product_id,
            "name": "iPhone Air",
            "shop": "Asgoodasnew",
            "current_price": 669,
            "currency": "EUR",
            "status": "active",
            "product_url": "https://example.com/a",
            "last_checked_at": "2026-11-21T18:50:37Z",
        }

    async def health(self):
        return {"status": "ok"}

    async def job(self, job_id):
        return {"status": "completed", "product": {"name": "iPhone", "current_price": 669}}

    async def alerts(self):
        return list(self.alerts_list)

    async def create_alert(self, **kwargs):
        self.created_alerts.append(kwargs)
        alert = {
            "alert_id": 1325,
            "name": kwargs.get("name") or "Alert",
            "currency": "EUR",
            "is_active": True,
            "notify_on_drop": bool(kwargs.get("notify_on_drop")),
            "notify_on_rise": bool(kwargs.get("notify_on_rise")),
            "min_threshold_price": kwargs.get("min_threshold_price"),
            "max_threshold_price": kwargs.get("max_threshold_price"),
        }
        return alert

    async def get_alert(self, alert_id):
        for alert in self.alerts_list:
            if alert.get("alert_id") == alert_id:
                return alert
        return {"alert_id": alert_id, "name": "Deal range", "is_active": True}

    async def delete_alert(self, alert_id):
        self.deleted_alerts.append(alert_id)

    async def update_alert(self, alert_id, **changes):
        self.updated_alerts.append((alert_id, changes))
        alert = {
            "alert_id": alert_id,
            "name": "Deal range",
            "currency": "EUR",
            "is_active": True,
            "notify_on_drop": False,
            "notify_on_rise": False,
        }
        alert.update(changes)
        return alert


class FakeTracker:
    def __init__(self):
        self.offers: dict[str, dict] = {}
        self.counter = 0
        self.started = False

    async def async_start(self, notify=None):
        self.started = True

    def async_stop(self):
        self.started = False

    def list(self, chat_id=None):
        return [
            offer
            for offer in self.offers.values()
            if offer.get("active", True)
            and (chat_id is None or offer.get("chat_id") == chat_id)
        ]

    def get(self, offer_id, chat_id=None):
        offer = self.offers.get(offer_id)
        if offer is None or not offer.get("active", True):
            return None
        if chat_id is not None and offer.get("chat_id") != chat_id:
            return None
        return offer

    def find(self, chat_id, url):
        for offer in self.list(chat_id):
            if offer.get("url") == url:
                return offer
        return None

    async def async_add(
        self,
        *,
        chat_id,
        source,
        url,
        title,
        shop,
        price,
        currency="EUR",
        condition="unknown",
        source_id=None,
        query="",
        target_total=None,
        notify_on_drop=False,
        product_id=None,
        watched=False,
    ):
        existing = self.find(chat_id, url)
        if existing:
            return existing, False
        self.counter += 1
        offer = {
            "id": f"t{self.counter}",
            "chat_id": chat_id,
            "source": source,
            "source_id": source_id or url,
            "url": url,
            "title": title,
            "shop": shop,
            "price": price,
            "last_total": price,
            "currency": currency,
            "condition": condition,
            "target_total": target_total,
            "notify_on_drop": notify_on_drop,
            "product_id": product_id,
            "watched": watched,
            "active": True,
            "snapshots": [{"at": "2026-11-25T10:00:00+00:00", "total": price}],
        }
        self.offers[offer["id"]] = offer
        return offer, True

    async def async_remove(self, offer_id, chat_id=None):
        offer = self.get(offer_id, chat_id)
        if offer is None:
            return False
        offer["active"] = False
        return True

    async def async_set_alert(
        self, offer_id, *, target_total=None, notify_on_drop=None, chat_id=None
    ):
        offer = self.get(offer_id, chat_id)
        if offer is None:
            return None
        offer["target_total"] = target_total
        if notify_on_drop is not None:
            offer["notify_on_drop"] = notify_on_drop
        return offer


class FakeAdvisor:
    """Duck-typed stand-in for AIAdvisor."""

    def __init__(
        self,
        text="669 € ist ein guter Preis.",
        available=True,
        error=None,
        web_available=False,
        citations=None,
    ):
        self.available = available
        self.web_available = web_available
        self.language = "de"
        self.text = text
        self.error = error
        self.citations = citations or []
        self.last_error = None
        self.calls: list[tuple[dict, str | None]] = []
        self.previous: list[str | None] = []
        self.web_calls: list[bool] = []
        self.history_calls: list[list[dict[str, str]] | None] = []
        self.plan = None
        self.plan_text = ""
        self.plan_calls: list[tuple[str, dict]] = []

    async def async_plan(self, text, context):
        self.plan_calls.append((text, context))
        return self.plan

    async def async_advise(
        self, payload, question=None, previous=None, *, web=False, history=None
    ):
        self.calls.append((payload, question))
        self.previous.append(previous)
        self.web_calls.append(web)
        self.history_calls.append(history)
        if self.error:
            self.last_error = self.error
            return None
        return AIResult(
            text=self.text,
            model="fake-model",
            cost_usd=0.0001,
            spent_today=0.002,
            citations=self.citations,
        )


class FakeCoordinator:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.refreshes = 0
        self.deal_calls = 0
        self.data: dict = {}

    async def async_find_best_deals(self, query, **kwargs):
        self.deal_calls += 1
        return {**DEALS, "query": query}

    async def async_request_refresh(self):
        self.refreshes += 1


@pytest.fixture(autouse=True)
def patch_store(monkeypatch):
    FakeStore.values = {}
    monkeypatch.setattr(telegram_module, "Store", FakeStore)
    # no real HTTP session exists in these fakes; the Bot API calls are stubbed per test
    monkeypatch.setattr(telegram_module, "async_get_clientsession", lambda hass: None)


def run(coro):
    return asyncio.run(coro)


async def make_frontend(
    hass=None,
    min_drop=2.0,
    advisor=None,
    photos=False,
    web_search=False,
    comparison_portals=False,
    geizhals=False,
    tracker=None,
):
    hass = hass or FakeHass()
    coordinator = FakeCoordinator()
    frontend = TelegramFrontend(
        hass,
        coordinator,
        min_drop,
        advisor=advisor,
        photos=photos,
        web_search=web_search,
        comparison_portals=comparison_portals,
        geizhals=geizhals,
        tracker=tracker,
    )
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
        assert "iphone 17 pro" in data["message"]
        assert "compared" in data["message"]
        assert data["parse_mode"] == "html"

    run(scenario())


def test_free_text_becomes_deal_query():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_text", {"chat_id": 7, "text": "iphone 17 pro"})
        await asyncio.sleep(0.05)

        assert "iphone 17 pro" in hass.services.calls[0][2]["message"]

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
        assert FakeStore.values[telegram_module.STORAGE_KEY] == {"chat_ids": [100]}

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
        assert "899 €" in message

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
    assert names == ["deal", "ask", "track", "alerts", "status", "refresh", "reset", "help"]


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


def test_finds_chat_id_behind_a_wrapped_service_schema():
    """HA 2026.9+ registers the schema as `vol.All(deprecated(...), Schema(...))`.

    Reading only `.schema` of the wrapper found nothing, assumed an old Home Assistant
    and sent `target` - which is what raised the "target is deprecated" repair.
    """
    async def scenario():
        hass = FakeHass()
        hass.services.wrap_schema = True
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        hass.bus.fire("telegram_command", {"chat_id": 7, "command": "/help", "args": []})
        await asyncio.sleep(0.05)

        payload = hass.services.calls[0][2]
        assert payload["chat_id"] == [7]
        assert "target" not in payload

    run(scenario())


def test_prefers_chat_id_when_the_schema_cannot_be_read():
    async def scenario():
        hass = FakeHass()
        hass.services.unreadable_schema = True
        frontend = TelegramFrontend(hass, FakeCoordinator(), 2.0)
        await frontend.async_start()
        hass.bus.fire("telegram_command", {"chat_id": 8, "command": "/help", "args": []})
        await asyncio.sleep(0.05)

        payload = hass.services.calls[0][2]
        assert payload["chat_id"] == [8]
        assert "target" not in payload

    run(scenario())


def test_messages_are_sent_as_html():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_command", {"chat_id": 9, "command": "/deal", "args": ["x"]})
        await asyncio.sleep(0.05)
        payload = hass.services.calls[0][2]
        assert payload["parse_mode"] == "html"
        assert payload["message"].startswith("<b>")

    run(scenario())


def test_a_rejected_html_message_falls_back_to_plain_text():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.services.reject_html = True
        hass.bus.fire("telegram_command", {"chat_id": 9, "command": "/deal", "args": ["x"]})
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 1
        payload = hass.services.calls[0][2]
        assert payload["parse_mode"] == "plain_text"
        assert "<b>" not in payload["message"]

    run(scenario())


def test_deal_appends_an_ai_assessment_after_the_table():
    advisor = FakeAdvisor()

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 2
        assert "verglichen" in hass.services.calls[0][2]["message"]
        assert "Fazit" not in hass.services.calls[0][2]["message"]
        assessment = hass.services.calls[1][2]["message"]
        assert "<b>Einschätzung</b>" in assessment
        assert "669 € ist ein guter Preis." in assessment
        assert "fake-model" in assessment
        assert len(advisor.calls) == 1

    run(scenario())


def test_ask_answers_from_the_last_deal():
    advisor = FakeAdvisor()

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/ask", "args": ["ist", "669", "gut?"]},
        )
        await asyncio.sleep(0.05)

        assert len(advisor.calls) == 2
        payload, question = advisor.calls[1]
        assert payload["query"] == "iphone"
        assert question == "ist 669 gut?"
        assert advisor.previous[1] == "669 € ist ein guter Preis."
        assert "<b>Antwort</b>" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_ask_without_a_deal_explains_the_order():
    advisor = FakeAdvisor()

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)

        assert advisor.calls == []
        assert "Starte zuerst /deal" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_ask_without_ai_points_at_the_options():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)

        message = hass.services.calls[-1][2]["message"]
        assert "AI unavailable" in message
        assert "Configure" in message

    run(scenario())


def test_ask_reports_why_the_ai_failed():
    advisor = FakeAdvisor(error="budget_daily")

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)

        assert "Tagesbudget" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_a_failed_assessment_falls_back_to_the_deterministic_verdict():
    advisor = FakeAdvisor(error="timeout")

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 2
        assert "Fazit" not in hass.services.calls[0][2]["message"]
        assert "<b>Fazit:</b>" in hass.services.calls[1][2]["message"]

    run(scenario())


def test_ask_uses_web_search_when_the_advisor_offers_it():
    advisor = FakeAdvisor(web_available=True)

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["black", "friday?"]})
        await asyncio.sleep(0.05)

        assert advisor.web_calls[-1] is True
        assert advisor.web_calls[0] is False

    run(scenario())


def test_deal_sends_the_product_photo(monkeypatch):
    async def fake_media(page_url):
        assert page_url == "https://example.com/a"
        return ProductMedia(
            image=b"fake-jpeg-bytes",
            content_type="image/jpeg",
            condition="very good",
            title="iPhone",
        )

    monkeypatch.setattr(telegram_module, "fetch_product_media", fake_media)

    async def scenario():
        hass, _, _ = await make_frontend(photos=True)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 2
        domain, service, data = hass.services.calls[1]
        assert (domain, service) == ("telegram_bot", "send_photo")
        assert data["chat_id"] == [1]
        assert data["file"].endswith(".jpg")
        assert "931 €" in data["caption"]
        assert "very good" in data["caption"]

    run(scenario())


def test_photo_is_skipped_when_disabled(monkeypatch):
    async def fake_media(page_url):
        raise AssertionError("should not fetch a photo when disabled")

    monkeypatch.setattr(telegram_module, "fetch_product_media", fake_media)

    async def scenario():
        hass, _, _ = await make_frontend(photos=False)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        assert len(hass.services.calls) == 1

    run(scenario())


def test_photo_is_skipped_when_the_page_has_no_image(monkeypatch):
    async def fake_media(page_url):
        return ProductMedia(condition=None)

    monkeypatch.setattr(telegram_module, "fetch_product_media", fake_media)

    async def scenario():
        hass, _, _ = await make_frontend(photos=True)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        assert len(hass.services.calls) == 1

    run(scenario())


def test_free_text_after_a_deal_is_a_follow_up():
    advisor = FakeAdvisor()

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        lookups = coordinator.deal_calls

        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "ist 669 gut?"})
        await asyncio.sleep(0.05)

        assert coordinator.deal_calls == lookups
        assert advisor.calls[-1][1] == "ist 669 gut?"
        assert "<b>Antwort</b>" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_free_text_without_a_question_still_searches():
    advisor = FakeAdvisor()

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        lookups = coordinator.deal_calls

        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "iphone 15 pro"})
        await asyncio.sleep(0.05)

        assert coordinator.deal_calls == lookups + 1

    run(scenario())


def test_memory_survives_a_restart():
    async def scenario():
        hass, _, frontend = await make_frontend(advisor=FakeAdvisor())
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        frontend.async_stop()

        advisor = FakeAdvisor()
        new_hass = FakeHass()
        new_frontend = TelegramFrontend(
            new_hass, FakeCoordinator(), 2.0, advisor=advisor, language="de"
        )
        await new_frontend.async_start()
        new_hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)

        assert advisor.calls, "the restored deal should feed /ask"
        assert "<b>Antwort</b>" in new_hass.services.calls[-1][2]["message"]

    run(scenario())


def test_reset_clears_the_conversation():
    advisor = FakeAdvisor()

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/reset", "args": []})
        await asyncio.sleep(0.05)
        assert "Gespräch gelöscht" in hass.services.calls[-1][2]["message"]

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)
        assert "Starte zuerst /deal" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_an_expired_conversation_is_not_restored():
    async def scenario():
        hass, _, frontend = await make_frontend(advisor=FakeAdvisor())
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        frontend.async_stop()

        stored = FakeStore.values[telegram_module.CONVERSATION_STORAGE_KEY]
        stored["chats"]["1"]["updated_at"] = (
            time.time() - frontend._memory_ttl - 1
        )

        advisor = FakeAdvisor()
        new_hass = FakeHass()
        new_frontend = TelegramFrontend(
            new_hass, FakeCoordinator(), 2.0, advisor=advisor, language="de"
        )
        await new_frontend.async_start()
        new_hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)

        assert advisor.calls == []
        assert "Starte zuerst /deal" in new_hass.services.calls[-1][2]["message"]

    run(scenario())


def test_question_history_is_passed_to_the_next_ask():
    advisor = FakeAdvisor()

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "ist 669 gut?"})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "und black friday?"})
        await asyncio.sleep(0.05)

        assert advisor.history_calls[1] == []
        history = advisor.history_calls[2]
        assert history and history[0]["q"] == "ist 669 gut?"
        assert history[0]["a"] == "669 € ist ein guter Preis."

    run(scenario())


def test_an_expired_conversation_is_not_used_in_the_same_session():
    advisor = FakeAdvisor()

    async def scenario():
        hass, _, frontend = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        frontend._memory_updated[1] = (
            time.time() - frontend._memory_ttl - 1
        )

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)

        assert "Starte zuerst /deal" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_the_configured_ttl_is_used():
    advisor = FakeAdvisor()

    async def scenario():
        hass = FakeHass()
        frontend = TelegramFrontend(
            hass,
            FakeCoordinator(),
            2.0,
            advisor=advisor,
            language="de",
            conversation_ttl_days=3,
        )
        await frontend.async_start()
        assert frontend._memory_ttl == 3 * 24 * 3600

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        frontend._memory_updated[1] = time.time() - 3.5 * 24 * 3600

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/ask", "args": ["gut?"]})
        await asyncio.sleep(0.05)
        assert "Starte zuerst /deal" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_alert_command_changes_the_minimum_price():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["1324", "min", "620"]},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == [
            (1324, {"min_threshold_price": 620.0})
        ]
        message = hass.services.calls[-1][2]["message"]
        assert "Alert #1324 updated" in message
        assert "620 €" in message

    run(scenario())


def test_alert_command_can_pause_resume_and_toggle_notifications():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["77", "pause"]},
        )
        await asyncio.sleep(0.05)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["77", "drop", "on"]},
        )
        await asyncio.sleep(0.05)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["77", "name", "Black", "Friday"]},
        )
        await asyncio.sleep(0.05)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["77", "min", "off"]},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == [
            (77, {"is_active": False}),
            (77, {"notify_on_drop": True}),
            (77, {"name": "Black Friday"}),
            (77, {"min_threshold_price": None}),
        ]
        assert "(paused)" in hass.services.calls[0][2]["message"]

    run(scenario())


def test_alert_command_explains_the_syntax():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/alert", "args": ["1324"]}
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == []
        assert "Nutzung" not in hass.services.calls[-1][2]["message"]
        assert "Usage: /alert 1324 min 620" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_ask_stores_its_sources_and_track_uses_the_number():
    advisor = FakeAdvisor(
        citations=[
            {"url": "https://shop.example/better", "title": "Better offer"},
            {"url": "https://shop.example/other", "title": "Other offer"},
        ]
    )

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "und black friday?"})
        await asyncio.sleep(0.05)

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/track", "args": ["1"]})
        await asyncio.sleep(0.05)

        assert coordinator.client.tracked == ["https://shop.example/better"]
        assert "Tracking gestartet: completed" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_track_without_an_argument_lists_the_watchlist_and_tracker():
    async def scenario():
        tracker = FakeTracker()
        await tracker.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/better",
            title="Better offer",
            shop="Shop",
            price=799,
        )
        hass, _, _ = await make_frontend(tracker=tracker)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/track", "args": []})
        await asyncio.sleep(0.05)

        message = hass.services.calls[-1][2]["message"]
        assert "Watchlist & tracker" in message
        assert "Better offer" in message
        assert "#t1" in message

    run(scenario())


def test_track_rejects_an_unknown_offer_number_by_showing_the_list():
    advisor = FakeAdvisor(
        citations=[{"url": "https://shop.example/better", "title": "Better offer"}]
    )

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "und black friday?"})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/track", "args": ["9"]})
        await asyncio.sleep(0.05)

        assert coordinator.client.tracked == []
        assert "Web-Angebote aus der letzten Antwort" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_stored_offers_survive_a_restart():
    advisor = FakeAdvisor(
        citations=[{"url": "https://shop.example/better", "title": "Better offer"}]
    )

    async def scenario():
        hass, _, frontend = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "und black friday?"})
        await asyncio.sleep(0.05)
        frontend.async_stop()

        new_hass = FakeHass()
        coordinator = FakeCoordinator()
        new_frontend = TelegramFrontend(
            new_hass, coordinator, 2.0, advisor=FakeAdvisor(), language="en"
        )
        await new_frontend.async_start()
        new_hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/track", "args": ["1"]})
        await asyncio.sleep(0.05)

        assert coordinator.client.tracked == ["https://shop.example/better"]

    run(scenario())


def test_watch_uses_the_last_deal_result_number():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/watch", "args": ["1"]})
        await asyncio.sleep(0.05)

        assert coordinator.client.watched == ["prod_a"]
        assert "Now watching prod_a" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_watchlist_and_unwatch_use_numbers():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        coordinator.data = {
            "prod_a": {
                "name": "iPhone Air",
                "current_price": 669,
                "currency": "EUR",
                "shop": "Asgoodasnew",
            }
        }
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/watchlist", "args": []})
        await asyncio.sleep(0.05)
        message = hass.services.calls[-1][2]["message"]
        assert "1. iPhone Air — 669 € · Asgoodasnew" in message

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/unwatch", "args": ["1"]})
        await asyncio.sleep(0.05)
        assert coordinator.client.unwatched == ["prod_a"]
        assert "no longer watching" in hass.services.calls[-1][2]["message"].lower()

    run(scenario())


def test_history_and_product_use_the_last_result_number():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/history", "args": ["1"]})
        await asyncio.sleep(0.05)
        assert "Price history" in hass.services.calls[-1][2]["message"]
        assert "Low 669 €" in hass.services.calls[-1][2]["message"]

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/product", "args": ["1"]})
        await asyncio.sleep(0.05)
        assert "Product: iPhone Air" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_status_and_refresh():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        coordinator.data = {"prod_a": {"name": "iPhone", "current_price": 669}}
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/status", "args": []})
        await asyncio.sleep(0.05)
        message = hass.services.calls[-1][2]["message"]
        assert "Pricewatcha status" in message
        assert "Watchlist: 1" in message
        assert "reachable" in message

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/refresh", "args": []})
        await asyncio.sleep(0.05)
        assert coordinator.refreshes >= 1
        assert "Watchlist refreshed" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_alert_new_show_and_delete():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        coordinator.client.alerts_list = [
            {
                "alert_id": 1324,
                "name": "Deal range",
                "currency": "EUR",
                "min_threshold_price": 620,
                "is_active": True,
            }
        ]
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["new", "1", "min", "620", "drop"]},
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.created_alerts == [
            {"product_id": "prod_a", "min_threshold_price": 620.0, "notify_on_drop": True}
        ]
        assert "Alert #1325 created" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["show", "1324"]},
        )
        await asyncio.sleep(0.05)
        assert "Alert #1324" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["delete", "1324"]},
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.deleted_alerts == [1324]
        assert "deleted" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_natural_language_watch_uses_the_stored_offer():
    advisor = FakeAdvisor(citations=[{"url": "https://shop.example/better", "title": "Better offer"}])
    advisor.plan = AIPlan(calls=[AIToolCall(name="watch", arguments={"ref": "w1"})])

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]})
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "und black friday?"})
        await asyncio.sleep(0.05)

        hass.bus.fire(
            "telegram_text",
            {"chat_id": 1, "text": "beobachte das angebot von Better offer"},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.tracked == ["https://shop.example/better"]
        assert advisor.plan_calls, "the model should have been asked to plan"
        assert "w1" in str(advisor.plan_calls[-1][1])

    run(scenario())


def test_natural_language_updates_an_alert():
    advisor = FakeAdvisor()
    advisor.plan = AIPlan(
        calls=[AIToolCall(name="update_alert", arguments={"alert_ref": "current", "min": 400})]
    )

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        coordinator.client.alerts_list = [
            {
                "alert_id": 1324,
                "name": "Deal range",
                "currency": "EUR",
                "min_threshold_price": 620,
                "is_active": True,
            }
        ]
        hass.bus.fire(
            "telegram_text",
            {"chat_id": 1, "text": "ändere den preisalarm auf 400 eur"},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == [
            (1324, {"min_threshold_price": 400.0})
        ]

    run(scenario())


def test_natural_language_lists_the_watchlist():
    advisor = FakeAdvisor()
    advisor.plan = AIPlan(calls=[AIToolCall(name="list_watchlist", arguments={})])

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        coordinator.data = {
            "prod_a": {"name": "iPhone Air", "current_price": 669, "shop": "Asgoodasnew"}
        }
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "zeig meine watchlist"})
        await asyncio.sleep(0.05)

        assert "iPhone Air" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_natural_language_reports_a_provider_without_tools():
    advisor = FakeAdvisor()
    advisor.last_error = "tools_unsupported"

    async def scenario():
        hass, _, _ = await make_frontend(advisor=advisor)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "beobachte das angebot"})
        await asyncio.sleep(0.05)

        message = hass.services.calls[-1][2]["message"]
        assert "keine Tools" in message
        assert "watch" in message

    run(scenario())


def test_alert_drop_only_clears_the_thresholds():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["77", "drop", "only"]},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == [
            (
                77,
                {
                    "notify_on_drop": True,
                    "notify_on_rise": False,
                    "min_threshold_price": None,
                    "max_threshold_price": None,
                },
            )
        ]
        message = hass.services.calls[-1][2]["message"]
        assert "on any drop" in message
        assert "min." not in message

    run(scenario())


def test_alert_chained_edit_can_clear_min_explicitly():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alert", "args": ["77", "drop", "on", "min", "off"]},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == [
            (77, {"notify_on_drop": True, "min_threshold_price": None})
        ]

    run(scenario())


def test_natural_language_drop_only_clears_the_value():
    advisor = FakeAdvisor()
    advisor.plan = AIPlan(
        calls=[
            AIToolCall(
                name="update_alert",
                arguments={
                    "alert_ref": "current",
                    "drop": True,
                    "clear_thresholds": True,
                },
            )
        ]
    )

    async def scenario():
        hass, coordinator, _ = await make_frontend(advisor=advisor)
        coordinator.client.alerts_list = [
            {
                "alert_id": 1324,
                "name": "Deal range",
                "currency": "EUR",
                "min_threshold_price": 620,
                "is_active": True,
            }
        ]
        hass.bus.fire(
            "telegram_text",
            {"chat_id": 1, "text": "ändere den preisalarm auf preis fallend ohne wert"},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.updated_alerts == [
            (
                1324,
                {
                    "notify_on_drop": True,
                    "min_threshold_price": None,
                    "max_threshold_price": None,
                },
            )
        ]

    run(scenario())


def test_deal_includes_cheaper_web_finds_and_track_uses_them(monkeypatch):
    import custom_components.pricewatcha.telegram as telegram_module
    from custom_components.pricewatcha.websearch import WebDeal

    async def fake_find(session, query, **kwargs):
        return [
            WebDeal(
                title="Apple iPhone 17 Pro 256 GB",
                url="https://www.mediamarkt.de/de/product/x",
                price=869,
                currency="EUR",
                shop="MediaMarkt",
                condition="new",
            )
        ]

    monkeypatch.setattr(telegram_module, "find_web_deals", fake_find)

    async def scenario():
        hass, coordinator, _ = await make_frontend(web_search=True)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/deal", "args": ["iphone", "17", "pro"]},
        )
        await asyncio.sleep(0.05)

        message = hass.services.calls[-1][2]["message"]
        assert "Web finds (unverified)" in message
        assert "869 € · MediaMarkt · new" in message
        assert "/track 1" in message

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["1"]}
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.tracked == ["https://www.mediamarkt.de/de/product/x"]

    run(scenario())


def test_deal_includes_idealo_comparison_when_enabled(monkeypatch):
    import custom_components.pricewatcha.telegram as telegram_module
    from custom_components.pricewatcha.websearch import ComparisonDeal

    async def fake_comparison(session, query, **kwargs):
        return ComparisonDeal(
            title="Apple iPhone 17 Pro",
            url="https://www.idealo.de/preisvergleich/OffersOfProduct/1.html",
            new_price=1128.99,
            new_count=230,
            used_price=899.0,
        )

    monkeypatch.setattr(telegram_module, "find_idealo_comparison", fake_comparison)

    async def scenario():
        hass, _, _ = await make_frontend(comparison_portals=True)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/deal", "args": ["iphone", "17", "pro"]},
        )
        await asyncio.sleep(0.05)

        message = hass.services.calls[-1][2]["message"]
        assert "Price comparison (unverified)" in message
        assert "new from" in message
        assert "230 offers" in message
        assert "B-Ware/used from 899 €" in message

    run(scenario())


def test_deal_engine_track_selects_the_catalog_offer_when_no_web_find_exists():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/deal", "args": ["iphone", "17", "pro"]},
        )
        await asyncio.sleep(0.05)
        assert "Best offers" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["1"]}
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.watched == ["prod_a"]

    run(scenario())


def test_deal_engine_lists_geizhals_when_enabled(monkeypatch):
    import custom_components.pricewatcha.telegram as telegram_module
    from custom_components.pricewatcha.websearch import ComparisonDeal

    async def fake_geizhals(session, query, **kwargs):
        return ComparisonDeal(
            title="Apple iPhone 17 Pro",
            url="https://geizhals.de/apple-iphone-17-pro-v1.html",
            source="geizhals",
            new_price=1088.65,
        )

    monkeypatch.setattr(telegram_module, "find_geizhals_comparison", fake_geizhals)

    async def scenario():
        hass, _, _ = await make_frontend(geizhals=True)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/deal", "args": ["iphone", "17", "pro"]},
        )
        await asyncio.sleep(0.05)

        message = hass.services.calls[-1][2]["message"]
        assert "Geizhals" in message or "geizhals" in message
        assert "1 088.65" in message or "1088.65" in message

    run(scenario())


def test_track_persists_a_web_offer_in_the_tracker(monkeypatch):
    import custom_components.pricewatcha.telegram as telegram_module
    from custom_components.pricewatcha.websearch import WebDeal

    async def fake_find(session, query, **kwargs):
        return [
            WebDeal(
                title="Apple iPhone 17 Pro",
                url="https://shop.example/p",
                price=869,
                currency="EUR",
                shop="MediaMarkt",
                condition="new",
            )
        ]

    monkeypatch.setattr(telegram_module, "find_web_deals", fake_find)

    async def scenario():
        tracker = FakeTracker()
        hass, _, _ = await make_frontend(web_search=True, tracker=tracker)
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/deal", "args": ["iphone", "17", "pro"]},
        )
        await asyncio.sleep(0.05)
        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["1"]}
        )
        await asyncio.sleep(0.05)

        assert len(tracker.list(1)) == 1
        assert "Tracked as #t1" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_tracked_commands_list_alert_and_history():
    async def scenario():
        tracker = FakeTracker()
        hass, _, _ = await make_frontend(tracker=tracker)
        await tracker.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone Air",
            shop="MediaMarkt",
            price=799,
        )

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/watchlist", "args": []}
        )
        await asyncio.sleep(0.05)
        assert "Tracked external offers" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/trackalert", "args": ["1", "target", "700"]},
        )
        await asyncio.sleep(0.05)
        assert tracker.list(1)[0]["target_total"] == 700.0
        assert "Alert for #t1" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/history", "args": ["1"]}
        )
        await asyncio.sleep(0.05)
        assert "Tracked history #t1" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/untrack", "args": ["1"]}
        )
        await asyncio.sleep(0.05)
        assert tracker.list(1) == []
        assert "Stopped tracking #t1" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_natural_language_track_offer_persists_the_web_offer():
    advisor = FakeAdvisor(
        citations=[{"url": "https://shop.example/better", "title": "Better offer"}]
    )
    advisor.plan = AIPlan(calls=[AIToolCall("track_offer", {"ref": "w1"})])

    async def scenario():
        tracker = FakeTracker()
        hass, coordinator, _ = await make_frontend(advisor=advisor, tracker=tracker)
        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/deal", "args": ["iphone"]}
        )
        await asyncio.sleep(0.05)
        hass.bus.fire("telegram_text", {"chat_id": 1, "text": "und black friday?"})
        await asyncio.sleep(0.05)
        hass.bus.fire(
            "telegram_text",
            {"chat_id": 1, "text": "beobachte das angebot von Better offer"},
        )
        await asyncio.sleep(0.05)

        assert coordinator.client.tracked == ["https://shop.example/better"]
        assert len(tracker.list(1)) == 1
        assert tracker.list(1)[0]["url"] == "https://shop.example/better"

    run(scenario())


def test_natural_language_sets_a_tracked_offer_alert():
    advisor = FakeAdvisor()
    advisor.plan = AIPlan(
        calls=[
            AIToolCall(
                "create_offer_alert",
                {"ref": "t1", "target_total": 700, "drop": True},
            )
        ]
    )

    async def scenario():
        tracker = FakeTracker()
        offer, _ = await tracker.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone Air",
            shop="MediaMarkt",
            price=799,
        )
        hass, _, _ = await make_frontend(advisor=advisor, tracker=tracker)
        hass.bus.fire(
            "telegram_text",
            {"chat_id": 1, "text": "setze den alarm auf 700 euro"},
        )
        await asyncio.sleep(0.05)

        assert tracker.get(offer["id"], 1)["target_total"] == 700.0
        assert tracker.get(offer["id"], 1)["notify_on_drop"] is True

    run(scenario())


def test_track_unified_remove_history_info_and_alert():
    async def scenario():
        tracker = FakeTracker()
        offer, _ = await tracker.async_add(
            chat_id=1,
            source="web",
            url="https://shop.example/p",
            title="iPhone Air",
            shop="MediaMarkt",
            price=799,
        )
        hass, coordinator, _ = await make_frontend(tracker=tracker)
        coordinator.data = {
            "prod_a": {
                "name": "iPhone Air",
                "current_price": 669,
                "currency": "EUR",
                "shop": "Asgoodasnew",
            }
        }

        hass.bus.fire("telegram_command", {"chat_id": 1, "command": "/track", "args": []})
        await asyncio.sleep(0.05)
        message = hass.services.calls[-1][2]["message"]
        assert "1. [Catalog] iPhone Air" in message
        assert "2. [Web <code>#t1</code>]" in message

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["info", "2"]}
        )
        await asyncio.sleep(0.05)
        assert "Tracked web offer #t1" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/track", "args": ["history", "2"]},
        )
        await asyncio.sleep(0.05)
        assert "Tracked history #t1" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/track", "args": ["alert", "2", "target", "700"]},
        )
        await asyncio.sleep(0.05)
        assert tracker.get(offer["id"], 1)["target_total"] == 700.0

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["remove", "1"]}
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.unwatched == ["prod_a"]

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["remove", "2"]}
        )
        await asyncio.sleep(0.05)
        assert tracker.list(1) == []

    run(scenario())


def test_alerts_namespace_edit_and_new():
    async def scenario():
        hass, coordinator, _ = await make_frontend()
        coordinator.client.alerts_list = [
            {
                "alert_id": 1324,
                "name": "Deal range",
                "currency": "EUR",
                "min_threshold_price": 620,
                "is_active": True,
            }
        ]
        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alerts", "args": ["edit", "1324", "min", "600"]},
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.updated_alerts == [(1324, {"min_threshold_price": 600.0})]

        hass.bus.fire(
            "telegram_command",
            {"chat_id": 1, "command": "/alerts", "args": ["new", "prod_a", "min", "620"]},
        )
        await asyncio.sleep(0.05)
        assert coordinator.client.created_alerts

    run(scenario())


def test_track_and_alerts_help():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/track", "args": ["help"]}
        )
        await asyncio.sleep(0.05)
        assert "/track remove 1" in hass.services.calls[-1][2]["message"]

        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/alerts", "args": ["help"]}
        )
        await asyncio.sleep(0.05)
        assert "/alerts edit 1324" in hass.services.calls[-1][2]["message"]

    run(scenario())


def test_search_top_level_command_is_gone():
    async def scenario():
        hass, _, _ = await make_frontend()
        hass.bus.fire(
            "telegram_command", {"chat_id": 1, "command": "/search", "args": ["iphone"]}
        )
        await asyncio.sleep(0.05)
        message = hass.services.calls[-1][2]["message"]
        assert "Pricewatcha commands" in message
        assert "/search" not in message

    run(scenario())
