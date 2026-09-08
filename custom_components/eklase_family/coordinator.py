from __future__ import annotations

import logging
from datetime import date, timedelta, time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EklaseApiClient
from .const import (
    CONF_LESSON_TIMES,
    CONF_PROFILE_LESSON_TIMES,
    DEFAULT_LESSON_TIMES,
    CONF_REFRESH_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    CONF_WATCH_DAYS,
    DEFAULT_WATCH_DAYS,
)

from .watch import compare_first_lessons

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def _get_watch_days(entry: ConfigEntry) -> int:
    v = entry.options.get(CONF_WATCH_DAYS)
    if v is None:
        v = entry.data.get(CONF_WATCH_DAYS, DEFAULT_WATCH_DAYS)
    try:
        n = int(v)
    except Exception:
        n = DEFAULT_WATCH_DAYS
    return max(1, min(n, 14))


class EklaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, client: EklaseApiClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="E-klase Coordinator",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
        )
        self._client = client
        self._entry = entry
        self._store = Store(hass, STORE_VERSION, f"eklase_family_{entry.entry_id}_state")
        self._prev: dict[str, Any] = {}

    async def async_config_entry_first_refresh(self) -> None:
        self._prev = await self._store.async_load() or {}
        await super().async_config_entry_first_refresh()

    def set_client(self, client: EklaseApiClient) -> None:
        self._client = client

    def apply_options(self, entry: ConfigEntry | None = None) -> None:
        """Apply options from ConfigEntry (refresh interval, etc.)."""
        if entry is not None:
            self._entry = entry

        v = self._entry.options.get(CONF_REFRESH_INTERVAL)
        if v is None:
            v = self._entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)

        try:
            minutes = int(v)
        except Exception:
            minutes = DEFAULT_UPDATE_INTERVAL_MINUTES

        minutes = max(1, min(minutes, 24 * 60))
        self.update_interval = timedelta(minutes=minutes)
        _LOGGER.warning("E-klase coordinator update interval set to %s minutes", minutes)

    async def _async_update_data(self) -> dict[str, Any]:
        dt_now = dt_util.now()
        now_t = dt_now.time()

        _LOGGER.warning("E-klase coordinator tick at %s", dt_now.isoformat())

        # night mode: 00:00–06:00 (no sense to hit API; keep previous data stable)
        night_start = time(0, 0)
        night_end = time(6, 0)

        if night_start <= now_t < night_end:
            _LOGGER.debug("E-klase refresh skipped (night mode)")
            prev = dict(self.data or {})
            # keep last refresh (if any) and add marker that we skipped
            prev["_night_skipped_at"] = dt_now.isoformat()
            return prev

        try:
            profiles = await self._client.get_active_profiles()

            watch_days = _get_watch_days(self._entry)
            frm = dt_now.date()
            to = frm + timedelta(days=max(7, watch_days - 1))

            diary_by_profile: dict[str, Any] = {}

            for p in profiles:
                pid = str(p["profileId"])
                await self._client.switch_profile(pid)
                diary = await self._client.get_diary(pid, frm, to)
                diary_by_profile[pid] = diary

            sizes = {pid: len(diary or []) for pid, diary in diary_by_profile.items()}
            _LOGGER.info("E-klase refresh OK: profiles=%s, diary_days=%s", len(profiles), sizes)

            common_times = (
                self._entry.options.get(CONF_LESSON_TIMES)
                or self._entry.data.get(CONF_LESSON_TIMES)
                or DEFAULT_LESSON_TIMES
            )
            snapshot, changes = compare_first_lessons(
                self._prev.get("first_lessons", {}), diary_by_profile, profiles,
                frm, watch_days, common_times,
                self._entry.data.get(CONF_PROFILE_LESSON_TIMES, {}),
            )
            revision = self._prev.get("watch_revision", 0) + bool(changes)
            new_state = {
                "first_lessons": snapshot,
                "watch_revision": revision,
            }
            # Commit the baseline only after storage succeeds, so failed writes
            # do not silently consume a detected change.
            await self._store.async_save(new_state)
            self._prev = new_state
            if changes:
                self.hass.bus.async_fire("eklase_family_first_lesson_changed", {
                    "entry_id": self._entry.entry_id,
                    "changes": changes,
                    "message": "\n".join(change["message"] for change in changes),
                })

            return {
                "profiles": profiles,
                "diary_by_profile": diary_by_profile,
                "range": {"from": frm.isoformat(), "to": to.isoformat()},
                "_last_refresh": dt_now.isoformat(),
                "watch_days": watch_days,
                "watch_modified": bool(changes),
                "watch_revision": revision,
                "watch_changes": changes,
                "watch_from": frm.isoformat(),
            }

        except Exception as ex:
            raise UpdateFailed(f"E-Klase update failed: {ex}") from ex
