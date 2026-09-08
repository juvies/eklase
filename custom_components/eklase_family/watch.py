"""Compare the first real lesson per student and date without API timestamps."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def compare_first_lessons(
    previous: dict[str, dict[str, int]],
    diary_by_profile: dict[str, list[dict]],
    profiles: list[dict],
    start: date,
    days: int,
    common_times: list[dict[str, str]],
    profile_times: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]]]:
    """Keep missing/empty days as unknown; first observations establish a baseline."""
    end = start + timedelta(days=max(7, days - 1))
    wanted_end = start + timedelta(days=days)
    snapshot = {
        str(pid): {day: number for day, number in dates.items()
                   if start.isoformat() <= day <= end.isoformat()}
        for pid, dates in previous.items()
    }
    names = {
        str(p['profileId']): ' '.join(filter(None, [p.get('firstName'), p.get('lastName')]))
        for p in profiles if p.get('profileId') is not None
    }
    changes = []
    for pid, diary in diary_by_profile.items():
        pid = str(pid)
        times = profile_times.get(pid, common_times)
        for item in diary or []:
            day = (item.get('date') or '')[:10]
            try:
                day_date = date.fromisoformat(day)
            except ValueError:
                continue
            if not start <= day_date <= end:
                continue
            numbers = []
            for lesson in item.get('lessons') or []:
                if not (lesson.get('classJournalName') or '').strip() or lesson['classJournalName'].strip() == '--':
                    continue
                try:
                    number = int(lesson.get('lessonNumber') or 0)
                except (ValueError, TypeError):
                    continue
                if number > 0:
                    numbers.append(number)
            if not numbers:
                # An empty response cannot prove that school has been cancelled.
                continue
            first = min(numbers)
            old = snapshot.setdefault(pid, {}).get(day)
            snapshot[pid][day] = first
            if old is None or old == first or day_date >= wanted_end:
                continue
            # Resolve both numbers against today's configuration so editing bell
            # times does not masquerade as a change received from E-klase.
            old_start = times[old - 1].get('start') if 0 < old <= len(times) else None
            new_start = times[first - 1].get('start') if first <= len(times) else None
            if old_start and new_start and old_start == new_start:
                continue
            student = names.get(pid) or pid
            old_label = old_start or f'{old}. stunda'
            new_label = new_start or f'{first}. stunda'
            changes.append({
                'profile_id': pid, 'student': student, 'date': day,
                'old_lesson_number': old, 'new_lesson_number': first,
                'old_start': old_start, 'new_start': new_start,
                'message': f'{student} ({day}): pirmā stunda tagad {new_label}, iepriekš {old_label}.',
            })
    changes.sort(key=lambda change: (change['date'], change['profile_id']))
    return snapshot, changes
