# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1234 (@eddieari, ID.7 GTX) — login-flow step pages and IDP block pages must
not be reported as "invalid_credentials".

With freshly re-verified credentials the portal login cycled through
loginIdentifier / loginAuthenticate (status 200) and browserFeaturesMissingError
/ generalErrorBranded (400), all bucketed as invalid_credentials — sending the
reporter to re-enter a password that was already correct. A genuine wrong password
re-renders the authenticate step WITH a password errorCode, which must still map to
invalid_credentials.
"""
from __future__ import annotations

from custom_components.vag_connect.cariad.auth._eu_data_act import (
    classify_portal_login_failure,
)
from custom_components.vag_connect.cariad.exceptions import (
    PortalInteractionRequiredError,
    TermsAndConditionsError,
)

_URL = "https://identity.vwgroup.io/signin-service/v1/CLIENT/login/authenticate"


def _html(template: str, errorcode: str | None = None) -> str:
    err = f',"error":{{"errorCode":"{errorcode}"}}' if errorcode else ""
    return f'<script>window._IDK = {{templateModel: {{"template":"{template}"{err}}}}};</script>'


def test_login_flow_step_pages_are_not_invalid_credentials():
    for tpl in ("loginIdentifier", "loginAuthenticate"):
        exc, ctx = classify_portal_login_failure(_URL, _html(tpl))
        assert isinstance(exc, PortalInteractionRequiredError), tpl
        assert ctx["classified"] == "portal_interaction_required", tpl


def test_idp_block_pages_are_not_invalid_credentials():
    for tpl in ("browserFeaturesMissingError", "generalErrorBranded"):
        exc, ctx = classify_portal_login_failure(_URL, _html(tpl))
        assert isinstance(exc, PortalInteractionRequiredError), tpl
        assert ctx["classified"] == "portal_interaction_required", tpl


def test_wrong_password_on_authenticate_step_still_invalid_credentials():
    # the crucial guard: a real bad password re-renders loginAuthenticate WITH a
    # password errorCode → must NOT be swallowed as portal_interaction_required.
    exc, ctx = classify_portal_login_failure(
        _URL, _html("loginAuthenticate", "login.error.password_invalid")
    )
    assert exc is None
    assert ctx["classified"] == "invalid_credentials"


def test_pagetype_reason_is_surfaced_and_secret_free():
    exc, ctx = classify_portal_login_failure(_URL, _html("browserFeaturesMissingError"))
    assert "browserFeaturesMissingError" in str(exc)
    assert {"email", "password", "relayState", "code"}.isdisjoint(ctx.keys())


# #1417 (also mps222 in #1337) — a generalErrorBranded / browserFeaturesMissingError
# 400 that LANDS ON the T&C URL is an IDP error, NOT a pending terms interstitial.
# The _TC_MARKERS URL check used to fire first and mis-classify it as
# terms_and_conditions, telling the user to accept terms that are not pending.
_TERMS_URL = (
    "https://identity.vwgroup.io/signin-service/v1/CLIENT/terms-and-conditions"
)


def test_idp_error_on_terms_url_is_not_terms_and_conditions():
    # Both the URL substring (terms-and-conditions ∈ _TC_MARKERS) AND the error
    # pageType are present; the error pageType must win → honest portal error.
    for tpl in ("generalErrorBranded", "browserFeaturesMissingError"):
        exc, ctx = classify_portal_login_failure(_TERMS_URL, _html(tpl))
        assert isinstance(exc, PortalInteractionRequiredError), tpl
        assert ctx["classified"] == "portal_interaction_required", tpl
        assert ctx.get("classified") != "terms_and_conditions", tpl


def test_real_terms_interstitial_still_classifies_as_terms():
    # GUARD: a genuine T&C interstitial (pageType termsAndConditions, no IDP
    # error) must STILL map to terms_and_conditions — the fix only skips the
    # marker buckets for the _NONCRED_ERROR_PAGETYPES, nothing else.
    exc, ctx = classify_portal_login_failure(_TERMS_URL, _html("termsAndConditions"))
    assert isinstance(exc, TermsAndConditionsError)
    assert ctx["classified"] == "terms_and_conditions"
