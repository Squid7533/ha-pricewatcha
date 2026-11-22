"""Config flow for Pricewatcha."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import PricewatchaApiError, PricewatchaAuthError, PricewatchaRateLimitError
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import async_validate_api_key

_LOGGER = logging.getLogger(__name__)

API_KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


async def _validate(hass: HomeAssistant, api_key: str) -> dict[str, str]:
    """Return a dict of field errors, empty when the key works."""
    try:
        await async_validate_api_key(hass, api_key)
    except PricewatchaAuthError:
        return {CONF_API_KEY: "invalid_auth"}
    except PricewatchaRateLimitError:
        return {"base": "rate_limited"}
    except PricewatchaApiError as err:
        if err.code == "cannot_connect":
            return {"base": "cannot_connect"}
        _LOGGER.debug("Pricewatcha validation failed: %s", err)
        return {"base": "unknown"}
    return {}


class PricewatchaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the API key once; it is stored in the config entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            errors = await _validate(self.hass, api_key)
            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Pricewatcha",
                    data={CONF_API_KEY: api_key},
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
            description_placeholders={
                "profile_url": "https://pricewatcha.com/profile#api-keys",
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None and entry is not None:
            api_key = user_input[CONF_API_KEY].strip()
            errors = await _validate(self.hass, api_key)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_API_KEY: api_key}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PricewatchaOptionsFlow()


class PricewatchaOptionsFlow(OptionsFlow):
    """Poll interval only; everything else lives on the services."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
