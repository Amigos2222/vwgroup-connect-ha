# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Porsche CHARGING_SESSION_HISTORY parsing (v4.7.12).

Grounded on the ``CHARGING_SESSION_HISTORY`` measurement of My Porsche
20.26.37 (verified in both flavours of that build — the older "ROW-only" note
in ``porsche.py`` was wrong). Its value is
``{"lastModified": <ISO>, "list": [<session>, ...]}`` and each session carries
``startChargingDateTimeWithOffset`` / ``netDurationS`` / ``chargeType`` /
``totalChargedEnergykWh`` / ``peakChargingPowerkW`` / ``startSoC`` /
``endSoC``.

The key behaviours pinned here: the list has NO ordering guarantee, so the
newest session must be derived from the start stamp rather than from
``list[0]``; a measurement whose own status says it is disabled is dropped by
the shared ``isEnabled`` filter; and a garbage entry is skipped instead of
taking the whole parse (or the last-session sensors) down with it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.vag_connect.cariad.api.porsche import _MEASUREMENTS, PorscheClient

_VIN = "WP0ZZZ99ZTS300001"


def _client() -> PorscheClient:
    return PorscheClient.__new__(PorscheClient)


async def _status(measurements: list[dict]) -> object:
    c = _client()
    c._get = AsyncMock(return_value={"measurements": measurements})
    return await c.get_status(_VIN)


# Two synthetic sessions in the WRONG order (older first) — the real payload
# gives no ordering promise, so this is the case that matters.
_OLDER = {
    "id": "s-older",
    "startChargingDateTimeWithOffset": "2026-09-01T22:14:00+02:00",
    "endChargingDateTimeWithOffset": "2026-09-02T04:02:00+02:00",
    "plugInDateTimeWithOffset": "2026-09-01T22:10:00+02:00",
    "plugOutDateTimeWithOffset": "2026-09-02T07:30:00+02:00",
    "netDurationS": 20880,
    "chargeType": "AC",
    "averageChargingPowerkW": 6.1,
    "peakChargingPowerkW": 10.9,
    "totalChargedEnergykWh": 35.421,
    "startSoC": 22,
    "endSoC": 80,
}
_NEWER = {
    "id": "s-newer",
    "startChargingDateTimeWithOffset": "2026-09-14T11:03:00+02:00",
    "endChargingDateTimeWithOffset": "2026-09-14T11:29:00+02:00",
    "plugInDateTimeWithOffset": "2026-09-14T11:01:00+02:00",
    "plugOutDateTimeWithOffset": "2026-09-14T11:33:00+02:00",
    "netDurationS": 1560,
    "chargeType": "DC",
    "averageChargingPowerkW": 128.4,
    "peakChargingPowerkW": 241.7,
    "totalChargedEnergykWh": 55.6,
    "startSoC": 14,
    "endSoC": 78,
}


def _history(sessions: list, *, enabled: bool = True) -> dict:
    return {
        "key": "CHARGING_SESSION_HISTORY",
        "status": {"isEnabled": enabled},
        "value": {"lastModified": "2026-09-14T11:35:00+02:00", "list": sessions},
    }


class TestLastSession:
    @pytest.mark.asyncio
    async def test_newest_picked_from_unordered_list(self) -> None:
        d = await _status([_history([_OLDER, _NEWER])])
        # DC session on the 14th, not the AC one the payload listed first.
        assert d.last_charging_session_start == "2026-09-14T11:03:00+02:00"
        assert d.last_charging_session_current_type == "DC"

    @pytest.mark.asyncio
    async def test_kwh_and_duration_mapped(self) -> None:
        d = await _status([_history([_OLDER, _NEWER])])
        assert d.last_charging_session_kwh == 55.6
        assert d.last_charging_session_duration_min == 26  # 1560 s

    @pytest.mark.asyncio
    async def test_recent_list_newest_first_with_porsche_extras(self) -> None:
        d = await _status([_history([_OLDER, _NEWER])])
        assert d.recent_charging_sessions == [
            {
                "started_at": "2026-09-14T11:03:00+02:00",
                "kwh": 55.6,
                "duration_min": 26,
                "start_soc": 14,
                "end_soc": 78,
                "peak_kw": 241.7,
            },
            {
                "started_at": "2026-09-01T22:14:00+02:00",
                "kwh": 35.42,
                "duration_min": 348,
                "start_soc": 22,
                "end_soc": 80,
                "peak_kw": 10.9,
            },
        ]

    @pytest.mark.asyncio
    async def test_recent_keys_match_the_cross_brand_shape(self) -> None:
        """started_at/kwh/duration_min are the keys the shared sensor
        attribute already reads for Skoda — they must be present verbatim."""
        d = await _status([_history([_NEWER])])
        assert {"started_at", "kwh", "duration_min"} <= set(
            d.recent_charging_sessions[0]
        )

    @pytest.mark.asyncio
    async def test_capped_at_ten(self) -> None:
        many = [
            {**_NEWER, "id": f"s{i}",
             "startChargingDateTimeWithOffset": f"2026-09-{i + 1:02d}T10:00:00+02:00"}
            for i in range(14)
        ]
        d = await _status([_history(many)])
        assert len(d.recent_charging_sessions) == 10
        # Newest first: the 14th of the month leads.
        assert (
            d.recent_charging_sessions[0]["started_at"]
            == "2026-09-14T10:00:00+02:00"
        )

    @pytest.mark.asyncio
    async def test_unknown_charge_type_not_published(self) -> None:
        d = await _status([_history([{**_NEWER, "chargeType": "UNKNOWN"}])])
        assert d.last_charging_session_current_type is None
        assert d.last_charging_session_kwh == 55.6


