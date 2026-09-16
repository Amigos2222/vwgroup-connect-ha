# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Škoda public-API keys — capability gate + true list-response shape (8.16.0).

MyŠkoda 8.16.0 gates the whole key-management feature on the capability id
``PUBLIC_API_KEY_MANAGEMENT``, and its list route answers an ``ApiKeysResponseDto``
that DOES carry a per-key list plus documentation links. These tests pin: the gate
skips the mint (and records a PII-free reason) only on explicit absence, the listing
is flattened without losing the original body, and the key name we send stays inside
the app's name character class.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.vag_connect.cariad.api.skoda import (
    _KEY_NAME_ALLOWED,
    _OFFICIAL_KEY_NAME,
    SkodaClient,
    _api_key_listing_extras,
    _api_key_statuses,
)
from custom_components.vag_connect.const import CONF_BRAND, CONF_SKODA_OFFICIAL_KEYS
from custom_components.vag_connect.coordinator import (
    _SKODA_KEY_MGMT_CAPABILITY,
    VagConnectCoordinator,
    _capability_listed,
)

VIN1, VIN2 = "TMBEL9NEXP1000001", "TMBEL9NEXP1000002"
_GATE_REASON = "gate: PUBLIC_API_KEY_MANAGEMENT capability not present for this car"


def _caps(*ids: str) -> dict[str, object]:
    """A cached capability document in the shape SkodaClient.get_capabilities emits."""
    return {"capabilities": [{"id": i, "statuses": []} for i in ids]}


def _client() -> MagicMock:
    cl = MagicMock()
    cl.can_mint_official_key = True
    cl.list_api_keys = AsyncMock(return_value={
        "maxKeys": 5,
        "vehicleKeys": [
            {"vin": VIN1, "keysRemaining": 5},
            {"vin": VIN2, "keysRemaining": 5},
        ],
    })
    cl.mint_api_key = AsyncMock(side_effect=lambda vin: {
        "id": f"id-{vin}", "key": f"KEY-{vin}", "validUntil": "2027-09-01T00:00:00Z"})
    cl.arm_supplementary_official = MagicMock()
    cl.probe_outcomes = {}  # real dict so _skoda_probe captures it
    return cl


def _coord(client: MagicMock, caps: dict[str, dict[str, object]] | None = None):
    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c.hass = MagicMock()
    c.entry = MagicMock()
    c.entry.entry_id = "e1"
    c.entry.data = {CONF_BRAND: "skoda"}
    c._cariad_client = client
    if caps is not None:
        c.vehicle_capabilities = caps
    return c


# ── (1) the capability gate ──────────────────────────────────────────────────


def test_capability_listed_tristate() -> None:
    assert _capability_listed(_caps(_SKODA_KEY_MGMT_CAPABILITY), _SKODA_KEY_MGMT_CAPABILITY) is True
    assert _capability_listed(_caps("CHARGING"), _SKODA_KEY_MGMT_CAPABILITY) is False
    # unknown: no document, wrong type, no list, or a backend-flagged errors block
    assert _capability_listed(None, _SKODA_KEY_MGMT_CAPABILITY) is None
    assert _capability_listed({}, _SKODA_KEY_MGMT_CAPABILITY) is None
    assert _capability_listed({"capabilities": "nope"}, _SKODA_KEY_MGMT_CAPABILITY) is None
    assert _capability_listed(
        {"capabilities": [], "errors": ["UNAVAILABLE_SERVICE_PLATFORM_CAPABILITIES"]},
        _SKODA_KEY_MGMT_CAPABILITY,
    ) is None


def test_capability_listed_ignores_status_limitations() -> None:
    """Presence, not usability: a listed-but-limited capability still exists."""
    doc = {"capabilities": [
        {"id": _SKODA_KEY_MGMT_CAPABILITY, "statuses": ["INSUFFICIENT_BATTERY_LEVEL"]},
    ]}
    assert _capability_listed(doc, _SKODA_KEY_MGMT_CAPABILITY) is True


