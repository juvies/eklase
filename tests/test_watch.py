"""First-lesson comparisons and coordinator persistence/event regressions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import importlib.util
import logging
from types import SimpleNamespace as NS
from typing import Any
import unittest

from test_student_schedules import ROOT, load_definitions

spec = importlib.util.spec_from_file_location('watch_under_test', ROOT / 'watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
compare = watch.compare_first_lessons
START = date(2026, 9, 8)
DAY = '2026-09-09'
TIMES = [{'start': '08:30'}, {'start': '09:20'}, {'start': '10:10'}]
PROFILES = [{'profileId': '1', 'firstName': 'Anna'}, {'profileId': '2', 'firstName': 'Janis'}]


def diary(numbers, day=DAY):
    return [{'date': day, 'lessons': [
        {'lessonNumber': n, 'classJournalName': 'Math'} for n in numbers
    ]}]


class WatchTests(unittest.TestCase):
    def compare(self, previous, current, days=2, start=START, times=None):
        return compare(previous, current, PROFILES, start, days, TIMES, times or {})

    def test_first_observation_and_legacy_storage_establish_baseline(self):
        snapshot, changes = self.compare({}, {'1': diary([1, 2])})
        self.assertEqual(snapshot, {'1': {DAY: 1}})
        self.assertEqual(changes, [])

    def test_first_lesson_disappears_without_modification_timestamp(self):
        _, changes = self.compare({'1': {DAY: 1}}, {'1': diary([2, 3])})
        self.assertEqual((changes[0]['old_start'], changes[0]['new_start']), ('08:30', '09:20'))

    def test_earlier_lesson_added(self):
        _, changes = self.compare({'1': {DAY: 2}}, {'1': diary([2, 1])})
        self.assertEqual(changes[0]['new_start'], '08:30')

    def test_empty_missing_and_placeholder_days_preserve_baseline(self):
        baseline = {'1': {DAY: 1}}
        for current in [{}, {'1': []}, {'1': diary([])}, {'1': [{'date': DAY, 'lessons': [
            {'lessonNumber': 1, 'classJournalName': '--'}]}]}]:
            snapshot, changes = self.compare(baseline, current)
            self.assertEqual(snapshot, baseline)
            self.assertEqual(changes, [])
        _, changes = self.compare(snapshot, {'1': diary([2])})
        self.assertEqual(len(changes), 1)

    def test_later_lessons_subject_room_homework_do_not_trigger(self):
        current = diary([1, 3])
        current[0]['lessons'][0].update(classJournalName='English', roomNumber='99', homeTasks=[{'text': 'new'}])
        _, changes = self.compare({'1': {DAY: 1}}, {'1': current})
        self.assertEqual(changes, [])

    def test_student_specific_times_and_separate_profiles(self):
        _, changes = self.compare({'1': {DAY: 1}, '2': {DAY: 1}},
                                  {'1': diary([1]), '2': diary([2])},
                                  times={'2': [{'start': '09:00'}, {'start': '09:50'}]})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['profile_id'], '2')
        self.assertEqual(changes[0]['new_start'], '09:50')

    def test_tomorrow_included_day_after_excluded_and_saved(self):
        later = '2026-09-10'
        snapshot, changes = self.compare({'1': {DAY: 1, later: 1}},
                                         {'1': diary([2]) + diary([2], later)})
        self.assertEqual([c['date'] for c in changes], [DAY])
        self.assertEqual(snapshot['1'][later], 2)

    def test_day_rollover_unchanged_does_not_trigger(self):
        snapshot, _ = self.compare({}, {'1': diary([1]) + diary([2], '2026-09-10')})
        _, changes = self.compare(snapshot, {'1': diary([1]) + diary([2], '2026-09-10')},
                                  start=date(2026, 9, 9))
        self.assertEqual(changes, [])

    def test_previously_loaded_future_day_is_compared_when_in_window(self):
        snapshot, _ = self.compare({}, {'1': diary([1], '2026-09-10')})
        _, changes = self.compare(snapshot, {'1': diary([2], '2026-09-10')}, start=date(2026, 9, 9))
        self.assertEqual(len(changes), 1)

    def test_past_days_pruned_and_inputs_not_mutated(self):
        baseline = {'1': {'2026-09-07': 1, DAY: 1}}
        original = deepcopy(baseline)
        snapshot, _ = self.compare(baseline, {'1': diary([2])})
        self.assertEqual(baseline, original)
        self.assertNotIn('2026-09-07', snapshot['1'])

    def test_out_of_range_lesson_still_reports_number(self):
        _, changes = self.compare({'1': {DAY: 1}}, {'1': diary([4])})
        self.assertIsNone(changes[0]['new_start'])
        self.assertIn('4. stunda', changes[0]['message'])


class CoordinatorBase:
    def __class_getitem__(cls, item):
        return cls

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()


class MemoryStore:
    def __init__(self):
        self.value = {}
        self.fail = False

    async def async_save(self, value):
        if self.fail:
            raise OSError('storage unavailable')
        self.value = deepcopy(value)

    async def async_load(self):
        return deepcopy(self.value)


class Client:
    def __init__(self):
        self.current = diary([1, 2])

    async def get_active_profiles(self):
        return PROFILES[:1]

    async def switch_profile(self, pid):
        pass

    async def get_diary(self, pid, frm, to):
        self.requested = (frm, to)
        return self.current


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        constants = {}
        exec((ROOT / 'const.py').read_text(), constants)
        self.now = datetime(2026, 9, 8, 12)
        ns = dict(constants, Any=Any, date=date, timedelta=timedelta, time=time,
                  DataUpdateCoordinator=CoordinatorBase, UpdateFailed=RuntimeError,
                  compare_first_lessons=compare, _LOGGER=logging.getLogger('watch-test'),
                  dt_util=NS(now=lambda: self.now))
        cls = load_definitions('coordinator.py', ns)['EklaseCoordinator']
        self.coordinator = object.__new__(cls)
        self.coordinator._client = Client()
        self.coordinator._store = MemoryStore()
        self.coordinator._prev = {'watch_checksum': 'old implementation'}
        self.coordinator._entry = NS(options={}, data={'watch_days': 2, 'lesson_times': TIMES}, entry_id='entry')
        self.events = []
        self.coordinator.hass = NS(bus=NS(async_fire=lambda kind, data: self.events.append((kind, data))))
        self.coordinator.data = None

    async def test_consecutive_changes_emit_separate_events_and_persist(self):
        c = self.coordinator
        await c.async_config_entry_first_refresh()
        self.assertFalse(c.data['watch_modified'])
        c._client.current = diary([2])
        first = await c._async_update_data()
        c._client.current = diary([1, 2])
        second = await c._async_update_data()
        self.assertEqual([first['watch_revision'], second['watch_revision']], [1, 2])
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[0][0], 'eklase_family_first_lesson_changed')
        self.assertEqual(self.events[0][1]['entry_id'], 'entry')
        c._prev = {}
        await c.async_config_entry_first_refresh()
        self.assertFalse(c.data['watch_modified'])
        self.assertEqual(c.data['watch_revision'], 2)
        self.assertEqual(len(self.events), 2)

    async def test_failed_save_does_not_consume_change(self):
        c = self.coordinator
        await c._async_update_data()
        c._client.current = diary([2])
        baseline = deepcopy(c._prev)
        c._store.fail = True
        with self.assertRaises(RuntimeError):
            await c._async_update_data()
        self.assertEqual(c._prev, baseline)
        self.assertEqual(self.events, [])
        c._store.fail = False
        result = await c._async_update_data()
        self.assertTrue(result['watch_modified'])

    async def test_fourteen_day_range_and_night_skip(self):
        c = self.coordinator
        c._entry.data['watch_days'] = 14
        c.data = await c._async_update_data()
        self.assertEqual(c._client.requested, (START, START + timedelta(days=13)))
        self.now = datetime(2026, 9, 9, 3)
        previous = deepcopy(c._prev)
        await c._async_update_data()
        self.assertEqual(c._prev, previous)
        self.assertEqual(self.events, [])
