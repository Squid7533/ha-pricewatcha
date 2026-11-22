"""Services: the surface an automation, a script or an LLM chat calls.

These deliberately return data (``supports_response``), because the whole point is
"find the best deal for X" answered inside a conversation, not just a side effect.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import PricewatchaApiError, PricewatchaRateLimitError
from .const import DOMAIN
from .coordinator import PricewatchaCoordinator

_LOGGER = logging.getLogger(__name__)

CONF_ENTRY = "config_entry_id"
ATTR_QUERY = "query"
ATTR_PRODUCT_ID = "product_id"
ATTR_URL = "url"
ATTR_LIMIT = "limit"
ATTR_MAX_PRICE = "max_price"
ATTR_CANDIDATES = "candidates"
ATTR_INCLUDE_ACCESSORIES = "include_accessories"
ATTR_ALERT_ID = "alert_id"

SERVICE_SEARCH = "search"
SERVICE_BEST_DEAL = "best_deal"
SERVICE_PRICE_HISTORY = "price_history"
SERVICE_TRACK = "track"
SERVICE_WATCH = "watch"
SERVICE_UNWATCH = "unwatch"
SERVICE_LIST_ALERTS = "list_alerts"
SERVICE_CREATE_ALERT = "create_alert"
SERVICE_UPDATE_ALERT = "update_alert"
SERVICE_DELETE_ALERT = "delete_alert"
SERVICE_REFRESH = "refresh"

def _coordinator(hass: HomeAssistant, call: ServiceCall) -> PricewatchaCoordinator:
    entries: dict[str, PricewatchaCoordinator] = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("Pricewatcha is not set up")
    entry_id = call.data.get(CONF_ENTRY)
    if entry_id:
        if entry_id not in entries:
            raise HomeAssistantError(f"Unknown config_entry_id {entry_id}")
        return entries[entry_id]
    if len(entries) > 1:
        raise HomeAssistantError("Several Pricewatcha entries are loaded; pass config_entry_id")
    return next(iter(entries.values()))


def _guard(err: Exception) -> HomeAssistantError:
    if isinstance(err, PricewatchaRateLimitError):
        suffix = f" (retry after {err.retry_after}s)" if err.retry_after else ""
        return HomeAssistantError(f"Pricewatcha rate limit{suffix}")
    if isinstance(err, PricewatchaApiError):
        return HomeAssistantError(f"Pricewatcha error: {err}")
    return HomeAssistantError(f"Pricewatcha error: {err}")


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register services once, however many config entries exist."""
    if hass.services.has_service(DOMAIN, SERVICE_SEARCH):
        return

    async def handle_search(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            results = await coordinator.client.search(
                call.data[ATTR_QUERY], limit=int(call.data.get(ATTR_LIMIT, 20))
            )
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err
        return {"query": call.data[ATTR_QUERY], "count": len(results), "results": results}

    async def handle_best_deal(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            return await coordinator.async_find_best_deals(
                call.data[ATTR_QUERY],
                limit=int(call.data.get(ATTR_LIMIT, 5)),
                max_price=call.data.get(ATTR_MAX_PRICE),
                candidates=int(call.data.get(ATTR_CANDIDATES, 10)),
                include_accessories=bool(call.data.get(ATTR_INCLUDE_ACCESSORIES, False)),
            )
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err

    async def handle_price_history(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            return await coordinator.client.price_history(call.data[ATTR_PRODUCT_ID])
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err

    async def handle_track(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            result = await coordinator.client.track(
                call.data[ATTR_URL],
                watch=bool(call.data.get("watch", False)),
                refresh=bool(call.data.get("refresh", False)),
            )
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err
        if call.data.get("watch"):
            await coordinator.async_request_refresh()
        return result

    async def handle_watch(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            result = await coordinator.client.watch(call.data[ATTR_PRODUCT_ID])
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err
        await coordinator.async_request_refresh()
        return result

    async def handle_unwatch(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            result = await coordinator.client.unwatch(call.data[ATTR_PRODUCT_ID])
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err
        await coordinator.async_request_refresh()
        return result

    async def handle_list_alerts(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        try:
            alerts = await coordinator.client.alerts(call.data.get(ATTR_PRODUCT_ID))
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err
        return {"count": len(alerts), "alerts": alerts}

    async def handle_create_alert(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        body = {key: value for key, value in call.data.items() if key != CONF_ENTRY}
        try:
            return await coordinator.client.create_alert(**body)
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err

    async def handle_update_alert(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass, call)
        body = {
            key: value
            for key, value in call.data.items()
            if key not in (CONF_ENTRY, ATTR_ALERT_ID)
        }
        try:
            return await coordinator.client.update_alert(
                int(call.data[ATTR_ALERT_ID]), **body
            )
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err

    async def handle_delete_alert(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        try:
            await coordinator.client.delete_alert(int(call.data[ATTR_ALERT_ID]))
        except Exception as err:  # noqa: BLE001
            raise _guard(err) from err

    async def handle_refresh(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        handle_search,
        schema=vol.Schema(
            {
                vol.Optional(CONF_ENTRY): cv.string,
                vol.Required(ATTR_QUERY): cv.string,
                vol.Optional(ATTR_LIMIT, default=20): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BEST_DEAL,
        handle_best_deal,
        schema=vol.Schema(
            {
                vol.Optional(CONF_ENTRY): cv.string,
                vol.Required(ATTR_QUERY): cv.string,
                vol.Optional(ATTR_LIMIT, default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
                vol.Optional(ATTR_CANDIDATES, default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Optional(ATTR_MAX_PRICE): vol.Coerce(float),
                vol.Optional(ATTR_INCLUDE_ACCESSORIES, default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PRICE_HISTORY,
        handle_price_history,
        schema=vol.Schema({vol.Optional(CONF_ENTRY): cv.string, vol.Required(ATTR_PRODUCT_ID): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRACK,
        handle_track,
        schema=vol.Schema(
            {
                vol.Optional(CONF_ENTRY): cv.string,
                vol.Required(ATTR_URL): cv.url,
                vol.Optional("watch", default=False): cv.boolean,
                vol.Optional("refresh", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    for name, handler in ((SERVICE_WATCH, handle_watch), (SERVICE_UNWATCH, handle_unwatch)):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=vol.Schema({vol.Optional(CONF_ENTRY): cv.string, vol.Required(ATTR_PRODUCT_ID): cv.string}),
            supports_response=SupportsResponse.OPTIONAL,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_ALERTS,
        handle_list_alerts,
        schema=vol.Schema({vol.Optional(CONF_ENTRY): cv.string, vol.Optional(ATTR_PRODUCT_ID): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_ALERT,
        handle_create_alert,
        schema=vol.Schema(
            {
                vol.Optional(CONF_ENTRY): cv.string,
                vol.Required(ATTR_PRODUCT_ID): cv.string,
                vol.Optional("notify_on_drop", default=False): cv.boolean,
                vol.Optional("notify_on_rise", default=False): cv.boolean,
                vol.Optional("min_threshold_price"): vol.Coerce(float),
                vol.Optional("max_threshold_price"): vol.Coerce(float),
                vol.Optional("notify_email", default=False): cv.boolean,
                vol.Optional("name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_ALERT,
        handle_update_alert,
        schema=vol.Schema(
            {
                vol.Optional(CONF_ENTRY): cv.string,
                vol.Required(ATTR_ALERT_ID): vol.Coerce(int),
                vol.Optional("notify_on_drop"): cv.boolean,
                vol.Optional("notify_on_rise"): cv.boolean,
                vol.Optional("min_threshold_price"): vol.Any(None, vol.Coerce(float)),
                vol.Optional("max_threshold_price"): vol.Any(None, vol.Coerce(float)),
                vol.Optional("notify_email"): cv.boolean,
                vol.Optional("is_active"): cv.boolean,
                vol.Optional("name"): cv.string,
                vol.Optional("webhook_url"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ALERT,
        handle_delete_alert,
        schema=vol.Schema({vol.Optional(CONF_ENTRY): cv.string, vol.Required(ATTR_ALERT_ID): vol.Coerce(int)}),
    )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh, schema=vol.Schema({vol.Optional(CONF_ENTRY): cv.string}))


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove services when the last entry is unloaded."""
    for service in (
        SERVICE_SEARCH,
        SERVICE_BEST_DEAL,
        SERVICE_PRICE_HISTORY,
        SERVICE_TRACK,
        SERVICE_WATCH,
        SERVICE_UNWATCH,
        SERVICE_LIST_ALERTS,
        SERVICE_CREATE_ALERT,
        SERVICE_UPDATE_ALERT,
        SERVICE_DELETE_ALERT,
        SERVICE_REFRESH,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
