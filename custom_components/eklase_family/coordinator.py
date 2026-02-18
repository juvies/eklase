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
    CONF_REFRESH_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    CONF_WATCH_DAYS,
    DEFAULT_WATCH_DAYS,
)

import hashlib

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def _extract_mod_rows(
    diary_by_profile: dict[str, list[dict]],
    start: date,
    days: int,
) -> list[str]:
    wanted = {(start + timedelta(days=i)).isoformat() for i in range(max(0, days))}
    rows: list[str] = []

    for pid, diary in (diary_by_profile or {}).items():
        pid = str(pid)
        for day_entry in diary or []:
            d = (day_entry.get("date") or "")[:10]
            if d not in wanted:
                continue

            lm = (day_entry.get("lastModification") or {})
            tm = lm.get("timeModified") or ""
            rows.append(f"{pid}|{d}|{tm}")

    rows.sort()
    return rows


def _checksum(rows: list[str]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(r.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


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

            frm = date.today()
            to = frm + timedelta(days=7)

            diary_by_profile: dict[str, Any] = {}

            for p in profiles:
                pid = str(p["profileId"])
                await self._client.switch_profile(pid)
                diary = await self._client.get_diary(pid, frm, to)
                diary_by_profile[pid] = diary

            sizes = {pid: len(diary or []) for pid, diary in diary_by_profile.items()}
            _LOGGER.info("E-klase refresh OK: profiles=%s, diary_days=%s", len(profiles), sizes)

            watch_days = _get_watch_days(self._entry)
            start = date.today()
            rows = _extract_mod_rows(diary_by_profile, start, watch_days)
            new_sum = _checksum(rows)
            old_sum = self._prev.get("watch_checksum")
            modified = bool(old_sum) and (new_sum != old_sum)

            self._prev["watch_checksum"] = new_sum
            self._prev["watch_days"] = watch_days
            self._prev["watch_from"] = start.isoformat()
            await self._store.async_save(self._prev)

            return {
                "profiles": profiles,
                "diary_by_profile": diary_by_profile,
                "range": {"from": frm.isoformat(), "to": to.isoformat()},
                "_last_refresh": dt_now.isoformat(),
                "watch_days": watch_days,
                "watch_modified": modified,
                "watch_checksum": new_sum,
                "watch_from": start.isoformat(),
            }

        except Exception as ex:
            raise UpdateFailed(f"E-Klase update failed: {ex}") from ex