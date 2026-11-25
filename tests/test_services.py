"""Service registration and tracked-offer service calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.pricewatcha.const import DOMAIN
from custom_components.pricewatcha.services import (
    SERVICE_CREATE_OFFER_ALERT,
    SERVICE_DELETE_OFFER_ALERT,
    SERVICE_LIST_TRACKED_OFFERS,
    SERVICE_OFFER_HISTORY,
    SERVICE_TRACK_OFFER,
    SERVICE_UNTRACK_OFFER,
    async_setup_services,
    async_unload_services,
)


class Registry:
    def __init__(self):
        self.handlers = {}
        self.removed = []

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, **kwargs):
        self.handlers[(domain, service)] = handler

    def async_remove(self, domain, service):
        self.removed.append((domain, service))
        self.handlers.pop((domain, service), None)


class Hass:
    def __init__(self):
        self.services = Registry()
        self.data = {f"{DOMAIN}_trackers": {}}


class Tracker:
    def __init__(self):
        self.offers = {}

    async def async_add(self, **kwargs):
        offer = {
            "id": "abc123",
            "chat_id": kwargs["chat_id"],
            "url": kwargs["url"],
            "title": kwargs["title"],
            "shop": kwargs["shop"],
            "last_total": kwargs["price"],
            "target_total": kwargs.get("target_total"),
            "notify_on_drop": kwargs.get("notify_on_drop", False),
            "active": True,
            "snapshots": [],
        }
        self.offers["abc123"] = offer
        return offer, True

    async def async_remove(self, offer_id, chat_id=None):
        return self.offers.pop(offer_id, None) is not None

    def list(self, chat_id=None):
        return list(self.offers.values())

    def get(self, offer_id, chat_id=None):
        return self.offers.get(offer_id)

    async def async_set_alert(self, offer_id, **kwargs):
        offer = self.offers.get(offer_id)
        if offer is None:
            return None
        offer["target_total"] = kwargs.get("target_total")
        offer["notify_on_drop"] = kwargs.get("notify_on_drop", False)
        return offer


def run(coro):
    return asyncio.run(coro)


def test_tracked_offer_services_are_registered_and_work():
    async def scenario():
        hass = Hass()
        tracker = Tracker()
        hass.data[f"{DOMAIN}_trackers"]["entry"] = tracker
        await async_setup_services(hass)

        for service in (
            SERVICE_TRACK_OFFER,
            SERVICE_UNTRACK_OFFER,
            SERVICE_LIST_TRACKED_OFFERS,
            SERVICE_OFFER_HISTORY,
            SERVICE_CREATE_OFFER_ALERT,
            SERVICE_DELETE_OFFER_ALERT,
        ):
            assert (DOMAIN, service) in hass.services.handlers

        result = await hass.services.handlers[(DOMAIN, SERVICE_TRACK_OFFER)](
            SimpleNamespace(
                data={
                    "url": "https://shop.example/p",
                    "title": "iPhone",
                    "shop": "Shop",
                    "price": 799,
                    "chat_id": 1,
                }
            )
        )
        assert result["created"] is True
        assert result["offer"]["id"] == "abc123"

        listed = await hass.services.handlers[(DOMAIN, SERVICE_LIST_TRACKED_OFFERS)](
            SimpleNamespace(data={"chat_id": 1})
        )
        assert listed["count"] == 1

        history = await hass.services.handlers[(DOMAIN, SERVICE_OFFER_HISTORY)](
            SimpleNamespace(data={"offer_id": "abc123", "chat_id": 1})
        )
        assert history["url"] == "https://shop.example/p"

        alerted = await hass.services.handlers[(DOMAIN, SERVICE_CREATE_OFFER_ALERT)](
            SimpleNamespace(
                data={"offer_id": "abc123", "target_total": 700, "chat_id": 1}
            )
        )
        assert alerted["target_total"] == 700.0

        cleared = await hass.services.handlers[(DOMAIN, SERVICE_DELETE_OFFER_ALERT)](
            SimpleNamespace(data={"offer_id": "abc123", "chat_id": 1})
        )
        assert cleared["target_total"] is None

        removed = await hass.services.handlers[(DOMAIN, SERVICE_UNTRACK_OFFER)](
            SimpleNamespace(data={"offer_id": "abc123", "chat_id": 1})
        )
        assert removed["removed"] is True

        async_unload_services(hass)
        assert not hass.services.handlers

    run(scenario())
