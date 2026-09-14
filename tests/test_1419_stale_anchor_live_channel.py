# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1419 (Ra72xx) — the stale_data Repair / data_stale binary anchor solely on
``last_seen_at``. The vw.de authproxy channel (LIVE, and PRIMARY for this VW EU
car) never set it, so on a portal-supplemented car the only ``last_seen_at`` was
the EU Data Act snapshot's frozen capture time (98 h) → a false "not updated in
98 h" every day while vw.de delivered live readings.

Fix: (1) the vw.de mappers set ``last_seen_at`` from the freshest
``carCapturedTimestamp`` they map; (2) ``merge_channels`` makes ``last_seen_at``
FRESHEST-wins across sources (not primary-first gap-fill), like the v4.7.8
position pass; a portal-only car keeps its portal value.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.vag_connect.cariad._channel_merge import merge_channels
from custom_components.vag_connect.cariad.auth._website_authproxy import (
    map_charging_to_vehicle_data,
    map_maintenance_to_vehicle_data,
)
from custom_components.vag_connect.cariad.models import VehicleData
from custom_components.vag_connect.coordinator import _capture_age_s

# 98 h-old portal snapshot vs a live vw.de read from a few minutes ago.
_NOW = datetime.now(tz=timezone.utc)
_PORTAL_98H = (_NOW - timedelta(hours=98)).isoformat()
_VWDE_FRESH = (_NOW - timedelta(minutes=5)).isoformat()


def test_merge_picks_fresher_vwde_over_98h_portal() -> None:
    """Portal is PRIMARY (listed first) with a 98 h-frozen last_seen_at; the live
    vw.de read is fresher → the merge must adopt the vw.de value + provenance,
    not let the primary-first gap-fill latch the stale portal snapshot."""
    portal = VehicleData(vin="X", last_seen_at=_PORTAL_98H)
    vwde = VehicleData(vin="X", last_seen_at=_VWDE_FRESH)
    merged = merge_channels([("eu_data_act", portal), ("website_authproxy", vwde)])
    assert merged.last_seen_at == _VWDE_FRESH
    assert merged.field_sources["last_seen_at"] == "website_authproxy"


def test_merge_freshest_wins_regardless_of_order() -> None:
    """vw.de PRIMARY (first) fresh, portal supplementary stale → fresh still wins
    and the stale portal never overwrites it."""
    vwde = VehicleData(vin="X", last_seen_at=_VWDE_FRESH)
    portal = VehicleData(vin="X", last_seen_at=_PORTAL_98H)
    merged = merge_channels([("website_authproxy", vwde), ("eu_data_act", portal)])
    assert merged.last_seen_at == _VWDE_FRESH
    assert merged.field_sources["last_seen_at"] == "website_authproxy"


def test_portal_only_car_keeps_portal_value() -> None:
    """No live channel present → the EU-DA-only car keeps its portal capture time
    (the portal snapshot age must still be trackable for portal_health)."""
    portal = VehicleData(vin="X", last_seen_at=_PORTAL_98H)
    merged = merge_channels([("eu_data_act", portal)])
    assert merged.last_seen_at == _PORTAL_98H
    assert merged.field_sources["last_seen_at"] == "eu_data_act"


def test_source_without_timestamp_cannot_win() -> None:
    """A live channel with NO last_seen_at must not blank the portal's value."""
    portal = VehicleData(vin="X", last_seen_at=_PORTAL_98H)
    vwde = VehicleData(vin="X")  # last_seen_at unset
    merged = merge_channels([("eu_data_act", portal), ("website_authproxy", vwde)])
    assert merged.last_seen_at == _PORTAL_98H


def test_merge_tolerates_datetime_and_iso_mixed_typing() -> None:
    """BFF ships a datetime, vw.de/EU-DA an ISO string — the freshest still wins
    across the heterogeneous typing without raising."""
    bff = VehicleData(vin="X", last_seen_at=_NOW - timedelta(hours=98))  # datetime
    vwde = VehicleData(vin="X", last_seen_at=_VWDE_FRESH)                # ISO string
    merged = merge_channels([("mbb", bff), ("website_authproxy", vwde)])
    assert merged.last_seen_at == _VWDE_FRESH
    assert merged.field_sources["last_seen_at"] == "website_authproxy"


def test_vwde_charging_mapper_sets_last_seen_from_newest_block_ts() -> None:
    """The charging mapper anchors last_seen_at on the NEWEST carCapturedTimestamp
    across batteryStatus/chargingStatus/plugStatus (BFF-shaped blocks)."""
    payload = {"data": {
        "batteryStatus": {"currentSOC_pct": 77,
                          "carCapturedTimestamp": "2026-09-14T08:00:00Z"},
        "chargingStatus": {"chargingState": "readyForCharging",
                           "carCapturedTimestamp": "2026-09-14T09:00:00Z"},
        "plugStatus": {"plugConnectionState": "connected",
                       "carCapturedTimestamp": "2026-09-14T07:00:00Z"},
    }}
    d = map_charging_to_vehicle_data(payload, VehicleData(vin="X"))
    assert d.last_seen_at == "2026-09-14T09:00:00Z"  # the newest block wins


def test_vwde_maintenance_mapper_sets_last_seen_and_keeps_dedicated_field() -> None:
    """The maintenance mapper feeds last_seen_at from carCapturedTimestamp while
    leaving the dedicated maintenance_report_captured_at untouched."""
    payload = {"data": {
        "carCapturedTimestamp": "2026-06-22T18:20:30Z",
        "mileage_km": 162062,
    }}
    d = map_maintenance_to_vehicle_data(payload, VehicleData(vin="X"))
    assert d.maintenance_report_captured_at == "2026-06-22T18:20:30Z"
    assert d.last_seen_at == "2026-06-22T18:20:30Z"


def test_vwde_mappers_accumulate_newest_across_payloads() -> None:
    """Charging then maintenance on the same VehicleData: last_seen_at ends on the
    freshest capture across BOTH payloads (advance-only), never regressing."""
    d = VehicleData(vin="X")
    map_charging_to_vehicle_data(
        {"data": {"batteryStatus": {"currentSOC_pct": 50,
                                    "carCapturedTimestamp": "2026-09-14T09:00:00Z"}}},
        d,
    )
    # older maintenance capture must NOT roll last_seen_at backwards
    map_maintenance_to_vehicle_data(
        {"data": {"carCapturedTimestamp": "2026-09-10T00:00:00Z", "mileage_km": 1}},
        d,
    )
    assert d.last_seen_at == "2026-09-14T09:00:00Z"


def test_capture_age_reflects_fresh_value_after_merge() -> None:
    """Coordinator-level: _capture_age_s over the MERGED payload reflects the live
    vw.de capture (~minutes), not the 98 h portal snapshot → no false stale flag."""
    portal = VehicleData(vin="X", last_seen_at=_PORTAL_98H)
    vwde = VehicleData(vin="X", last_seen_at=_VWDE_FRESH)
    merged = merge_channels([("eu_data_act", portal), ("website_authproxy", vwde)])
    age = _capture_age_s({"last_seen_at": merged.last_seen_at})
    assert age is not None
    assert age < 3600  # minutes, not the 98 h that would trip the Repair
