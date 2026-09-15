# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#465 (BooM80, VW Tiguan III MQB evo 2026) — portal value-less field visibility.

VW's EU Data Act export can ship a data point's NAME + capture timestamp but
omit the ``value`` (BooM80's official export: 10 field names, only 3 valued), so
the raw feed reads "complete" while most fields carry nothing. Nothing is broken
— the walker still drops a point with no value to surface — but the count of
delivered-empty names is now recorded so diagnostics / the portal-feed-health
sensor can say "VW sent nothing", not "we dropped it".
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.vag_connect.binary_sensor import VagSourceConnectivitySensor
from custom_components.vag_connect.cariad.auth._eu_data_act import (
    _VALUELESS_FIELD_CAP,
    EUDataActConnector,
    _walk_fields,
)
from custom_components.vag_connect.coordinator import VagConnectCoordinator

_TS = "2026-09-14T10:00:00Z"


def _point(name: str, value: object | None = None, ts: str | None = _TS) -> dict:
    """Build a portal data point; omit ``value`` for a value-less delivery."""
    node: dict[str, object] = {"dataFieldName": name}
    if ts is not None:
        node["carCapturedTimestamp"] = ts
    if value is not None:
        node["value"] = value
    return node


# ── the walker classification ────────────────────────────────────────────────

def test_valued_and_valueless_split_counts_and_names() -> None:
    # BooM80's shape: a handful of real readings + a majority delivered empty.
    payload = {
        "vin": "WVWZZZ5NZ00000001",  # envelope — excluded from both sets
        "data": [
            _point("battery_state_report.soc", "80"),
            _point("mileage.odometer", "12345"),
            _point("charging.target_soc", "90"),
            # seven names VW shipped with a timestamp but NO value:
            _point("battery_state_report.charge_power"),
            _point("battery_state_report.charge_rate"),
            _point("charging.remaining_time"),
            _point("climatisation.status"),
            _point("position.latitude"),
            _point("position.longitude"),
            _point("tyre.front_left_pressure"),
        ],
    }
    delivery: dict[str, set[str]] = {}
    fields = _walk_fields(payload, None, None, None, None, delivery)

    assert delivery["valued"] == {
        "battery_state_report.soc",
        "mileage.odometer",
        "charging.target_soc",
    }
    assert len(delivery["valued"]) == 3
    assert len(delivery["valueless"]) == 7
    assert "battery_state_report.charge_power" in delivery["valueless"]
    assert "position.latitude" in delivery["valueless"]
    # the value-less points are NOT surfaced as readings (nothing to surface),
    # only the three valued names carry a value into the flattened dict.
    assert fields.get("battery_state_report.soc") == "80"
    assert "battery_state_report.charge_power" not in fields


def test_envelope_and_credential_names_excluded_from_both_sets() -> None:
    payload = {
        "data": [
            _point("vin", "WVWZZZ..."),          # envelope, valued → excluded
            _point("state", "x"),                 # envelope, valued → excluded
            _point("user_id"),                    # envelope, value-less → excluded
            _point("timestamp"),                  # envelope, value-less → excluded
            _point("idp_idt"),                    # credential, value-less → excluded
            _point("battery_state_report.soc", "55"),
            _point("charging.remaining_time"),    # real value-less
        ],
    }
    delivery: dict[str, set[str]] = {}
    _walk_fields(payload, None, None, None, None, delivery)
    assert delivery["valued"] == {"battery_state_report.soc"}
    assert delivery["valueless"] == {"charging.remaining_time"}


def test_no_timestamp_missing_value_is_not_counted_valueless() -> None:
    # "arrived WITH a timestamp but WITHOUT a value" is the signal. A named node
    # with neither a value nor its OWN capture timestamp (e.g. a plain container)
    # must not be miscounted — own_ts gates it.
    payload = {
        "data": [
            _point("charging.remaining_time"),                     # own ts, no value
            _point("some.container", value=None, ts=None),         # no ts, no value
        ],
    }
    delivery: dict[str, set[str]] = {}
    _walk_fields(payload, None, None, None, None, delivery)
    assert delivery.get("valueless") == {"charging.remaining_time"}


def test_delivery_out_optional_is_no_op_when_absent() -> None:
    # Callers that don't ask for the split (parse_export_zip) pass nothing.
    payload = {"data": [_point("battery_state_report.soc", "80"), _point("x.empty")]}
    fields = _walk_fields(payload)  # must not raise
    assert fields.get("battery_state_report.soc") == "80"


# ── the connector's per-parse recording + cap ────────────────────────────────

def test_connector_records_split_and_resets_per_parse() -> None:
    c = EUDataActConnector.__new__(EUDataActConnector)
    c._record_field_delivery({
        "valued": {"a", "b", "c"},
        "valueless": {"x", "y"},
    })
    assert c.last_valued_count == 3
    assert c.last_valueless_count == 2
    assert c.last_valueless_fields == ["x", "y"]  # sorted
    # a subsequent parse fully REPLACES the prior poll's numbers (never merges).
    c._record_field_delivery({"valued": {"a"}, "valueless": set()})
    assert c.last_valued_count == 1
    assert c.last_valueless_count == 0
    assert c.last_valueless_fields == []


def test_connector_caps_valueless_sample_but_keeps_full_count() -> None:
    c = EUDataActConnector.__new__(EUDataActConnector)
    many = {f"field_{i:03d}" for i in range(_VALUELESS_FIELD_CAP + 10)}
    c._record_field_delivery({"valued": set(), "valueless": many})
    assert c.last_valueless_count == _VALUELESS_FIELD_CAP + 10   # full total kept
    assert len(c.last_valueless_fields) == _VALUELESS_FIELD_CAP  # sample capped
    assert c.last_valueless_fields == sorted(many)[:_VALUELESS_FIELD_CAP]


# ── coordinator channel-status wiring ────────────────────────────────────────

def _coord(client) -> VagConnectCoordinator:
    from unittest.mock import MagicMock
    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c.hass = MagicMock()
    c.entry = MagicMock()
    c.entry.data = {"brand": "volkswagen"}
    c._cariad_client = client
    return c


def test_channel_status_carries_field_delivery() -> None:
    client = SimpleNamespace(
        _supplementary_authproxy=None, _supplementary_tibber=None,
        _supplementary_official=None, _eu_portal=None,
        _supplementary_eu_portal=object(),
    )
    c = _coord(client)
    data = {
        "field_sources": {"battery_soc": "eu_data_act"},
        "portal_health": "ok",
        "fields_with_values": 3,
        "fields_delivered_without_values": 7,
        "valueless_field_names": ["battery_state_report.charge_power"],
    }
    st = c._compute_channel_status("WVWZZZ5NZ00000001", data)
    assert st["eu_data_act"]["fields_with_values"] == 3
    assert st["eu_data_act"]["fields_delivered_without_values"] == 7
    assert st["eu_data_act"]["valueless_field_names"] == [
        "battery_state_report.charge_power"
    ]


# ── the portal-feed-health sensor's extra attributes ─────────────────────────

def _sensor(channel_status: dict) -> VagSourceConnectivitySensor:
    s = VagSourceConnectivitySensor.__new__(VagSourceConnectivitySensor)
    s._token = "eu_data_act"
    s._vin = "WVWZZZ5NZ00000001"
    s.coordinator = SimpleNamespace(
        data={"WVWZZZ5NZ00000001": {"channel_status": channel_status}}
    )
    return s


def test_sensor_surfaces_field_delivery_attributes() -> None:
    s = _sensor({"eu_data_act": {
        "armed": True, "active": True, "failover": False,
        "active_values": 3, "total_values": 3, "last_active": _TS,
        "portal_health": "ok", "minutes_since_last_snapshot": 7,
        "fields_with_values": 3, "fields_delivered_without_values": 7,
        "valueless_field_names": ["battery_state_report.charge_power"],
    }})
    a = s._platform_attributes()
    assert a["fields_with_values"] == 3
    assert a["fields_delivered_without_values"] == 7
    assert a["valueless_field_names"] == ["battery_state_report.charge_power"]
    assert a["portal_health"] == "ok"  # existing attrs still present


def test_sensor_omits_field_delivery_when_absent() -> None:
    # a non-portal channel (no field-delivery keys) must not gain empty attrs.
    s = _sensor({"eu_data_act": {
        "armed": True, "active": False, "failover": False,
        "active_values": 0, "total_values": 0, "last_active": None,
    }})
    a = s._platform_attributes() or {}
    assert "fields_with_values" not in a
    assert "valueless_field_names" not in a
