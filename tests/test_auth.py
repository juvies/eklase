"""Regression tests for the October 2026 E-klase login flow (no HA required)."""
from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

API_PATH = Path(__file__).resolve().parents[1] / 'custom_components/eklase_family/api.py'
spec = importlib.util.spec_from_file_location('eklase_auth_test_api', API_PATH)
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
# Type annotations are deferred; the client uses the supplied session only.
with patch.dict(sys.modules, {'aiohttp': ModuleType('aiohttp')}):
    spec.loader.exec_module(api)


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.payload


class LoginSession:
    """Model the observed authorize -> CSRF -> JSON login -> authorize protocol."""
    def __init__(self, *, csrf=None, login_status=200, state_mode='valid',
                 start_status=200, token_status=200, refresh_status=200):
        self.csrf = {'token': 'csrf-secret'} if csrf is None else csrf
        self.login_status = login_status
        self.start_status = start_status
        self.token_status = token_status
        self.refresh_status = refresh_status
        self.state_mode = state_mode
        self.calls = []
        self.authorize_url = None
        self.login_done = False

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        if url == api.CSRF_URL:
            return Response(payload=self.csrf)
        assert url.startswith('https://auth.e-klase.lv/connect/authorize?')
        if self.authorize_url is None:
            assert kwargs['allow_redirects'] is True
            self.authorize_url = url
            return Response(self.start_status)
        assert self.login_done
        assert url == self.authorize_url
        assert kwargs['allow_redirects'] is False
        query = parse_qs(urlparse(url).query)
        callback = {'code': 'authorization-secret'}
        if self.state_mode != 'missing':
            callback['state'] = query['state'][0] if self.state_mode == 'valid' else 'wrong'
        return Response(302, headers={'Location': api.REDIRECT_URI + '?' + urlencode(callback)})

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        if url == 'https://auth.e-klase.lv/connect/login':
            assert kwargs['json'] == {'username': 'test-user', 'password': 'test-password'}
            assert kwargs['headers'] == {'X-CSRF-TOKEN': 'csrf-secret'}
            assert kwargs['allow_redirects'] is False
            self.login_done = self.login_status == 200
            return Response(self.login_status)
        assert url == 'https://auth.e-klase.lv/connect/token'
        data = kwargs['data']
        assert data['client_id'] == 'web'
        if data['grant_type'] == 'refresh_token':
            return Response(self.refresh_status, {'access_token': 'refreshed', 'expires_in': 300})
        assert data['code'] == 'authorization-secret'
        assert data['redirect_uri'] == api.REDIRECT_URI
        verifier = data['code_verifier']
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        query = parse_qs(urlparse(self.authorize_url).query)
        assert query['code_challenge'] == [challenge]
        assert query['code_challenge_method'] == ['S256']
        assert query['scope'] == ['openid offline_access']
        return Response(self.token_status, {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 300})


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def client(self, session):
        return api.EklaseApiClient(session, 'test-user', 'test-password')

    async def test_complete_pkce_login(self):
        session = LoginSession()
        client = self.client(session)
        await client.ensure_token()
        self.assertEqual(client._token.access_token, 'access')
        self.assertEqual(client._token.refresh_token, 'refresh')
        self.assertEqual(len(session.calls), 5)
        self.assertGreater(client._token.expires_at, api._now_utc())
        await client.ensure_token()
        self.assertEqual(len(session.calls), 5, 'Unexpired tokens must be reused')

    async def test_bad_credentials_stop_before_code_exchange(self):
        session = LoginSession(login_status=401)
        client = self.client(session)
        with self.assertRaisesRegex(api.EklaseAuthError, 'Login failed: HTTP 401'):
            await client.ensure_token()
        self.assertIsNone(client._token)
        self.assertEqual(len(session.calls), 3)

    async def test_missing_csrf_never_sends_credentials(self):
        for csrf in ({}, {'token': ''}, {'token': 123}, []):
            with self.subTest(csrf=csrf):
                session = LoginSession(csrf=csrf)
                with self.assertRaisesRegex(api.EklaseAuthError, 'did not contain a token'):
                    await self.client(session).ensure_token()
                self.assertTrue(all(method == 'GET' for method, _, _ in session.calls))

    async def test_missing_or_wrong_state_never_exchanges_code(self):
        for mode in ('missing', 'wrong'):
            with self.subTest(mode=mode):
                session = LoginSession(state_mode=mode)
                with self.assertRaisesRegex(api.EklaseAuthError, 'State mismatch'):
                    await self.client(session).ensure_token()
                self.assertEqual(len(session.calls), 4)

    async def test_auth_start_failure(self):
        session = LoginSession(start_status=404)
        with self.assertRaisesRegex(api.EklaseAuthError, 'Auth start failed: HTTP 404'):
            await self.client(session).ensure_token()
        self.assertEqual(len(session.calls), 1)

    async def test_token_failure_does_not_set_token(self):
        client = self.client(LoginSession(token_status=400))
        with self.assertRaisesRegex(api.EklaseAuthError, 'Token exchange failed: HTTP 400'):
            await client.ensure_token()
        self.assertIsNone(client._token)

    async def test_refresh_uses_new_endpoint_and_preserves_refresh_token(self):
        session = LoginSession()
        client = self.client(session)
        client._token = api.TokenSet('old', 'refresh', api._now_utc() - timedelta(seconds=1))
        await client.ensure_token()
        self.assertEqual(client._token.access_token, 'refreshed')
        self.assertEqual(client._token.refresh_token, 'refresh')
        self.assertEqual(len(session.calls), 1)

    async def test_failed_refresh_falls_back_to_new_login(self):
        session = LoginSession(refresh_status=400)
        client = self.client(session)
        client._token = api.TokenSet('old', 'refresh', api._now_utc() - timedelta(seconds=1))
        await client.ensure_token()
        self.assertEqual(client._token.access_token, 'access')
        self.assertEqual(len(session.calls), 6)


if __name__ == '__main__':
    unittest.main()
