"""The Pricewatcha integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PricewatchaClient
from .const import CONF_API_KEY, DOMAIN, PLATFORMS
from .coordinator import PricewatchaCoordinator, scan_interval_of
from .services import async_setup_services, async_unload_services

type PricewatchaConfigEntry = ConfigEntry[PricewatchaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PricewatchaConfigEntry) -> bool:
    """Set up Pricewatcha from a config entry."""
    client = PricewatchaClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = PricewatchaCoordinator(hass, client, scan_interval_of(entry))

    # Raises ConfigEntryNotReady (rate limit, outage) or ConfigEntryAuthFailed (bad key).
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PricewatchaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            async_unload_services(hass)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: PricewatchaConfigEntry) -> None:
    """Re-apply options such as the poll interval."""
    await hass.config_entries.async_reload(entry.entry_id)
