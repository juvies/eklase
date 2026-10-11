from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Any, Optional
import base64
import hashlib
import secrets
import urllib.parse

import aiohttp


# Published by https://family.e-klase.lv/api/auth-config and its OIDC discovery URL.
AUTH_BASE = "https://auth.e-klase.lv/connect"
AUTH_URL = f"{AUTH_BASE}/authorize"
LOGIN_URL = f"{AUTH_BASE}/login"
CSRF_URL = f"{LOGIN_URL}/token"
TOKEN_URL = f"{AUTH_BASE}/token"

API_BASE = "https://family.e-klase.lv/api"
PROFILES_URL = f"{API_BASE}/user/profiles"
SWITCH_URL = f"{API_BASE}/user/profiles/switch"
SYNC_URL = f"{API_BASE}/user/profiles/sync"
DIARY_URL = f"{API_BASE}/diary"

REDIRECT_URI = "https://family.e-klase.lv/redirect.html"
CLIENT_ID = "web"

REDIRECT_HOST = "family.e-klase.lv"
REDIRECT_PATH = "/redirect.html"


class EklaseAuthError(Exception):
    """Authentication or authorization failed."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)  # 43..128 chars, URL-safe
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str]
    expires_at: datetime  # UTC


class EklaseApiClient:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._s = session
        self._username = username
        self._password = password
        self._token: TokenSet | None = None

    async def ensure_token(self) -> None:
        if self._token and (self._token.expires_at - _now_utc() > timedelta(seconds=60)):
            return

        if self._token and self._token.refresh_token:
            if await self._try_refresh():
                return

        await self._login_pkce()

    async def _try_refresh(self) -> bool:
        assert self._token is not None
        if not self._token.refresh_token:
            return False

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token,
            "client_id": CLIENT_ID,
        }
        async with self._s.post(TOKEN_URL, data=data) as r:
            if r.status >= 400:
                return False
            js = await r.json()

        self._token = TokenSet(
            access_token=js["access_token"],
            refresh_token=js.get("refresh_token", self._token.refresh_token),
            expires_at=_now_utc() + timedelta(seconds=int(js.get("expires_in", 300))),
        )
        return True

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token.access_token}"}

    def _headers_for_profile(self, profile_id: str | None) -> dict[str, str]:
        h = dict(self._auth_headers())
        if profile_id:
            # šo neliekam auth endpointos, tikai FAMILY API endpointos
            h["x-profile-id"] = str(profile_id)
        return h

    async def _follow_redirects_for_code(self, url: str, max_hops: int = 15) -> str:
        """
        Follow GET redirects until we reach:
          https://family.e-klase.lv/redirect.html?code=...&state=...

        Returns the final URL that contains code.
        """
        for _ in range(max_hops):
            parsed = urllib.parse.urlparse(url)
            if (
                parsed.scheme == "https"
                and parsed.netloc == REDIRECT_HOST
                and parsed.path == REDIRECT_PATH
            ):
                return url

            async with self._s.get(url, allow_redirects=False) as r:
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.headers.get("Location")
                    if not loc:
                        raise EklaseAuthError("Redirect without Location during login")
                    url = urllib.parse.urljoin(url, loc)
                    continue

                raise EklaseAuthError(f"Login redirect flow stopped: HTTP {r.status}")

        raise EklaseAuthError("Too many redirects while completing login flow")

    async def _login_pkce(self) -> None:
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_hex(32)

        # 1) Start authorize flow (sets cookies / initializes flow)
        params = {
            "scope": "openid offline_access",
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "nonce": nonce,
            "ui_locales": "lv",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        # Use exactly the same authorization request after the login cookie is set.
        authorize_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        async with self._s.get(authorize_url, allow_redirects=True) as r:
            if r.status >= 400:
                raise EklaseAuthError(f"Auth start failed: HTTP {r.status}")

        # The current login page obtains a CSRF token and submits JSON credentials.
        # Both requests must use the same cookie jar as the authorization requests.
        async with self._s.get(CSRF_URL, allow_redirects=False) as r:
            if r.status != 200:
                raise EklaseAuthError(f"Login CSRF token failed: HTTP {r.status}")
            csrf = await r.json()
        csrf_token = csrf.get("token") if isinstance(csrf, dict) else None
        if not isinstance(csrf_token, str) or not csrf_token:
            raise EklaseAuthError("Login CSRF response did not contain a token")

        async with self._s.post(
            LOGIN_URL,
            json={"username": self._username, "password": self._password},
            headers={"X-CSRF-TOKEN": csrf_token},
            allow_redirects=False,
        ) as r:
            if r.status != 200:
                raise EklaseAuthError(f"Login failed: HTTP {r.status}")

        # The browser returns to /connect/authorize after a successful login.
        final_loc = await self._follow_redirects_for_code(authorize_url)

        parsed = urllib.parse.urlparse(final_loc)
        q = urllib.parse.parse_qs(parsed.query)
        code = (q.get("code") or [None])[0]
        st = (q.get("state") or [None])[0]

        if not code:
            raise EklaseAuthError("No authorization code in login callback")
        if st != state:
            raise EklaseAuthError("State mismatch (possible CSRF / broken flow)")

        # Exchange code for tokens
        token_form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        }
        async with self._s.post(TOKEN_URL, data=token_form) as r:
            if r.status >= 400:
                raise EklaseAuthError(f"Token exchange failed: HTTP {r.status}")
            js = await r.json()

        self._token = TokenSet(
            access_token=js["access_token"],
            refresh_token=js.get("refresh_token"),
            expires_at=_now_utc() + timedelta(seconds=int(js.get("expires_in", 300))),
        )

    # ---------- FAMILY API ----------

    async def get_profiles_raw(self) -> dict[str, Any]:
        await self.ensure_token()
        async with self._s.get(PROFILES_URL, headers=self._auth_headers()) as r:
            if r.status == 401:
                self._token = None
                await self.ensure_token()
                async with self._s.get(PROFILES_URL, headers=self._auth_headers()) as r2:
                    r2.raise_for_status()
                    return await r2.json()
            r.raise_for_status()
            return await r.json()

    async def get_active_profiles(self) -> list[dict[str, Any]]:
        js = await self.get_profiles_raw()
        return js.get("activeProfiles", []) or []

    async def switch_profile(self, profile_id: str) -> None:
        await self.ensure_token()
        payload = {"profileId": str(profile_id)}

        async with self._s.post(SWITCH_URL, json=payload, headers=self._headers_for_profile(profile_id)) as r:
            if r.status == 401:
                self._token = None
                await self.ensure_token()
                async with self._s.post(SWITCH_URL, json=payload, headers=self._headers_for_profile(profile_id)) as r2:
                    r2.raise_for_status()
                    return
            r.raise_for_status()

    async def sync_profile(self, profile_id: str) -> None:
        """
        Best-effort: UI calls before getting diary, but if it fails, we can still get the diary (maybe just a bit stale).
        if sync fails, then likely the diary will also fail, but if it succeeds, then the diary should be up-to-date.
        """
        await self.ensure_token()
        payload = {"profileId": str(profile_id)}

        try:
            async with self._s.post(SYNC_URL, json=payload, headers=self._headers_for_profile(profile_id)) as r:
                # 2xx ok; 4xx/5xx ignore as best-effort
                return
        except Exception:
            return

    async def get_diary(self, profile_id: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
        await self.ensure_token()
        params = {"from": from_date.isoformat(), "to": to_date.isoformat()}

        async with self._s.get(DIARY_URL, params=params, headers=self._headers_for_profile(profile_id)) as r:
            if r.status == 401:
                self._token = None
                await self.ensure_token()
                async with self._s.get(DIARY_URL, params=params, headers=self._headers_for_profile(profile_id)) as r2:
                    r2.raise_for_status()
                    return await r2.json()
            r.raise_for_status()
            return await r.json()
        
    def set_credentials(self, username: str, password: str) -> None:
        username = (username or "").strip()
        password = (password or "").strip()
        if username != self._username or password != self._password:
            self._username = username
            self._password = password
            self._token = None  # force re-login