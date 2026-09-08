from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EklaseApiClient
from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LESSON_TIMES,
    CONF_PROFILE_LESSON_TIMES,
    CONF_REFRESH_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DEFAULT_LESSON_TIMES,
)
from .coordinator import EklaseCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["calendar"]


def _get_lesson_times(entry: ConfigEntry) -> list[dict[str, str]]:
    # prioritāte: options -> data -> default
    times = entry.options.get(CONF_LESSON_TIMES)
    if times:
        return list(times)
    times = entry.data.get(CONF_LESSON_TIMES)
    if times:
        return list(times)
    return list(DEFAULT_LESSON_TIMES)


def _get_refresh_minutes(entry: ConfigEntry) -> int:
    v = entry.options.get(CONF_REFRESH_INTERVAL)
    if v is None:
        v = entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)

    try:
        minutes = int(v)
    except Exception:
        minutes = DEFAULT_UPDATE_INTERVAL_MINUTES

    # security boundary: 1 min .. 24 h
    return max(1, min(minutes, 24 * 60))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    client = EklaseApiClient(
        session=session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    coordinator = EklaseCoordinator(hass, client, entry)
    coordinator.update_interval = timedelta(minutes=_get_refresh_minutes(entry))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "lesson_times": _get_lesson_times(entry),
        CONF_PROFILE_LESSON_TIMES: entry.data.get(CONF_PROFILE_LESSON_TIMES, {}),
    }

    # to see if refresh interval and lesson times are correctly read from config at startup
    _LOGGER.warning(
        "E-klase setup: refresh=%s min, lesson_times=%s",
        _get_refresh_minutes(entry),
        len(hass.data[DOMAIN][entry.entry_id]["lesson_times"]),
    )

    # token + pirmais refresh
    await client.ensure_token()
    await coordinator.async_config_entry_first_refresh()

    async def _update_listener(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        """Reload entities and the client after configuration changes."""
        await hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok