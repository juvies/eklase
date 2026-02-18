from __future__ import annotations

from datetime import datetime, date, time
from typing import Any
import logging
import re
import html as html_lib

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(s: str | None) -> str:
    """Very simple HTML -> plain text (good enough for E-klase task fields)."""
    if not s:
        return ""
    txt = _TAG_RE.sub("", s)
    txt = html_lib.unescape(txt)
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _build_lesson_details(lesson: dict[str, Any]) -> tuple[bool, bool, str]:
    """Returns (has_md, has_pd, details_text_for_description)."""
    parts: list[str] = []

    # MD (homeTasks)
    home_tasks = lesson.get("homeTasks") or []
    has_md = len(home_tasks) > 0
    if has_md:
        md_texts: list[str] = []
        for ht in home_tasks:
            t = ((ht or {}).get("task") or {}).get("text")
            plain = _html_to_text(t)
            if plain:
                md_texts.append(plain)
        if md_texts:
            parts.append("MD: " + " | ".join(md_texts))

    # PD (scheduledTests)
    scheduled_tests = lesson.get("scheduledTests") or []
    has_pd = len(scheduled_tests) > 0
    if has_pd:
        pd_texts: list[str] = []
        for st in scheduled_tests:
            t = ((st or {}).get("description") or {}).get("text")
            plain = _html_to_text(t)
            if plain:
                pd_texts.append(plain)
        if pd_texts:
            parts.append("PD: " + " | ".join(pd_texts))

    return has_md, has_pd, " • ".join(parts).strip()


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _dt_local(day: date, t: time) -> datetime:
    naive = datetime.combine(day, t)
    return naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


def _overlaps(start: datetime, end: datetime, win_start: datetime, win_end: datetime) -> bool:
    return not (end <= win_start or start >= win_end)


