# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1439 — a data call bounced into the brand app's sign-in.

On an expired session the Audi data endpoints answer with a redirect to the
IDP sign-in instead of a 401. With the IDP session cookie still in the jar the
sign-in completes silently and redirects on to ``myaudi://?code=…``, which
aiohttp refuses to follow (``NonHttpUrlRedirectClientError``). That exception
is neither a 401 nor a transient network error, so it escaped ``_request``: no
refresh ran, and on a car that had never polled successfully the hybrid_full
stale watchdog never armed either — every entity stayed unavailable (reported
on a 2022 Q4 e-tron, EU).

It is an expired bearer in all but status code, so it now recovers like a 401:
refresh (a silent full re-login for hybrid_full) and retry once. The redirect
target carries a live authorization code, so it must never reach an exception
chain that could be logged.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import NonHttpUrlRedirectClientError
from yarl import URL

from custom_components.vag_connect.cariad.api import base as _base
from custom_components.vag_connect.cariad.api.audi import AudiClient
from custom_components.vag_connect.cariad.exceptions import AuthenticationError
from custom_components.vag_connect.cariad.models import TokenSet

CODE = "LIVE_AUTH_CODE_must_not_leak"
URL_DATA = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/VIN/selectivestatus"


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class _Ctx:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> _Resp:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Sess:
    """Replays one scripted outcome per request; records the bearer each used."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.bearers: list[str] = []

    def request(self, method: str, url: str, headers: Any = None, **_: Any) -> _Ctx:
        self.bearers.append((headers or {}).get("Authorization", ""))
        return _Ctx(self._outcomes.pop(0))


def _tokens(access: str) -> TokenSet:
    return TokenSet(
        access_token=access, refresh_token="", id_token="i",
        expires_at=0.0, strategy="hybrid_full",
    )


def _client(sess: _Sess) -> AudiClient:
    c = AudiClient(sess, "u@t.de", "pw")
    c._tokens = _tokens("EXPIRED")
    return c


def _redirect() -> NonHttpUrlRedirectClientError:
    return NonHttpUrlRedirectClientError(URL(f"myaudi:///?code={CODE}&state=s"))


def _rotating_refresh(c: AudiClient, calls: list[str]):
    async def _fake(*, for_command: bool = False, stale_access_token: str | None = None) -> None:
        calls.append(stale_access_token or "")
        c._tokens = _tokens("FRESH")
    return _fake


def _chain(err: BaseException | None) -> list[BaseException]:
    """Every exception a logged traceback of ``err`` would show."""
    out: list[BaseException] = []
    while err is not None and err not in out:
        out.append(err)
        err = err.__cause__ or (None if err.__suppress_context__ else err.__context__)
    return out


def test_app_scheme_redirect_refreshes_and_retries() -> None:
    sess = _Sess([_redirect(), _Resp(200, {"ok": True})])
    c = _client(sess)
    calls: list[str] = []
    c._refresh_tokens = _rotating_refresh(c, calls)

    assert asyncio.run(c._get(URL_DATA)) == {"ok": True}
    assert calls == ["EXPIRED"]                               # one refresh, keyed on the stale bearer
    assert sess.bearers == ["Bearer EXPIRED", "Bearer FRESH"]  # retried with the new one


def test_redirect_after_reauth_raises_authentication_error_without_looping() -> None:
    """One refresh, one retry, then a clean AuthenticationError — which the
    coordinator already turns into a reauth prompt."""
    sess = _Sess([_redirect(), _redirect()])
    c = _client(sess)
    calls: list[str] = []
    c._refresh_tokens = _rotating_refresh(c, calls)

    with pytest.raises(AuthenticationError):
        asyncio.run(c._get(URL_DATA))
    assert len(calls) == 1
    assert len(sess.bearers) == 2


def test_authorization_code_never_enters_the_exception_chain() -> None:
    sess = _Sess([_redirect(), _redirect()])
    c = _client(sess)
    c._refresh_tokens = _rotating_refresh(c, [])

    with pytest.raises(AuthenticationError) as info:
        asyncio.run(c._get(URL_DATA))
    for exc in _chain(info.value):
        assert CODE not in str(exc)
        assert not isinstance(exc, NonHttpUrlRedirectClientError)


def test_failed_reauth_does_not_carry_the_code_either() -> None:
    """The re-login can itself fail; that error must not have the redirect
    (and its code) attached as implicit context."""
    sess = _Sess([_redirect()])
    c = _client(sess)

    async def _boom(**_: Any) -> None:
        raise AuthenticationError("re-login failed")

    c._refresh_tokens = _boom
    with pytest.raises(AuthenticationError, match="re-login failed") as info:
        asyncio.run(c._get(URL_DATA))
    for exc in _chain(info.value):
        assert CODE not in str(exc)
        assert not isinstance(exc, NonHttpUrlRedirectClientError)


def test_probe_treats_app_scheme_redirect_like_a_401() -> None:
    sess = _Sess([_redirect()])
    c = _client(sess)
    with pytest.raises(_base._AuthStormSignal) as info:
        asyncio.run(c._probe_request(URL_DATA))
    assert info.value.__suppress_context__ is True
    for exc in _chain(info.value):
        assert CODE not in str(exc)


def test_normal_responses_are_untouched() -> None:
    sess = _Sess([_Resp(200, {"ok": 1})])
    c = _client(sess)
    calls: list[str] = []
    c._refresh_tokens = _rotating_refresh(c, calls)
    assert asyncio.run(c._get(URL_DATA)) == {"ok": 1}
    assert calls == []
