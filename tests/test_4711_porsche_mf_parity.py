# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""v4.7.11 Porsche mf-capture parity — competitor-grounded fixes.

Grounded against a real Taycan/Macan mf capture (a public CJNE
ha-porscheconnect issue paste):

  - CHARGING_RATE moved the charging power to ``chargingPowerkW`` on newer
    cars (real: {"chargingPower": 0, "chargingPowerkW": 8.7}); prefer it and
    fall back to the legacy ``chargingPower`` (CJNE #397).
  - CLIMATIZER_STATE.targetTemperature is Kelvin (real: 293.15 → 20.0 °C);
    wire it to target_temperature (pyporscheconnectapi PR #98).
  - Seat heating is intentionally NOT parsed: the capture carries no per-seat
    heating levels (HEATING_STATE NOT_SUPPORTED, no HVAC_SUMMARY, and
    climateZonesEnabled is climate-zone booleans).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.vag_connect.cariad.api.porsche import PorscheClient

_VIN = "WP0ZZZ99ZTS300001"


def _client() -> PorscheClient:
    return PorscheClient.__new__(PorscheClient)


async def _status(measurements: list[dict]) -> object:
    c = _client()
    c._get = AsyncMock(return_value={"measurements": measurements})
    return await c.get_status(_VIN)


class TestChargingPower:
    @pytest.mark.asyncio
    async def test_prefers_charging_power_kw(self) -> None:
        # Newer car: chargingPower==0 but chargingPowerkW carries the real value.
        d = await _status([
            {"key": "CHARGING_RATE", "value": {"chargingPower": 0, "chargingPowerkW": 8.7}},
        ])
        assert d.charging_power_kw == 8.7

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_charging_power(self) -> None:
        # Older payload: no chargingPowerkW member → keep the legacy field.
        d = await _status([
            {"key": "CHARGING_RATE", "value": {"chargingPower": 6.45, "chargingRate": 0.4}},
        ])
        assert d.charging_power_kw == 6.45

    @pytest.mark.asyncio
    async def test_absent_charging_rate_leaves_none(self) -> None:
        d = await _status([{"key": "BATTERY_LEVEL", "value": {"percent": 50}}])
        assert d.charging_power_kw is None


class TestTargetTemperature:
    @pytest.mark.asyncio
    async def test_kelvin_to_celsius(self) -> None:
        d = await _status([
            {"key": "CLIMATIZER_STATE", "value": {"isOn": False, "targetTemperature": 293.15}},
        ])
        assert d.target_temperature == 20.0

    @pytest.mark.asyncio
    async def test_one_decimal(self) -> None:
        # 294.65 K = 21.5 °C — proves the round(…, 1).
        d = await _status([
            {"key": "CLIMATIZER_STATE", "value": {"isOn": True, "targetTemperature": 294.65}},
        ])
        assert d.target_temperature == 21.5

    @pytest.mark.asyncio
    async def test_non_numeric_target_ignored(self) -> None:
        d = await _status([
            {"key": "CLIMATIZER_STATE", "value": {"isOn": True, "targetTemperature": "warm"}},
        ])
        assert d.target_temperature is None

    @pytest.mark.asyncio
    async def test_missing_target_leaves_none(self) -> None:
        d = await _status([
            {"key": "CLIMATIZER_STATE", "value": {"isOn": True}},
        ])
        assert d.target_temperature is None


class TestSeatHeatingNotInvented:
    @pytest.mark.asyncio
    async def test_climate_zones_are_not_seat_heating(self) -> None:
        # climateZonesEnabled are climate-zone booleans, never per-seat heat
        # levels — seat_heating must stay unknown (must not be invented).
        d = await _status([
            {"key": "CLIMATIZER_STATE", "value": {
                "isOn": False,
                "targetTemperature": 293.15,
                "climateZonesEnabled": {
                    "frontLeft": True, "frontRight": True,
                    "rearLeft": True, "rearRight": True,
                },
            }},
        ])
        assert d.seat_heating is None
