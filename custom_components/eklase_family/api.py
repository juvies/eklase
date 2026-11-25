from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Any, Optional
import base64
import hashlib
import secrets
import urllib.parse

import aiohttp


# Published by https://my.e-klase.lv/family/api/auth-config and its OIDC discovery URL.
AUTH_BASE = "https://auth.e-klase.lv/connect"
AUTH_URL = f"{AUTH_BASE}/authorize"
LOGIN_URL = f"{AUTH_BASE}/login"
CSRF_URL = f"{LOGIN_URL}/token"
TOKEN_URL = f"{AUTH_BASE}/token"

API_BASE = "https://my.e-klase.lv/family/api"
PROFILES_URL = f"{API_BASE}/user/profiles/by-year"
SWITCH_URL = f"{API_BASE}/user/profiles/switch"
SYNC_URL = f"{API_BASE}/user/profiles/sync"
DIARY_URL = f"{API_BASE}/diary"

REDIRECT_URI = "https://family.e-klase.lv/"
CLIENT_ID = "web"

REDIRECT_HOST = "family.e-klase.lv"
REDIRECT_PATH = "/"


class EklaseAuthError(Exception):
    """Authentication or authorization failed."""


class EklaseApiError(Exception):
    """The Family API response cannot be used safely."""


def _active_profiles(payload: Any) -> list[dict[str, Any]]:
    """Adapt the current study year's profiles to the existing calendar model."""
    if not isinstance(payload, dict) or not isinstance(payload.get("studyYears"), list):
        raise EklaseApiError("Unexpected profiles response: missing studyYears list")
    years = payload["studyYears"]
    if not all(isinstance(year, dict) for year in years):
        raise EklaseApiError("Unexpected study year format")
    current = [year for year in years if year.get("isCurrent") is True]
    if len(current) != 1:
        raise EklaseApiError("Expected one current study year in profiles response")
    profiles = current[0].get("profiles")
    if not isinstance(profiles, list):
        raise EklaseApiError("Unexpected current study year profiles format")
    if not profiles:
        raise EklaseApiError("No active student profiles in the current study year")

    result = []
    seen: dict[str, Any] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            raise EklaseApiError("Unexpected student profile format")
        identity = profile.get("identity")
        user = profile.get("user")
        if not isinstance(identity, dict) or not isinstance(user, dict):
            raise EklaseApiError("Student profile is missing identity or user")
        for field in ("profileId", "classId"):
            value = identity.get(field)
            if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
                raise EklaseApiError(f"Student identity is missing a valid {field}")
        pid = str(identity["profileId"])
        if pid in seen:
            if seen[pid] != identity["classId"]:
                raise EklaseApiError("Multiple active classes for the same profile are not supported")
            continue
        seen[pid] = identity["classId"]
        result.append({
            "profileId": identity["profileId"],
            "classId": identity["classId"],
            "firstName": user.get("firstName") or "",
            "lastName": user.get("lastName") or "",
            "studentDescription": ", ".join(
                value for value in (user.get("className"), user.get("schoolName")) if value
            ),
        })
    return result


def _validate_diary(payload: Any) -> list[dict[str, Any]]:
    """Reject changed response envelopes instead of publishing an empty calendar."""
    if not isinstance(payload, list):
        raise EklaseApiError("Unexpected diary response: expected a list of days")
    for day in payload:
        if not isinstance(day, dict) or not isinstance(day.get("date"), str):
            raise EklaseApiError("Unexpected diary day format")
        try:
            date.fromisoformat(day["date"][:10])
        except ValueError as ex:
            raise EklaseApiError("Invalid diary date") from ex
        lessons = day.get("lessons")
        if lessons is None:
            continue
        if not isinstance(lessons, list) or not all(
            isinstance(lesson, dict) and isinstance(lesson.get("classJournalName"), str)
            for lesson in lessons
        ):
            raise EklaseApiError("Unexpected diary lessons format")
    return payload


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

    def _headers_for_profile(
        self, profile_id: str | int | None, class_id: str | int | None
    ) -> dict[str, str]:
        h = dict(self._auth_headers())
        if profile_id is not None and class_id is not None:
            # Both parts of the identity are required, and only on Family requests.
            h["x-profile-id"] = str(profile_id)
            h["x-class-id"] = str(class_id)
        return h

    async def _follow_redirects_for_code(self, url: str, max_hops: int = 15) -> str:
        """
        Follow GET redirects until we reach:
          https://family.e-klase.lv/?code=...&state=...

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

    async def _request(
        self, method: str, url: str, *, profile_id: str | int | None = None,
        class_id: str | int | None = None, read_json: bool = True, **kwargs: Any,
    ) -> Any:
        """Retry once after a rejected token, retaining the full profile identity."""
        for attempt in range(2):
            await self.ensure_token()
            request = self._s.get if method == "GET" else self._s.post
            async with request(
                url, headers=self._headers_for_profile(profile_id, class_id), **kwargs
            ) as response:
                if response.status == 401 and attempt == 0:
                    self._token = None
                    continue
                response.raise_for_status()
                return await response.json() if read_json else None

    async def sync_profiles(self) -> None:
        """The new UI synchronizes all profiles before fetching them by year."""
        try:
            await self._request("POST", SYNC_URL, read_json=False)
        except aiohttp.ClientError:
            # The UI also continues to fetch profiles after a failed sync.
            pass

    async def get_profiles_raw(self) -> dict[str, Any]:
        await self.sync_profiles()
        return await self._request("GET", PROFILES_URL)

    async def get_active_profiles(self) -> list[dict[str, Any]]:
        return _active_profiles(await self.get_profiles_raw())

    async def switch_profile(self, profile_id: str | int, class_id: str | int) -> None:
        await self._request(
            "POST", SWITCH_URL, profile_id=profile_id, class_id=class_id,
            json={"profileId": profile_id, "classId": class_id}, read_json=False,
        )

    async def get_diary(
        self, profile_id: str | int, from_date: date, to_date: date, *,
        class_id: str | int,
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET", DIARY_URL, profile_id=profile_id, class_id=class_id,
            params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        return _validate_diary(payload)

    def set_credentials(self, username: str, password: str) -> None:
        username = (username or "").strip()
        password = (password or "").strip()
        if username != self._username or password != self._password:
            self._username = username
            self._password = password
            self._token = None  # force re-login
