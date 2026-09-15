# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#465/#632/#966 (toglo) — OPT-IN vw.de credential re-login on a dead resume.

The Volkswagen.de read channel resumes its session silently (prompt=none). When
the identity SSO behind that resume dies, ``refresh()`` normally raises so the
user re-adds the channel — because falling back to the interactive login would
make VW e-mail a fresh OTP on every poll. This adds an OPT-IN escape hatch
(``allow_cred_relogin``, default OFF, armed from CONF_VWDE_CRED_RELOGIN): on the
dead-SSO path ``refresh()`` may do ONE cooldown-bounded stored-password login via
``begin_login()``. On "ok" the session resumes (cookies rotate → the normal
persist path saves them); an OTP challenge is NEVER auto-answered (we log once and
raise as before); bad credentials raise as before. A monotonic cooldown
(``_CRED_RELOGIN_COOLDOWN_S`` = 900 s) plus the v4.7.10 per-cycle resume latch
bound retries so no OTP-email storm / loop is possible.

Parity ADOPT (shape only) from the other vw.de cookie-camp project; the code here
is our own.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.vag_connect.cariad.auth._website_authproxy import (
    _CRED_RELOGIN_COOLDOWN_S,
    WebsiteAuthProxyConnector,
)
from custom_components.vag_connect.cariad.exceptions import AuthenticationError

_SITE_LANDING = (
    "https://www.volkswagen.de/de/besitzer-und-nutzer/myvolkswagen.html"
)
_LOGIN_HTML = (
    '<html><body>'
    '<form action="/u/login?state=AUTH0STATE">'
    '<input type="hidden" name="state" value="AUTH0STATE">'
    '<input type="hidden" name="hmac" value="loginhmac">'
    '</form></body></html>'
)


class _FakeResp:
    def __init__(self, url: str, *, status: int = 200, text: str = "") -> None:
        self.url = url
        self.status = status
        self._text = text
        self.history: tuple[Any, ...] = ()

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def text(self, errors: str | None = None) -> str:
        return self._text


class _DeadSSOSession:
    """The silent-resume GET lands on the IDP /u/login (dead SSO). The credential
    POST that ``begin_login`` fires afterwards routes to ``post_landing``."""

    def __init__(self, post_landing: str, *, post_status: int = 200) -> None:
        self._post_landing = post_landing
        self._post_status = post_status
        self.gets = 0
        self.posts: list[dict[str, Any]] = []

    def get(self, url: str, **kw: Any) -> _FakeResp:
        self.gets += 1
        # Both refresh()'s resume probe AND begin_login()'s login-page GET hit the
        # same authproxy login trigger; return the IDP login page for both.
        return _FakeResp(
            "https://identity.vwgroup.io/u/login?state=AUTH0STATE",
            text=_LOGIN_HTML,
        )

    def post(self, url: str, **kw: Any) -> _FakeResp:
        self.posts.append({"url": url, "data": kw.get("data")})
        return _FakeResp(self._post_landing, status=self._post_status,
                         text="<html>x</html>")


def _conn(session: Any) -> WebsiteAuthProxyConnector:
    return WebsiteAuthProxyConnector(session, "user@example.com", "secret")  # type: ignore[arg-type]


# ── (1) off by default ──────────────────────────────────────────────────────

def test_off_by_default_never_calls_begin_login() -> None:
    """Default OFF: a dead-SSO refresh raises exactly as before and NEVER touches
    the credential login — today's behaviour is preserved bit-for-bit."""
    conn = _conn(_DeadSSOSession(_SITE_LANDING))
    conn.logged_in = True
    conn.begin_login = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(AuthenticationError):
        asyncio.run(conn.refresh())

    conn.begin_login.assert_not_awaited()
    assert conn.allow_cred_relogin is False  # the shipped default


# ── (2) cooldown ────────────────────────────────────────────────────────────