def test_gate_skips_mint_and_records_reason() -> None:
    cl = _client()
    c = _coord(cl, {VIN1: _caps("CHARGING", "PARKING_POSITION")})
    asyncio.run(c._auto_enroll_skoda_official([VIN1]))
    cl.mint_api_key.assert_not_awaited()
    assert cl.probe_outcomes["skoda_official"] == _GATE_REASON
    c.hass.config_entries.async_update_entry.assert_not_called()


def test_gate_is_per_car_and_does_not_block_the_other_vin() -> None:
    cl = _client()
    c = _coord(cl, {
        VIN1: _caps("CHARGING"),
        VIN2: _caps(_SKODA_KEY_MGMT_CAPABILITY),
    })
    with patch("custom_components.vag_connect.repairs.raise_issue_skoda_official_enrolled"):
        asyncio.run(c._auto_enroll_skoda_official([VIN1, VIN2]))
    assert [call.args[0] for call in cl.mint_api_key.await_args_list] == [VIN2]
    data = c.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert set(data[CONF_SKODA_OFFICIAL_KEYS]) == {VIN2}


def test_capability_present_mints() -> None:
    cl = _client()
    c = _coord(cl, {VIN1: _caps(_SKODA_KEY_MGMT_CAPABILITY, "CHARGING")})
    with patch("custom_components.vag_connect.repairs.raise_issue_skoda_official_enrolled"):
        asyncio.run(c._auto_enroll_skoda_official([VIN1]))
    cl.mint_api_key.assert_awaited_once()
    assert cl.probe_outcomes["skoda_official"] == "enrolled (1 new key(s))"


def test_unknown_capabilities_keep_previous_behaviour() -> None:
    """No capability document (never fetched / fetch failed) → unchanged: still mint."""
    cl = _client()
    c = _coord(cl)  # no vehicle_capabilities attribute at all
    with patch("custom_components.vag_connect.repairs.raise_issue_skoda_official_enrolled"):
        asyncio.run(c._auto_enroll_skoda_official([VIN1]))
    cl.mint_api_key.assert_awaited_once()

    cl2 = _client()
    c2 = _coord(cl2, {VIN1: {"capabilities": [], "errors": ["MISSING_RENDER"]}})
    with patch("custom_components.vag_connect.repairs.raise_issue_skoda_official_enrolled"):
        asyncio.run(c2._auto_enroll_skoda_official([VIN1]))
    cl2.mint_api_key.assert_awaited_once()


def test_gate_does_not_poison_the_session_retry() -> None:
    """A gated VIN is not marked 'attempted', so it enrols once the cache updates."""
    cl = _client()
    c = _coord(cl, {VIN1: _caps("CHARGING")})
    asyncio.run(c._auto_enroll_skoda_official([VIN1]))
    cl.mint_api_key.assert_not_awaited()
    c.vehicle_capabilities[VIN1] = _caps(_SKODA_KEY_MGMT_CAPABILITY)
    with patch("custom_components.vag_connect.repairs.raise_issue_skoda_official_enrolled"):
        asyncio.run(c._auto_enroll_skoda_official([VIN1]))
    cl.mint_api_key.assert_awaited_once()


# ── (2) the true list-response shape ─────────────────────────────────────────

_LISTING = {
    "maxKeys": 5,
    "vehicleKeys": [
        {
            "vin": VIN1.lower(),
            "keysRemaining": 3,
            "keys": [
                {"id": "k1", "name": "Home Assistant", "status": "ACTIVE",
                 "validUntil": "2027-09-01T00:00:00Z"},
                {"id": "k2", "name": "My script", "status": "EXPIRED",
                 "validUntil": "2026-01-01T00:00:00Z"},
            ],
        },
        {"vin": VIN2, "keysRemaining": 5, "keys": []},
    ],
    "links": {
        "apiDocumentation": "https://example.invalid/docs",
        "termsAndConditions": "https://example.invalid/terms",
    },
}


