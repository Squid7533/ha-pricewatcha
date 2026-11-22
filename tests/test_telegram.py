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
from custom_components.pricewatcha.ai import AIResult
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
        self.reject_html = False

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
        self.updated_alerts: list[tuple[int, dict]] = []

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


class FakeAdvisor:
    """Duck-typed stand-in for AIAdvisor."""

    def __init__(
        self, text="669 € ist ein guter Preis.", available=True, error=None, web_available=False
    ):
        self.available = available
        self.web_available = web_available
        self.language = "de"
        self.text = text
        self.error = error
        self.last_error = None
        self.calls: list[tuple[dict, str | None]] = []
        self.previous: list[str | None] = []
        self.web_calls: list[bool] = []
        self.history_calls: list[list[dict[str, str]] | None] = []

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
        )


class FakeCoordinator:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.refreshes = 0
        self.deal_calls = 0

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


async def make_frontend(hass=None, min_drop=2.0, advisor=None, photos=False):
    hass = hass or FakeHass()
    coordinator = FakeCoordinator()
    frontend = TelegramFrontend(
        hass, coordinator, min_drop, advisor=advisor, photos=photos
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
    assert names == ["deal", "ask", "search", "track", "watch", "alerts", "alert", "reset", "help"]


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
