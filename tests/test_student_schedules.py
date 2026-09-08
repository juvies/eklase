"""Focused regression tests, runnable without a Home Assistant installation."""
from __future__ import annotations

import ast
import asyncio
from datetime import date, datetime, time, timezone
import html
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'eklase_family'


def load_definitions(filename, namespace):
    """Load production definitions with lightweight substitutes for HA UI classes."""
    tree = ast.parse((ROOT / filename).read_text())
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    exec(compile(tree, str(ROOT / filename), 'exec'), namespace)
    return namespace


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return kwargs

    def async_show_menu(self, **kwargs):
        return kwargs

    def async_abort(self, **kwargs):
        return kwargs

    def async_create_entry(self, **kwargs):
        return kwargs


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        constants = {}
        exec((ROOT / 'const.py').read_text(), constants)
        self.constants = constants
        namespace = dict(constants, Any=object, datetime=datetime, date=date, time=time,
                         json=json, html_lib=html, re=re, _TAG_RE=re.compile(r'<[^>]+>'),
                         CoordinatorEntity=CoordinatorEntity, CalendarEntity=type('CalendarEntity', (), {}),
                         CalendarEvent=lambda **kwargs: SimpleNamespace(**kwargs),
                         dt_util=SimpleNamespace(DEFAULT_TIME_ZONE=timezone.utc),
                         HomeAssistant=object, ConfigEntry=object, AddEntitiesCallback=object)
        self.calendar_class = load_definitions('calendar.py', namespace)['EklaseFamilyCalendar']
        namespace = dict(constants, time=time, LESSON_COUNT=10,
                         config_entries=SimpleNamespace(ConfigFlow=Flow, OptionsFlow=Flow, ConfigEntry=object),
                         callback=lambda fn: fn, FlowResult=dict, HomeAssistantError=ValueError,
                         vol=SimpleNamespace(Schema=lambda fields: fields,
                                             Required=lambda key, **kwargs: key,
                                             Optional=lambda key, **kwargs: key,
                                             In=lambda choices: choices))
        self.flow_class = load_definitions('config_flow.py', namespace)['EklaseOptionsFlow']

    def calendar(self, overrides=None):
        diary = [{'date': '2026-09-08', 'lessons': [{'lessonNumber': 1, 'classJournalName': 'Math'}]}]
        coordinator = SimpleNamespace(data={
            '_last_refresh': 'same',
            'profiles': [{'profileId': 1, 'firstName': 'Anna'}, {'profileId': 2, 'firstName': 'Janis'}],
            'diary_by_profile': {'1': diary, '2': diary},
        })
        return self.calendar_class(coordinator, [{'start': '08:10', 'end': '08:50'}], 'entry', overrides)

    def test_individual_times_and_shared_fallback(self):
        calendar = self.calendar({'2': [{'start': '09:00', 'end': '09:40'}]})
        rows, _ = calendar._build_event_rows()
        self.assertEqual([(r['summary'], r['start'].strftime('%H:%M')) for r in rows],
                         [('Anna - Math', '08:10'), ('Janis - Math', '09:00')])
        self.assertEqual(rows[1]['end'].strftime('%H:%M'), '09:40')

    def test_legacy_configuration(self):
        rows, _ = self.calendar()._build_event_rows()
        self.assertEqual([r['start'].strftime('%H:%M') for r in rows], ['08:10', '08:10'])

    def test_cache_includes_individual_times(self):
        calendar = self.calendar({'2': [{'start': '09:00', 'end': '09:40'}]})
        calendar._build_event_rows()
        calendar._profile_lesson_times['2'][0]['start'] = '09:10'
        rows, _ = calendar._build_event_rows()
        self.assertEqual(rows[1]['start'].strftime('%H:%M'), '09:10')

    def flow(self, data=None):
        entry = SimpleNamespace(data=data or {}, options={}, entry_id='entry')
        flow = self.flow_class(entry)
        flow.hass = SimpleNamespace(data={})
        flow._profile_id = '2'
        flow._profile_label = 'Janis'
        flow._save = lambda data: {'saved': data}
        return flow

    def test_save_and_reset_preserve_other_student(self):
        schedules = {'1': [{'start': '08:00', 'end': '08:40'}]}
        flow = self.flow({'profile_lesson_times': schedules, 'username': 'unchanged'})
        fields = {f'l{i}_{part}': value for i in range(1, 11)
                  for part, value in [('start', '09:00'), ('end', '09:40')]}
        saved = asyncio.run(flow.async_step_student_times(fields))['saved']
        self.assertEqual(saved['username'], 'unchanged')
        self.assertEqual(saved['profile_lesson_times']['1'], schedules['1'])
        self.assertEqual(len(saved['profile_lesson_times']['2']), 10)
        self.assertNotIn('2', schedules)
        flow._entry.data = saved
        reset = asyncio.run(flow.async_step_student_times({'use_default': True}))['saved']
        self.assertEqual(reset['profile_lesson_times'], schedules)

    def test_invalid_times_do_not_save(self):
        flow = self.flow()
        result = asyncio.run(flow.async_step_student_times({'l1_start': '25:00', 'l1_end': '08:00'}))
        self.assertEqual(result['errors'], {'base': 'invalid_times'})
        self.assertNotIn('saved', result)

    def test_profiles_not_loaded(self):
        result = asyncio.run(self.flow().async_step_student())
        self.assertEqual(result['reason'], 'no_profiles')

    def test_options_menu_available_before_profiles_load(self):
        result = asyncio.run(self.flow().async_step_init())
        self.assertEqual(result['menu_options'], ['settings', 'student'])


if __name__ == '__main__':
    unittest.main()