def test_listing_extras_parse_keys_and_links() -> None:
    extras = _api_key_listing_extras(_LISTING)
    assert set(extras["keys_by_vin"]) == {VIN1, VIN2}  # VIN keys upper-cased
    assert extras["keys_by_vin"][VIN1][0] == {
        "id": "k1", "name": "Home Assistant", "status": "ACTIVE",
        "validUntil": "2027-09-01T00:00:00Z",
    }
    assert extras["keys_by_vin"][VIN2] == []
    assert extras["api_documentation_url"] == "https://example.invalid/docs"
    assert extras["terms_and_conditions_url"] == "https://example.invalid/terms"


def test_listing_extras_are_total_on_junk() -> None:
    assert _api_key_listing_extras({}) == {
        "keys_by_vin": {}, "api_documentation_url": None,
        "terms_and_conditions_url": None,
    }
    junk = {"vehicleKeys": ["x", {}, {"vin": VIN1, "keys": "nope"}], "links": []}
    extras = _api_key_listing_extras(junk)
    assert extras["keys_by_vin"] == {VIN1: []}
    assert extras["api_documentation_url"] is None


def test_statuses_are_distinct_and_sorted() -> None:
    assert _api_key_statuses(_api_key_listing_extras(_LISTING)["keys_by_vin"]) == [
        "ACTIVE", "EXPIRED",
    ]
    assert _api_key_statuses({VIN1: [{"status": None}, {"status": "  "}]}) == []


def _native_client() -> SkodaClient:
    c = SkodaClient(MagicMock(), "u@t.de", "pw")
    c._tokens = SimpleNamespace(strategy="", access_token="eyJ.a.b")  # type: ignore[assignment]
    c._eu_portal = None
    return c


def test_list_api_keys_keeps_body_and_adds_extras() -> None:
    c = _native_client()
    c._get = AsyncMock(return_value=_LISTING)  # type: ignore[method-assign]
    out = asyncio.run(c.list_api_keys())
    assert out is not None
    # original body intact (the quota + multi-integration callers read these)
    assert out["maxKeys"] == 5
    assert out["vehicleKeys"][0]["keysRemaining"] == 3
    assert out["vehicleKeys"][0]["keys"][1]["id"] == "k2"
    assert out["links"]["apiDocumentation"].endswith("/docs")
    # plus the derived entries
    assert out["keys_by_vin"][VIN1][1]["status"] == "EXPIRED"
    assert out["terms_and_conditions_url"].endswith("/terms")


def test_list_probe_is_pii_free_and_reports_statuses() -> None:
    c = _native_client()
    c._get = AsyncMock(return_value=_LISTING)  # type: ignore[method-assign]
    asyncio.run(c.list_api_keys())
    probe = c.probe_outcomes["skoda_official_keygen_list"]
    assert probe == "GET 2xx maxKeys=5 vins=2 keys=2 statuses=[ACTIVE,EXPIRED]"
    assert VIN1 not in probe and VIN1.lower() not in probe and "k1" not in probe


# ── (3) observe statuses, never auto-delete ──────────────────────────────────


def test_listing_never_deletes_a_stale_looking_key() -> None:
    c = _native_client()
    c._get = AsyncMock(return_value=_LISTING)  # type: ignore[method-assign]
    c._request = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(c.list_api_keys())
    c._request.assert_not_awaited()  # EXPIRED key present, still untouched


# ── (4) the key name we send obeys the app's name character class ────────────


def test_key_name_matches_app_regex() -> None:
    assert re.match(_KEY_NAME_ALLOWED, _OFFICIAL_KEY_NAME)
    assert "(" not in _OFFICIAL_KEY_NAME and ")" not in _OFFICIAL_KEY_NAME
    # the parenthesised variant would NOT pass the app's class — pinned so a future
    # rename can't quietly reintroduce it
    assert not re.match(_KEY_NAME_ALLOWED, "Home Assistant (vag_connect)")


def test_mint_sends_the_compliant_name() -> None:
    c = _native_client()
    c._post = AsyncMock(return_value={"id": "k1", "key": "S", "validUntil": "z"})  # type: ignore[method-assign]
    asyncio.run(c.mint_api_key(VIN1))
    sent = c._post.call_args.kwargs["json"]["name"]
    assert sent == _OFFICIAL_KEY_NAME
    assert re.match(_KEY_NAME_ALLOWED, sent)