class TestDegradedPayloads:
    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        d = await _status([_history([])])
        assert d.recent_charging_sessions == []
        assert d.last_charging_session_kwh is None
        assert d.last_charging_session_start is None
        assert d.last_charging_session_duration_min is None
        assert d.last_charging_session_current_type is None

    @pytest.mark.asyncio
    async def test_measurement_absent(self) -> None:
        d = await _status([{"key": "BATTERY_LEVEL", "value": {"percent": 50}}])
        assert d.recent_charging_sessions == []
        assert d.last_charging_session_kwh is None

    @pytest.mark.asyncio
    async def test_disabled_measurement_filtered(self) -> None:
        d = await _status([_history([_NEWER], enabled=False)])
        assert d.recent_charging_sessions == []
        assert d.last_charging_session_kwh is None
        assert d.last_charging_session_current_type is None

    @pytest.mark.asyncio
    async def test_malformed_items_skipped_good_one_survives(self) -> None:
        d = await _status([_history([
            "not-a-session",
            {},                                   # nothing usable at all
            {"id": "x", "startChargingDateTimeWithOffset": None,
             "netDurationS": None, "totalChargedEnergykWh": None,
             "peakChargingPowerkW": None, "startSoC": None, "endSoC": None},
            _NEWER,
        ])])
        assert len(d.recent_charging_sessions) == 1
        assert d.last_charging_session_kwh == 55.6
        assert d.last_charging_session_current_type == "DC"

    @pytest.mark.asyncio
    async def test_unparseable_start_never_wins_over_a_real_one(self) -> None:
        d = await _status([_history([
            {**_OLDER, "startChargingDateTimeWithOffset": "yesterday-ish"},
            _NEWER,
        ])])
        assert d.last_charging_session_start == "2026-09-14T11:03:00+02:00"
        assert d.recent_charging_sessions[-1]["started_at"] == "yesterday-ish"

    @pytest.mark.asyncio
    async def test_partial_session_fields_fail_soft(self) -> None:
        d = await _status([_history([
            {"startChargingDateTimeWithOffset": "2026-09-14T11:03:00+02:00",
             "chargeType": "AC", "startSoC": 130, "endSoC": "not-a-number"},
        ])])
        assert d.last_charging_session_start == "2026-09-14T11:03:00+02:00"
        assert d.last_charging_session_kwh is None
        assert d.last_charging_session_duration_min is None
        assert d.recent_charging_sessions[0]["start_soc"] is None  # 130 % ≠ real
        assert d.recent_charging_sessions[0]["end_soc"] is None

    @pytest.mark.asyncio
    async def test_value_not_a_dict(self) -> None:
        d = await _status([
            {"key": "CHARGING_SESSION_HISTORY", "value": ["unexpected"]},
        ])
        assert d.recent_charging_sessions == []

    @pytest.mark.asyncio
    async def test_no_cumulative_total_derived(self) -> None:
        """The window is 28 days, not lifetime — summing it into the
        TOTAL_INCREASING energy sensor would make its statistics walk
        backwards as old sessions age out."""
        d = await _status([_history([_OLDER, _NEWER])])
        assert d.total_charged_energy_kwh is None


class TestRawCaptureKeys:
    def test_new_raw_capture_keys_requested(self) -> None:
        for key in (
            "TRIP_STATISTICS_SHORT_TERM", "TRIP_STATISTICS_SHORT_TERM_HISTORY",
            "TRIP_STATISTICS_LONG_TERM", "TRIP_STATISTICS_LONG_TERM_HISTORY",
            "TRIP_STATISTICS_CYCLIC", "TRIP_STATISTICS_CYCLIC_HISTORY",
        ):
            assert key in _MEASUREMENTS

    def test_charging_session_history_still_requested(self) -> None:
        assert "CHARGING_SESSION_HISTORY" in _MEASUREMENTS

    def test_no_duplicate_measurement_keys(self) -> None:
        assert len(_MEASUREMENTS) == len(set(_MEASUREMENTS))

    def test_aggregate_tire_pressure_key_still_absent(self) -> None:
        """TIRE_PRESSURE_WARNING/_CRITICAL are real per-vehicle keys; the
        aggregate TIRE_PRESSURE that b20 removed must stay gone."""
        assert "TIRE_PRESSURE" not in _MEASUREMENTS
