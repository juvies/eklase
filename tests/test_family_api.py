"""November 2026 Family API contract regressions; synthetic student data only."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import AsyncMock, patch

from test_auth import api, Response
import test_student_schedules


def profile(pid=101, cid=201, name='Anna'):
    return {
        'identity': {'profileId': pid, 'classId': cid},
        'user': {'firstName': name, 'lastName': 'Testa',
                 'className': '8.a', 'schoolName': 'Testa skola'},
    }


def profiles_response():
    # Deliberately put the old year first; the API's ordering is not a contract.
    return {'studyYears': [
        {'isCurrent': False, 'profiles': [profile(cid=100)]},
        {'isCurrent': True, 'profiles': [profile(), profile(102, 202, 'Jānis')],
         'inactiveProfiles': [profile(103, 203, 'Neaktīvs')]},
        {'isCurrent': False, 'isNext': True, 'profiles': [profile(cid=300)]},
    ]}


DIARY = [{'date': '2026-11-25', 'lessons': [
    {'lessonNumber': 1, 'classJournalName': '--'},
    {'lessonNumber': 2, 'classJournalName': 'Matemātika', 'roomNumber': '12',
     'homeTasks': [{'task': {'text': '<p>Izpildīt 5. uzdevumu</p>'}}],
     'scheduledTests': [{'description': {'text': '<p>Daļskaitļi</p>'}}]},
]}]


class HttpError(Exception):
    pass


class FamilyResponse(Response):
    def raise_for_status(self):
        if self.status >= 400:
            raise HttpError(f'HTTP {self.status}')


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)


class ProfileTests(unittest.TestCase):
    def test_only_current_active_profiles_with_stable_ids_and_names(self):
        payload = profiles_response()
        original = deepcopy(payload)
        profiles = api._active_profiles(payload)
        self.assertEqual([p['profileId'] for p in profiles], [101, 102])
        self.assertEqual([p['classId'] for p in profiles], [201, 202])
        self.assertEqual(profiles[0]['firstName'], 'Anna')
        self.assertEqual(profiles[0]['studentDescription'], '8.a, Testa skola')
        self.assertEqual(payload, original)

    def test_invalid_or_empty_profiles_cannot_silently_clear_calendar(self):
        payloads = [None, [], {}, {'activeProfiles': []}, {'studyYears': {}},
                    {'studyYears': []}, {'studyYears': [None]},
                    {'studyYears': [{'isCurrent': False, 'profiles': [profile()]}]},
                    {'studyYears': [{'isCurrent': True, 'profiles': []}]},
                    {'studyYears': [{'isCurrent': True}]},
                    {'studyYears': [{'isCurrent': True, 'profiles': [profile()] }]*2}]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(api.EklaseApiError):
                api._active_profiles(payload)

    def test_incomplete_identity_is_rejected(self):
        for identity in ({'profileId': 101}, {'classId': 201},
                         {'profileId': '', 'classId': 201},
                         {'profileId': 101, 'classId': {}},
                         {'profileId': True, 'classId': 201}):
            payload = profiles_response()
            payload['studyYears'][1]['profiles'][0]['identity'] = identity
            with self.subTest(identity=identity), self.assertRaises(api.EklaseApiError):
                api._active_profiles(payload)

    def test_duplicate_identity_is_deduplicated_but_other_class_is_not_overwritten(self):
        payload = profiles_response()
        payload['studyYears'][1]['profiles'].append(profile())
        self.assertEqual(len(api._active_profiles(payload)), 2)
        payload['studyYears'][1]['profiles'].append(profile(cid=999))
        with self.assertRaisesRegex(api.EklaseApiError, 'Multiple active classes'):
            api._active_profiles(payload)

    def test_diary_format_validation_and_empty_days(self):
        self.assertEqual(api._validate_diary(DIARY), DIARY)
        self.assertEqual(api._validate_diary([]), [])
        empty = [{'date': '2026-11-25', 'lessons': None, 'isEmptyDay': True}]
        self.assertEqual(api._validate_diary(empty), empty)
        for payload in ({}, None, {'days': DIARY}, [None],
                        [{'date': 'invalid'}], [{'date': '2026-11-25', 'lessons': {}}],
                        [{'date': '2026-11-25', 'lessons': [{'subject': 'Renamed'}]}]):
            with self.subTest(payload=payload), self.assertRaises(api.EklaseApiError):
                api._validate_diary(payload)

    def test_new_profiles_and_diary_render_with_existing_individual_schedule(self):
        helper = test_student_schedules.ScheduleTests()
        helper.setUp()
        calendar = helper.calendar()
        calendar.coordinator.data = {
            'profiles': api._active_profiles(profiles_response()),
            'diary_by_profile': {'101': api._validate_diary(DIARY)},
            '_last_refresh': 'new-api',
        }
        calendar._profile_lesson_times = {'101': [
            {'start': '08:30', 'end': '09:10'}, {'start': '09:20', 'end': '10:00'},
        ]}
        rows, _ = calendar._build_event_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['summary'], 'Anna - Matemātika - MD - PD (12)')
        self.assertEqual(rows[0]['start'].strftime('%H:%M'), '09:20')
        self.assertEqual(rows[0]['description'],
                         'Anna Testa — 8.a, Testa skola • MD: Izpildīt 5. uzdevumu • PD: Daļskaitļi')


class FamilyRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.error_patch = patch.object(api.aiohttp, 'ClientError', HttpError, create=True)
        self.error_patch.start()
        self.addCleanup(self.error_patch.stop)

    def client(self, responses):
        session = Session(responses)
        client = api.EklaseApiClient(session, 'test', 'test')
        client._token = api.TokenSet('access', None, api._now_utc() + timedelta(hours=1))
        return client, session

    async def test_sync_then_profiles_use_new_base_without_profile_headers_or_body(self):
        client, session = self.client([FamilyResponse(204), FamilyResponse(payload=profiles_response())])
        self.assertEqual(len(await client.get_active_profiles()), 2)
        self.assertEqual([(m, u) for m, u, _ in session.calls], [
            ('POST', 'https://my.e-klase.lv/family/api/user/profiles/sync'),
            ('GET', 'https://my.e-klase.lv/family/api/user/profiles/by-year'),
        ])
        for _, _, kwargs in session.calls:
            self.assertEqual(kwargs, {'headers': {'Authorization': 'Bearer access'}})

    async def test_switch_and_diary_include_both_identity_parts_for_each_student(self):
        client, session = self.client([FamilyResponse(204), FamilyResponse(payload=DIARY)] * 2)
        for pid, cid in [(101, 201), (102, 202)]:
            await client.switch_profile(pid, cid)
            self.assertEqual(await client.get_diary(pid, date(2026, 11, 25), date(2026, 12, 2),
                                                   class_id=cid), DIARY)
        for index, (_, url, kwargs) in enumerate(session.calls):
            pid, cid = [(101, 201), (102, 202)][index // 2]
            self.assertEqual(kwargs['headers'], {
                'Authorization': 'Bearer access', 'x-profile-id': str(pid), 'x-class-id': str(cid),
            })
            if index % 2 == 0:
                self.assertEqual(url, 'https://my.e-klase.lv/family/api/user/profiles/switch')
                self.assertEqual(kwargs['json'], {'profileId': pid, 'classId': cid})
            else:
                self.assertEqual(url, 'https://my.e-klase.lv/family/api/diary')
                self.assertEqual(kwargs['params'], {'from': '2026-11-25', 'to': '2026-12-02'})

    async def test_401_retry_keeps_identity_and_payload_with_new_token(self):
        for method in ('GET', 'POST'):
            with self.subTest(method=method):
                client, session = self.client([FamilyResponse(401), FamilyResponse(payload=DIARY)])
                async def login():
                    client._token = api.TokenSet('renewed', None, api._now_utc() + timedelta(hours=1))
                client._login_pkce = AsyncMock(side_effect=login)
                if method == 'GET':
                    await client.get_diary(101, date(2026, 11, 25), date(2026, 12, 2), class_id=201)
                else:
                    await client.switch_profile(101, 201)
                client._login_pkce.assert_awaited_once()
                first, retry = session.calls
                self.assertEqual(first[:2], retry[:2])
                expected = deepcopy(first[2])
                expected['headers']['Authorization'] = 'Bearer renewed'
                self.assertEqual(retry[2], expected)

    async def test_failed_sync_still_loads_profiles(self):
        client, session = self.client([FamilyResponse(503), FamilyResponse(payload=profiles_response())])
        self.assertEqual(len(await client.get_active_profiles()), 2)

    async def test_second_401_is_reported_without_infinite_retry(self):
        client, session = self.client([FamilyResponse(401), FamilyResponse(401)])
        client._login_pkce = AsyncMock()
        with self.assertRaisesRegex(HttpError, '401'):
            await client.switch_profile(101, 201)
        self.assertEqual(len(session.calls), 2)

    async def test_server_error_does_not_become_an_empty_diary(self):
        client, _ = self.client([FamilyResponse(503)])
        with self.assertRaisesRegex(HttpError, '503'):
            await client.get_diary(101, date(2026, 11, 25), date(2026, 12, 2), class_id=201)


if __name__ == '__main__':
    unittest.main()
