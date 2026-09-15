# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""v4.7.11 — per-capability ``connected_services`` breakdown (mirrors the
approach of audi_connect_ha #854, merged 2026-09-14).

The CARIAD-BFF userCapabilities array (VW EU / Audi) and the SEAT/CUPRA
``mycar.services`` map already feed the earliest-wins ``subscription_*``
aggregate. This surfaces a compact per-entitlement ``{id, expires_at, status}``
list (capped at 40) so a user can see WHICH connected service is expiring or
errored — exposed as an attribute on the existing capabilities_count diagnostic
sensor, with the sensor STATE (the int count) unchanged.

VW EU is exercised through the real ``_parse_status`` parser; the SEAT/CUPRA
services→connected_services mapping is covered with a pure-python mirror per the
established seat_cupra test convention (get_status carries an HA dep + a ~20-call
async fetch), pinning the real 40-cap constant from the module.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.vag_connect.sensor import (
    VagConnectSensor,
    VagSensorDescription,
)


# ── VW EU / Audi: real _parse_status parser ──────────────────────────────────

def _bff(caps: object) -> object:
    from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient

    client = VWEUClient.__new__(VWEUClient)
    client._vehicle_metadata = {}
    raw = {"userCapabilities": {"capabilitiesStatus": {"value": caps}}}
    return client._parse_status("VINX", raw, parking={})


def test_vw_eu_connected_services_populated() -> None:
    d = _bff([
        {"id": "honkAndFlash", "expirationDate": "2027-03-15T00:00:00Z", "status": []},
        {"id": "parkingPosition", "status": ["MISSING"]},  # errored — still listed
        {"id": "access", "validUntil": "2029-01-01T00:00:00Z"},
    ])
    assert d.connected_services == [
        {"id": "honkAndFlash", "expires_at": "2027-03-15T00:00:00Z", "status": []},
        {"id": "parkingPosition", "expires_at": None, "status": ["MISSING"]},
        {"id": "access", "expires_at": "2029-01-01T00:00:00Z", "status": None},
    ]
    # count sensor state is unaffected — every cap still counted
    assert d.capabilities_count == 3


def test_vw_eu_connected_services_capped_at_40() -> None:
    caps = [{"id": f"svc{i}", "status": []} for i in range(45)]
    d = _bff(caps)
    assert len(d.connected_services) == 40
    assert d.connected_services[0]["id"] == "svc0"
    assert d.connected_services[-1]["id"] == "svc39"
    # the count sensor still reflects the FULL array, not the cap
    assert d.capabilities_count == 45


def test_vw_eu_connected_services_skips_idless_and_nondict() -> None:
    d = _bff([
        "not-a-dict",
        {"expirationDate": "2027-03-15T00:00:00Z"},  # no id
        {"id": "", "status": []},                    # empty id
        {"id": "access", "status": []},
    ])
    assert d.connected_services == [{"id": "access", "expires_at": None, "status": []}]


def test_vw_eu_no_capabilities_leaves_empty() -> None:
    d = _bff("not-a-list")
    assert d.connected_services == []


# ── sensor: attribute exposure + state unchanged ─────────────────────────────

def _coord(vehicle: dict) -> MagicMock:
    coord = MagicMock()
    coord.data = {"X": {"vin": "X", **vehicle}}
    coord.vehicles = coord.data
    coord.is_read_only = MagicMock(return_value=False)
    coord.last_update_success = True
    return coord


def _cap_sensor(vehicle: dict) -> VagConnectSensor:
    desc = VagSensorDescription(
        key="capabilities_count", data_key="capabilities_count"
    )
    return VagConnectSensor(_coord(vehicle), "X", desc)


def test_sensor_exposes_connected_services_attribute() -> None:
    services = [
        {"id": "honkAndFlash", "expires_at": "2027-03-15T00:00:00Z", "status": []},
        {"id": "parkingPosition", "expires_at": None, "status": ["MISSING"]},
    ]
    s = _cap_sensor({"capabilities_count": 2, "connected_services": services})
    assert s.extra_state_attributes == {"connected_services": services}
    # STATE stays the int count
    assert s.native_value == 2


def test_sensor_no_attribute_when_empty() -> None:
    s = _cap_sensor({"capabilities_count": 5, "connected_services": []})
    assert s.extra_state_attributes is None
    assert s.native_value == 5


def test_sensor_no_attribute_when_missing() -> None:
    s = _cap_sensor({"capabilities_count": 5})
    assert s.extra_state_attributes is None
    assert s.native_value == 5


# ── SEAT/CUPRA: services→connected_services mapping (mirror convention) ───────

def _cupra_map(services: dict) -> list[dict]:
    """Pure mirror of the seat_cupra.py services→connected_services loop
    (kept in-test per the established seat_cupra convention: the real
    get_status carries an HA dep + a large async fetch). The 40-cap is
    pinned to the real module constant below."""
    from custom_components.vag_connect.cariad.api.seat_cupra import (
        _CONNECTED_SERVICES_CAP,
    )

    out: list[dict] = []
    for svc_name, svc_data in services.items():
        if not isinstance(svc_data, dict):
            continue
        if len(out) < _CONNECTED_SERVICES_CAP and isinstance(svc_name, str) and svc_name:
            _status = svc_data.get("serviceStatus") or svc_data.get("status")
            _exp = (
                svc_data.get("expirationDate")
                or svc_data.get("validUntil")
                or svc_data.get("expiresAt")
            )
            out.append({
                "id": svc_name,
                "expires_at": _exp if isinstance(_exp, str) and _exp else None,
                "status": _status,
            })
    return out


def test_cupra_cap_constant_is_40() -> None:
    from custom_components.vag_connect.cariad.api.seat_cupra import (
        _CONNECTED_SERVICES_CAP,
    )
    assert _CONNECTED_SERVICES_CAP == 40


def test_cupra_services_mapping_lists_errored_and_active() -> None:
    services = {
        "charging": {"serviceStatus": "ACTIVATED", "expirationDate": "2027-03-15T00:00:00Z"},
        "climatisation": {"status": "EXPIRED"},  # errored — still listed
    }
    assert _cupra_map(services) == [
        {"id": "charging", "expires_at": "2027-03-15T00:00:00Z", "status": "ACTIVATED"},
        {"id": "climatisation", "expires_at": None, "status": "EXPIRED"},
    ]


def test_cupra_services_mapping_capped_at_40() -> None:
    services = {f"svc{i}": {"status": "ACTIVATED"} for i in range(45)}
    assert len(_cupra_map(services)) == 40
