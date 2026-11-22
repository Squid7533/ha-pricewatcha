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
    CONF_AI_API_KEY,
    CONF_AI_BASE_URL,
    CONF_AI_DAILY_BUDGET,
    CONF_AI_ENABLED,
    CONF_AI_INPUT_COST,
    CONF_AI_MAX_TOKENS,
    CONF_AI_MODEL,
    CONF_AI_MONTHLY_BUDGET,
    CONF_AI_OUTPUT_COST,
    CONF_AI_PROVIDER,
    CONF_AI_TEMPERATURE,
    CONF_AI_TIMEOUT,
    CONF_AI_WEB_SEARCH,
    CONF_API_KEY,
    CONF_CONVERSATION_TTL_DAYS,
    CONF_LANGUAGE,
    CONF_SEARXNG_URL,
    CONF_SCAN_INTERVAL,
    CONF_TELEGRAM_MIN_DROP,
    CONF_TELEGRAM_PHOTOS,
    CONF_WEB_SEARCH_PROVIDER,
    DEFAULT_AI_BASE_URL,
    DEFAULT_AI_DAILY_BUDGET,
    DEFAULT_AI_ENABLED,
    DEFAULT_AI_INPUT_COST,
    DEFAULT_AI_MAX_TOKENS,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_MONTHLY_BUDGET,
    DEFAULT_AI_OUTPUT_COST,
    DEFAULT_AI_PROVIDER,
    DEFAULT_AI_TEMPERATURE,
    DEFAULT_AI_TIMEOUT,
    DEFAULT_AI_WEB_SEARCH,
    DEFAULT_CONVERSATION_TTL_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TELEGRAM_MIN_DROP,
    DEFAULT_TELEGRAM_PHOTOS,
    DEFAULT_WEB_SEARCH_PROVIDER,
    DOMAIN,
    language_of,
    MAX_CONVERSATION_TTL_DAYS,
    MAX_SCAN_INTERVAL,
    MIN_CONVERSATION_TTL_DAYS,
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
    """General polling options plus the optional AI advisor."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "ai"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )

        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LANGUAGE,
                        default=language_of(self.config_entry.options),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "en", "label": "English"},
                                {"value": "de", "label": "Deutsch"},
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
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
                    ),
                    vol.Optional(
                        CONF_CONVERSATION_TTL_DAYS,
                        default=self.config_entry.options.get(
                            CONF_CONVERSATION_TTL_DAYS,
                            DEFAULT_CONVERSATION_TTL_DAYS,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_CONVERSATION_TTL_DAYS,
                            max=MAX_CONVERSATION_TTL_DAYS,
                            step=1,
                            unit_of_measurement="days",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_TELEGRAM_PHOTOS,
                        default=self.config_entry.options.get(
                            CONF_TELEGRAM_PHOTOS, DEFAULT_TELEGRAM_PHOTOS
                        ),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_TELEGRAM_MIN_DROP,
                        default=self.config_entry.options.get(
                            CONF_TELEGRAM_MIN_DROP, DEFAULT_TELEGRAM_MIN_DROP
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=50,
                            step=0.5,
                            unit_of_measurement="%",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    async def async_step_ai(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = self.config_entry.options
        current = {**options, **(user_input or {})}
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = dict(user_input)
            for key in (CONF_AI_API_KEY, CONF_AI_BASE_URL, CONF_AI_MODEL):
                if key in cleaned:
                    cleaned[key] = str(cleaned[key] or "").strip()
            if cleaned.get(CONF_AI_ENABLED):
                base_url = str(cleaned.get(CONF_AI_BASE_URL) or "")
                if not base_url.startswith(("http://", "https://")):
                    errors[CONF_AI_BASE_URL] = "invalid_url"
                if not str(cleaned.get(CONF_AI_MODEL) or "").strip():
                    errors[CONF_AI_MODEL] = "invalid_model"
                searxng = str(cleaned.get(CONF_SEARXNG_URL) or "").strip()
                if searxng and not searxng.startswith(("http://", "https://")):
                    errors[CONF_SEARXNG_URL] = "invalid_url"
                if (
                    cleaned.get(CONF_WEB_SEARCH_PROVIDER) == "searxng"
                    and not searxng
                ):
                    errors[CONF_SEARXNG_URL] = "searxng_url_required"
            if not errors:
                return self.async_create_entry(
                    title="", data={**options, **cleaned}
                )

        return self.async_show_form(
            step_id="ai",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AI_ENABLED,
                        default=current.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_AI_WEB_SEARCH,
                        default=current.get(
                            CONF_AI_WEB_SEARCH, DEFAULT_AI_WEB_SEARCH
                        ),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_WEB_SEARCH_PROVIDER,
                        default=current.get(
                            CONF_WEB_SEARCH_PROVIDER, DEFAULT_WEB_SEARCH_PROVIDER
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "auto", "label": "Auto (SearXNG if set, else DuckDuckGo)"},
                                {"value": "duckduckgo", "label": "DuckDuckGo"},
                                {"value": "searxng", "label": "SearXNG"},
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_SEARXNG_URL,
                        default=current.get(CONF_SEARXNG_URL, ""),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_AI_PROVIDER,
                        default=current.get(CONF_AI_PROVIDER, DEFAULT_AI_PROVIDER),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": "openai_compatible",
                                    "label": "OpenAI-compatible (DeepSeek, OpenAI, Ollama, ...)",
                                },
                                {
                                    "value": "anthropic",
                                    "label": "Anthropic (Claude)",
                                },
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_BASE_URL,
                        default=current.get(CONF_AI_BASE_URL, DEFAULT_AI_BASE_URL),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_AI_MODEL,
                        default=current.get(CONF_AI_MODEL, DEFAULT_AI_MODEL),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_AI_API_KEY,
                        default=current.get(CONF_AI_API_KEY, ""),
                    ): API_KEY_SELECTOR,
                    vol.Optional(
                        CONF_AI_MAX_TOKENS,
                        default=current.get(CONF_AI_MAX_TOKENS, DEFAULT_AI_MAX_TOKENS),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=100,
                            max=4000,
                            step=50,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_TEMPERATURE,
                        default=current.get(
                            CONF_AI_TEMPERATURE, DEFAULT_AI_TEMPERATURE
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=2,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_TIMEOUT,
                        default=current.get(CONF_AI_TIMEOUT, DEFAULT_AI_TIMEOUT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5,
                            max=120,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_INPUT_COST,
                        default=current.get(CONF_AI_INPUT_COST, DEFAULT_AI_INPUT_COST),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.01,
                            unit_of_measurement="USD / 1M tokens",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_OUTPUT_COST,
                        default=current.get(
                            CONF_AI_OUTPUT_COST, DEFAULT_AI_OUTPUT_COST
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.01,
                            unit_of_measurement="USD / 1M tokens",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_DAILY_BUDGET,
                        default=current.get(
                            CONF_AI_DAILY_BUDGET, DEFAULT_AI_DAILY_BUDGET
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            unit_of_measurement="USD",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_AI_MONTHLY_BUDGET,
                        default=current.get(
                            CONF_AI_MONTHLY_BUDGET, DEFAULT_AI_MONTHLY_BUDGET
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=10000,
                            step=0.01,
                            unit_of_measurement="USD",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )
