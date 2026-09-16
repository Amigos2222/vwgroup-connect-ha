# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#584 (pp2stay, Passat GTE 2017, Netherlands) — the MBB fs-car action path
carries a ``{country}`` market segment. It used to be the id_token's claim or a
hard ``DE``; a claim-less token routed a Dutch car to ``/VW/DE/`` and the
market-scoped charger action was refused (403 batterycharge.auth.forbidden)
while both S-PIN legs (VIN-only) passed. Now: token claim → Home Assistant
country → DE, and the command logs service/grant/host/market/source (no VIN).
"""
from __future__ import annotations

import base64
import json

from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient


def _client(id_token: str = "", ha_country: str | None = None) -> VWEUClient:
    c = VWEUClient.__new__(VWEUClient)
    c._tokens = None
    if id_token:
        tok = type("T", (), {})()
        tok.id_token = id_token
        tok.strategy = "mbb"
        c._tokens = tok
    if ha_country is not None:
        c._ha_country = ha_country
    return c


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}."


def test_token_claim_wins() -> None:
    c = _client(_jwt({"locale": "nl-NL"}), ha_country="DE")
    assert c._mbb_country_with_source() == ("NL", "id_token")


def test_ha_country_fills_a_claimless_token() -> None:
    c = _client(_jwt({"sub": "x"}), ha_country="NL")
    assert c._mbb_country_with_source() == ("NL", "home-assistant country")
    assert c._mbb_country() == "NL"


def test_default_de_when_nothing_known() -> None:
    c = _client("", ha_country="")
    assert c._mbb_country_with_source() == ("DE", "default")


def test_ha_country_is_normalised_and_validated() -> None:
    assert _client("", ha_country=" nl ")._mbb_country() == "NL"
    assert _client("", ha_country="Netherlands")._mbb_country() == "DE"  # not ISO-2 → default
    assert _client("", ha_country="1X")._mbb_country() == "DE"


def test_token_not_consulted_when_not_allowed() -> None:
    c = _client(_jwt({"country": "CH"}), ha_country="NL")
    assert c._mbb_country(token_ok=False) == "NL"
    assert c._mbb_country(token_ok=True) == "CH"
