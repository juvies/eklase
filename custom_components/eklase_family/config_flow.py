from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LESSON_TIMES,
    CONF_REFRESH_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DEFAULT_LESSON_TIMES,
    CONF_WATCH_DAYS,
    DEFAULT_WATCH_DAYS,
)

LESSON_COUNT = len(DEFAULT_LESSON_TIMES)  # parasti 10


def _times_schema(lesson_times: list[dict[str, str]]) -> dict:
    """
§   Dynamically creates schema fields for l1_start/l1_end..l10_start/l10_end based on DEFAULT_LESSON_TIMES and optionally provided lesson_times (which can override defaults). Returns a dict suitable for inclusion in a voluptuous.Schema.
    """
    fields: dict = {}

    for i in range(1, LESSON_COUNT + 1):
        default = DEFAULT_LESSON_TIMES[i - 1]
        if i - 1 < len(lesson_times):
            t = lesson_times[i - 1]
            default = {"start": t.get("start", default["start"]), "end": t.get("end", default["end"])}

        fields[vol.Required(f"l{i}_start", default=default["start"])] = str
        fields[vol.Required(f"l{i}_end", default=default["end"])] = str

    return fields


def _read_times_from_input(user_input: dict) -> list[dict[str, str]]:
    """Nolasa l1_start/l1_end..l10_start/l10_end no formas. Atgriež tikai tos pārus, kas ir aizpildīti."""
    times: list[dict[str, str]] = []
    for i in range(1, LESSON_COUNT + 1):
        s = (user_input.get(f"l{i}_start") or "").strip()
        e = (user_input.get(f"l{i}_end") or "").strip()
        if not s or not e:
            continue
        times.append({"start": s, "end": e})
    return times


def _schema_with_defaults(
    *,
    username: str = "",
    password: str = "",
    refresh_interval: int = DEFAULT_UPDATE_INTERVAL_MINUTES,
    lesson_times: list[dict[str, str]] | None = None,
    watch_days: int = DEFAULT_WATCH_DAYS,
) -> vol.Schema:
    lesson_times = lesson_times or DEFAULT_LESSON_TIMES

    fields = {
        vol.Required(CONF_USERNAME, default=username): str,
        vol.Required(CONF_PASSWORD, default=password): str,
        vol.Optional(CONF_REFRESH_INTERVAL, default=refresh_interval): vol.Coerce(int),
        vol.Optional(CONF_WATCH_DAYS, default=watch_days): vol.Coerce(int),
        **_times_schema(lesson_times),
    }
    return vol.Schema(fields)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return EklaseOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_schema_with_defaults())

        username = (user_input.get(CONF_USERNAME) or "").strip()
        password = (user_input.get(CONF_PASSWORD) or "").strip()
        if not username:
            raise HomeAssistantError("Lietotājvārds nedrīkst būt tukšs.")
        if not password:
            raise HomeAssistantError("Parole nedrīkst būt tukša.")

        refresh_interval = int(user_input.get(CONF_REFRESH_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES))
        watch_days = int(user_input.get(CONF_WATCH_DAYS, DEFAULT_WATCH_DAYS))

        lesson_times = _read_times_from_input(user_input)
        if not lesson_times:
            raise HomeAssistantError("Nav norādīti stundu laiki.")

        return self.async_create_entry(
            title="E-klase",
            data={
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_REFRESH_INTERVAL: refresh_interval,
                CONF_LESSON_TIMES: lesson_times,
                CONF_WATCH_DAYS: watch_days,
            },
        )


class EklaseOptionsFlow(config_entries.OptionsFlow):
    """Options UI,stores all in entry.data (not in entry.options)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            new_username = (user_input.get(CONF_USERNAME) or "").strip()
            new_password = (user_input.get(CONF_PASSWORD) or "").strip()
            if not new_username:
                raise HomeAssistantError("Lietotājvārds nedrīkst būt tukšs.")
            if not new_password:
                raise HomeAssistantError("Parole nedrīkst būt tukša.")

            refresh_interval = int(user_input.get(CONF_REFRESH_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES))
            watch_days = int(user_input.get(CONF_WATCH_DAYS, DEFAULT_WATCH_DAYS))

            lesson_times = _read_times_from_input(user_input)
            if not lesson_times:
                raise HomeAssistantError("Nav norādīti stundu laiki.")

            new_data = dict(self._entry.data)
            new_data[CONF_USERNAME] = new_username
            new_data[CONF_PASSWORD] = new_password
            new_data[CONF_REFRESH_INTERVAL] = refresh_interval
            new_data[CONF_LESSON_TIMES] = lesson_times
            new_data[CONF_WATCH_DAYS] = watch_days

            self.hass.config_entries.async_update_entry(self._entry, data=new_data)

            # reloads integration so that new credentials are used immediately (without HA restart):
            self.hass.async_create_task(self.hass.config_entries.async_reload(self._entry.entry_id))

            # OptionsFlow requires to return entry; but we already updated it, so just return it as is:
            return self.async_create_entry(title="", data={})

        current_username = self._entry.data.get(CONF_USERNAME, "")
        current_password = self._entry.data.get(CONF_PASSWORD, "")

        current_refresh = int(self._entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES))
        current_times = self._entry.data.get(CONF_LESSON_TIMES, DEFAULT_LESSON_TIMES)
        current_watch_days = int(self._entry.data.get(CONF_WATCH_DAYS, DEFAULT_WATCH_DAYS))

        return self.async_show_form(
            step_id="init",
            data_schema=_schema_with_defaults(
                username=current_username,
                password=current_password,
                refresh_interval=current_refresh,
                lesson_times=current_times,
                watch_days=current_watch_days,
            ),
        )