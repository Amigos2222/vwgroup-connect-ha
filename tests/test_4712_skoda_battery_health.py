# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Škoda battery State-of-Health (MyŠkoda 8.16.0, vc 260821007).

``GET api/v1/vehicle-information/{vin}/battery-health`` → BatteryHealthStatusDto
{status: HEALTHY|NEEDS_MAINTENANCE|UNAVAILABLE, remainingCapacity: int|null,
carCapturedTimestamp}. remainingCapacity fills the existing ``battery_soh_pct``
(rounded int, like the VW/Audi BFF read); status rides along as
``battery_health_status`` for the sensor attribute. The read is capability-gated
on BATTERY_HEALTH_STATE but permissive when the list is unknown, and a 403/404
parks the VIN for the client's lifetime.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from custom_components.vag_connect.cariad._capabilities import cap_id_for
from custom_components.vag_connect.cariad.api.skoda import (
    _BASE,
    APIError,
    SkodaClient,
)

VIN = "TMBJJ7NX1M0000009"
_HEALTH_PATH = f"/api/v1/vehicle-information/{VIN}/battery-health"


class _Backend:
    """Minimal ``_get`` stand-in: records URLs, answers per path fragment."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url: str, **_kw: Any) -> Any:
        self.calls.append(url)
        for frag, result in self.routes.items():
            if frag in url:
                if isinstance(result, Exception):
                    raise result
                return result
        return {}

    @property
    def health_calls(self) -> int:
        return sum(1 for u in self.calls if _HEALTH_PATH in u)


def _client(routes: dict[str, Any]) -> tuple[SkodaClient, _Backend]:
    c = SkodaClient(MagicMock(), "u@t.de", "pw")
    backend = _Backend(routes)
    c._get = backend.get  # type: ignore[method-assign]
    c.get_charging_statistics = _no_stats  # type: ignore[method-assign]
    return c, backend


async def _no_stats(_vin: str) -> dict[str, Any]:
    return {}


def _status(routes: dict[str, Any]):
    client, _ = _client(routes)
    return asyncio.run(client.get_status(VIN))


# ── parse ────────────────────────────────────────────────────────────────────


def test_healthy_fills_soh_and_status() -> None:
    d = _status({_HEALTH_PATH: {
        "status": "HEALTHY",
        "remainingCapacity": 92,
        "carCapturedTimestamp": "2026-09-01T10:00:00Z",
    }})
    assert d.battery_soh_pct == 92
    assert d.battery_health_status == "HEALTHY"


def test_needs_maintenance_status_passes_through_verbatim() -> None:
    d = _status({_HEALTH_PATH: {"status": "NEEDS_MAINTENANCE",
                                "remainingCapacity": 64}})
    assert d.battery_soh_pct == 64
    assert d.battery_health_status == "NEEDS_MAINTENANCE"


def test_unavailable_status_without_capacity() -> None:
    d = _status({_HEALTH_PATH: {"status": "UNAVAILABLE", "remainingCapacity": None}})
    assert d.battery_health_status == "UNAVAILABLE"
    assert d.battery_soh_pct is None  # nothing to publish → sensor stays unknown


def test_out_of_range_capacity_is_dropped() -> None:
    # A sentinel must not become a nonsense percentage.
    d = _status({_HEALTH_PATH: {"status": "HEALTHY", "remainingCapacity": 4294967295}})
    assert d.battery_soh_pct is None
    assert d.battery_health_status == "HEALTHY"


def test_float_capacity_is_rounded_to_int() -> None:
    d = _status({_HEALTH_PATH: {"status": "HEALTHY", "remainingCapacity": 88.6}})
    assert d.battery_soh_pct == 89


def test_bool_capacity_is_not_read_as_one_percent() -> None:
    # The DTO says int|null; a bool is a schema surprise, not a 1 % battery.
    d = _status({_HEALTH_PATH: {"status": "HEALTHY", "remainingCapacity": True}})
    assert d.battery_soh_pct is None


def test_blank_status_leaves_field_none() -> None:
    d = _status({_HEALTH_PATH: {"status": "  ", "remainingCapacity": 90}})
    assert d.battery_health_status is None
    assert d.battery_soh_pct == 90


# ── soft-fail ────────────────────────────────────────────────────────────────


def test_404_soft_fails_and_never_sinks_the_poll() -> None:
    err = APIError(404, f"{_BASE}{_HEALTH_PATH}", "not found")
    d = _status({_HEALTH_PATH: err, "/driving-range": {"carType": "electric"}})
    assert d.battery_soh_pct is None
    assert d.battery_health_status is None
    assert d.vin == VIN  # the rest of the status read still produced a car


def test_404_is_remembered_for_the_client_lifetime() -> None:
    client, backend = _client({
        _HEALTH_PATH: APIError(404, f"{_BASE}{_HEALTH_PATH}", "not found"),
    })
    asyncio.run(client.get_status(VIN))
    asyncio.run(client.get_status(VIN))
    assert backend.health_calls == 1  # second poll does not re-ask


def test_403_is_remembered_too() -> None:
    client, backend = _client({
        _HEALTH_PATH: APIError(403, f"{_BASE}{_HEALTH_PATH}", "forbidden"),
    })
    assert asyncio.run(client.get_battery_health(VIN)) is None
    assert asyncio.run(client.get_battery_health(VIN)) is None
    assert backend.health_calls == 1


def test_non_json_body_returns_none() -> None:
    client, _ = _client({_HEALTH_PATH: "<html>maintenance</html>"})
    assert asyncio.run(client.get_battery_health(VIN)) is None


def test_transient_error_is_not_remembered() -> None:
    # A 500 says nothing about capability — keep asking on the next poll.
    client, backend = _client({
        _HEALTH_PATH: APIError(500, f"{_BASE}{_HEALTH_PATH}", "boom"),
    })
    assert asyncio.run(client.get_battery_health(VIN)) is None
    assert asyncio.run(client.get_battery_health(VIN)) is None
    assert backend.health_calls == 2


# ── capability gate ──────────────────────────────────────────────────────────


def _garage(cap_ids: list[str]) -> dict[str, Any]:
    return {"capabilities": {"capabilities": [
        {"id": cid, "statuses": []} for cid in cap_ids
    ]}}


def test_known_capability_list_without_battery_health_skips_the_call() -> None:
    client, backend = _client({
        f"/api/v2/garage/vehicles/{VIN}": _garage(["CHARGING", "AIR_CONDITIONING"]),
    })
    asyncio.run(client.get_capabilities(VIN))
    assert asyncio.run(client.get_battery_health(VIN)) is None
    assert backend.health_calls == 0


def test_capability_present_allows_the_call() -> None:
    client, backend = _client({
        f"/api/v2/garage/vehicles/{VIN}": _garage(["CHARGING", "BATTERY_HEALTH_STATE"]),
        _HEALTH_PATH: {"status": "HEALTHY", "remainingCapacity": 97},
    })
    asyncio.run(client.get_capabilities(VIN))
    assert asyncio.run(client.get_battery_health(VIN)) == {
        "status": "HEALTHY", "remainingCapacity": 97,
    }
    assert backend.health_calls == 1


def test_unknown_capability_list_still_tries_once() -> None:
    # Permissive by design: never hide a real reading because we have no list.
    client, backend = _client({_HEALTH_PATH: {"status": "HEALTHY",
                                              "remainingCapacity": 95}})
    assert asyncio.run(client.get_battery_health(VIN)) is not None
    assert backend.health_calls == 1


def test_capability_id_is_mapped_for_the_gate() -> None:
    assert cap_id_for("skoda", "command_battery_health") == "BATTERY_HEALTH_STATE"
    assert cap_id_for("skoda", "command_public_api_keys") == "PUBLIC_API_KEY_MANAGEMENT"


# ── sensor attribute ─────────────────────────────────────────────────────────


def _soh_sensor(vehicle: dict[str, Any]) -> Any:
    from custom_components.vag_connect.sensor import VagConnectSensor

    sensor = VagConnectSensor.__new__(VagConnectSensor)
    sensor.entity_description = MagicMock()
    sensor.entity_description.key = "battery_soh_pct"
    sensor._vin = VIN
    sensor.coordinator = SimpleNamespace(data={VIN: vehicle})
    return sensor


def test_status_rides_as_attribute_of_the_soh_sensor() -> None:
    sensor = _soh_sensor({"battery_soh_pct": 92, "battery_health_status": "HEALTHY"})
    assert sensor._key_platform_attributes() == {"health_status": "HEALTHY"}


def test_no_attribute_when_status_absent() -> None:
    sensor = _soh_sensor({"battery_soh_pct": 92})
    assert sensor._key_platform_attributes() is None