def test_cooldown_blocks_a_second_attempt_within_the_window() -> None:
    """Opted in, a login was just attempted → the next call is a no-op until the
    900 s window elapses; the stamp is what gates it."""
    conn = _conn(_DeadSSOSession(_SITE_LANDING))
    conn.allow_cred_relogin = True
    conn.begin_login = AsyncMock(return_value="ok")  # type: ignore[method-assign]

    conn._last_cred_relogin = time.monotonic()  # just attempted
    assert asyncio.run(conn.relogin_if_allowed()) is False
    conn.begin_login.assert_not_awaited()

    # Pretend the last attempt was longer ago than the cooldown → proceeds.
    conn._last_cred_relogin -= _CRED_RELOGIN_COOLDOWN_S + 1.0
    assert asyncio.run(conn.relogin_if_allowed()) is True
    conn.begin_login.assert_awaited_once()


def test_no_password_is_a_noop() -> None:
    """No stored password → nothing to replay, so it never logs in."""
    conn = _conn(_DeadSSOSession(_SITE_LANDING))
    conn.allow_cred_relogin = True
    conn._password = ""
    conn.begin_login = AsyncMock(return_value="ok")  # type: ignore[method-assign]

    assert asyncio.run(conn.relogin_if_allowed()) is False
    conn.begin_login.assert_not_awaited()


# ── (3) "ok" path resumes ───────────────────────────────────────────────────

def test_ok_path_resumes_the_session_without_raising() -> None:
    """Opted in + the stored password logs back in → refresh() returns cleanly,
    the session is live, and the credential POST carried the real password."""
    session = _DeadSSOSession(_SITE_LANDING)
    conn = _conn(session)
    conn.allow_cred_relogin = True
    conn.logged_in = True

    asyncio.run(conn.refresh())  # must NOT raise

    assert conn.logged_in is True
    assert session.posts, "begin_login should have POSTed credentials"
    assert session.posts[-1]["data"]["password"] == "secret"
    # The cooldown was stamped so an immediately-following dead poll can't retry.
    assert conn._last_cred_relogin > float("-inf")


# ── (4) OTP path never submits, raises as before ────────────────────────────

def test_otp_path_never_submits_and_raises() -> None:
    """A credential re-login that reaches an e-mail challenge must NOT auto-answer
    it (that is the OTP-storm the silent path exists to avoid): submit_otp is
    never called and refresh() raises the usual re-add verdict."""
    session = _DeadSSOSession(
        "https://identity.vwgroup.io/u/email-challenge?state=AUTH0STATE"
    )
    conn = _conn(session)
    conn.allow_cred_relogin = True
    conn.logged_in = True
    conn.submit_otp = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(AuthenticationError):
        asyncio.run(conn.refresh())

    conn.submit_otp.assert_not_awaited()
    assert conn.logged_in is False


# ── (5) bad-credentials path raises as before ───────────────────────────────

def test_auth_error_path_raises() -> None:
    """A credential login that fails auth (HTTP 401) surfaces the usual re-add
    verdict — the opt-in never turns a bad password into a silent success."""
    session = _DeadSSOSession(
        "https://identity.vwgroup.io/u/login?state=AUTH0STATE", post_status=401
    )
    conn = _conn(session)
    conn.allow_cred_relogin = True
    conn.logged_in = True

    with pytest.raises(AuthenticationError):
        asyncio.run(conn.refresh())
    assert conn.logged_in is False


# ── (6) per-cycle latch still short-circuits (no double resume / loop) ───────

def test_per_cycle_latch_short_circuits_before_any_relogin() -> None:
    """The v4.7.10 latch still applies: when a proactive roll already declared the
    SSO dead this cycle, the reactive refresh() re-raises WITHOUT a second GET —
    and therefore without a credential login either, so opting in can't defeat the
    one-attempt-per-cycle guard."""
    session = _DeadSSOSession(_SITE_LANDING)
    conn = _conn(session)
    conn.allow_cred_relogin = True
    conn.logged_in = True
    conn._resume_dead_this_cycle = True
    conn.begin_login = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(AuthenticationError):
        asyncio.run(conn.refresh())

    conn.begin_login.assert_not_awaited()
    assert session.gets == 0  # not even the resume GET fired