class EklaseFamilyCalendar(CoordinatorEntity, CalendarEntity):
    _attr_has_entity_name = True
    _attr_name = "E-klase"

    def __init__(self, coordinator, lesson_times: list[dict[str, str]], entry_id: str) -> None:
        super().__init__(coordinator)
        self._lesson_times = lesson_times or []
        self._attr_unique_id = f"eklase_family_calendar_{entry_id}"

        self._cache_key: str | None = None
        self._cached_rows: list[dict[str, Any]] = []   # <-- raw event rows (NOT CalendarEvent)
        self._cached_diag: dict[str, Any] = {}

    def _row_to_event(self, row: dict[str, Any]) -> CalendarEvent:
        """Always create a fresh CalendarEvent instance."""
        return CalendarEvent(
            summary=row["summary"],
            start=row["start"],
            end=row["end"],
            description=row.get("description"),
            location=row.get("location"),
        )

    def _build_event_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        data: dict[str, Any] = self.coordinator.data or {}

        lt_sig = ""
        if self._lesson_times:
            lt_sig = (
                f"{self._lesson_times[0].get('start','')}-{self._lesson_times[0].get('end','')};"
                f"{self._lesson_times[-1].get('start','')}-{self._lesson_times[-1].get('end','')}"
            )

        cache_key = f"{data.get('_last_refresh') or ''}|lt={len(self._lesson_times)}|sig={lt_sig}"
        if cache_key and cache_key == self._cache_key:
            return self._cached_rows, self._cached_diag

        profiles: list[dict[str, Any]] = data.get("profiles", []) or []
        diary_by_profile: dict[str, Any] = data.get("diary_by_profile", {}) or {}
        profile_names: dict[str, str] = {}

        for p in profiles:
            pid = str(p.get("profileId"))
            fn = p.get("firstName") or ""
            profile_names[pid] = f"{fn}".strip()

        prof_label: dict[str, str] = {}
        for p in profiles:
            pid = str(p.get("profileId") or "")
            if not pid:
                continue
            fn = p.get("firstName") or ""
            ln = p.get("lastName") or ""
            descr = p.get("studentDescription") or ""
            who = (f"{fn} {ln}").strip()
            prof_label[pid] = f"{who} — {descr}".strip(" —")

        times_by_no = {i + 1: lt for i, lt in enumerate(self._lesson_times)}

        rows: list[dict[str, Any]] = []

        max_lesson_no_seen = 0
        real_lessons_seen = 0
        skipped_no_time = 0
        total_lessons_items = 0
        first_nonempty: dict[str, Any] | None = None

        for pid, diary in diary_by_profile.items():
            label = prof_label.get(str(pid), str(pid))
            student_name = profile_names.get(str(pid), str(pid))

            for day_entry in diary or []:
                day_str = (day_entry.get("date") or "")[:10]
                if not day_str:
                    continue
                try:
                    day = date.fromisoformat(day_str)
                except ValueError:
                    continue

                lessons_list = day_entry.get("lessons", []) or []
                total_lessons_items += len(lessons_list)

                if first_nonempty is None and lessons_list:
                    first = lessons_list[0] if lessons_list else {}
                    first_nonempty = {
                        "pid": str(pid),
                        "date": day_str,
                        "lessons_len": len(lessons_list),
                        "first_lesson_keys": list((first or {}).keys()),
                        "first_lesson_journal": (first or {}).get("classJournalName"),
                        "first_lesson_no": (first or {}).get("lessonNumber"),
                    }

                for lesson in lessons_list:
                    lesson_no = int(lesson.get("lessonNumber", 0) or 0)
                    max_lesson_no_seen = max(max_lesson_no_seen, lesson_no)

                    jname = lesson.get("classJournalName") or "--"
                    if jname == "--":
                        continue

                    real_lessons_seen += 1

                    lt = times_by_no.get(lesson_no)
                    if not lt:
                        skipped_no_time += 1
                        continue

                    start_t = _parse_hhmm(lt["start"])
                    end_t = _parse_hhmm(lt["end"])

                    room = lesson.get("roomNumber") or ""

                    has_md, has_pd, details = _build_lesson_details(lesson)
                    suffix = ""
                    if has_md:
                        suffix += " - MD"
                    if has_pd:
                        suffix += " - PD"

                    summary = f"{student_name} - {jname}{suffix}" + (f" ({room})" if room else "")

                    description = label
                    if details:
                        description = f"{label} • {details}"
                    description = (description or "").strip() or None

                    rows.append(
                        {
                            "summary": summary,
                            "start": _dt_local(day, start_t),
                            "end": _dt_local(day, end_t),
                            "description": description,
                            "location": room or None,
                        }
                    )

        rows.sort(key=lambda r: r["start"])

        diag = {
            "lesson_times_len": len(self._lesson_times),
            "max_lesson_no_seen": max_lesson_no_seen,
            "real_lessons_seen": real_lessons_seen,
            "skipped_no_time": skipped_no_time,
            "total_lessons_items": total_lessons_items,
            "first_nonempty": first_nonempty,
            "events_built": len(rows),
        }

        self._cache_key = cache_key
        self._cached_rows = rows
        self._cached_diag = diag
        return rows, diag

    def _build_events(self) -> tuple[list[CalendarEvent], dict[str, Any]]:
        rows, diag = self._build_event_rows()
        # important: return NEW CalendarEvent objects every time
        return [self._row_to_event(r) for r in rows], diag

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        rows, _ = self._build_event_rows()
        upcoming = [r for r in rows if r["end"] > now]
        upcoming.sort(key=lambda r: r["start"])
        return self._row_to_event(upcoming[0]) if upcoming else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        rows, _ = self._build_event_rows()
        return [self._row_to_event(r) for r in rows if _overlaps(r["start"], r["end"], start_date, end_date)]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data: dict[str, Any] = self.coordinator.data or {}
        diary_by_profile = data.get("diary_by_profile", {}) or {}
        return {
            "profiles_count": len(data.get("profiles", []) or []),
            "diary_days_by_profile": {str(pid): len(days or []) for pid, days in diary_by_profile.items()},
            "last_refresh": data.get("_last_refresh"),
            "watch_days": data.get("watch_days"),
            "watch_from": data.get("watch_from"),
            "watch_modified": data.get("watch_modified"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    lesson_times = data["lesson_times"]
    async_add_entities([EklaseFamilyCalendar(coordinator, lesson_times, entry.entry_id)])