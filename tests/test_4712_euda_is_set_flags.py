# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scout #1421 follow-up — the seven EU Data Act container ``is_set`` flags.

A VW ID.x portal export (2026-09-16 diagnostics) delivers ``acknowledge``,
``battery_level_HV``, ``enginehood``, ``hvbatterytemperature``, ``mileage``,
``outdoor_temperature`` and ``trunk`` present-flags as separate data points with
value "true". Their containers are all mapped already, so the flag adds no
reading and only says "this container was populated" — yet unconsumed it
re-reported to the Vehicle Data Scout on every poll. They are now consumed as
metadata: no entity, no inferred state, real values untouched.

The ownerless opening UUID ``c0bb1348`` stays Scout-visible (no-suppression
policy for an unidentified REAL field).
"""
from __future__ import annotations

from custom_components.vag_connect.cariad.auth._eu_data_act import (
    map_dataset_to_vehicle_data,
)
from custom_components.vag_connect.cariad.models import VehicleData

_FLAGS = (
    "acknowledge.is_set",
    "battery_level_HV.is_set",
    "enginehood.is_set",
    "hvbatterytemperature.is_set",
    "mileage.is_set",
    "outdoor_temperature.is_set",
    "trunk.is_set",
)


def _map(
    fields: dict[str, str],
    field_uuids: dict[str, set[str]] | None = None,
) -> VehicleData:
    return map_dataset_to_vehicle_data(
        dict(fields), VehicleData(vin="X"), field_uuids=field_uuids
    )


def test_seven_is_set_flags_leave_raw_unmapped_fields() -> None:
    """The exact shape the reporter's export delivered."""
    fields = {name: "true" for name in _FLAGS}
    fields["some_new_field"] = "42"  # keeps the discovery surface non-empty
    d = _map(fields)
    assert d.raw_unmapped_fields is not None
    for name in _FLAGS:
        assert name not in d.raw_unmapped_fields
    assert "some_new_field" in d.raw_unmapped_fields


def test_is_set_flags_create_no_state_and_no_reading() -> None:
    """"Populated" is not "open" — and not a value either."""
    d = _map({name: "true" for name in _FLAGS})
    assert d.trunk_open is None
    assert d.hood_open is None
    assert d.odometer_km is None
    assert d.outside_temp is None
    assert d.battery_soc is None


def test_real_values_still_win_next_to_the_flags() -> None:
    fields = {name: "true" for name in _FLAGS}
    fields.update({
        "mileage.value": "48211",
        "trunk.open": "false",
        "state_of_hood": "3",          # 3 == closed
        "outdoor_temperature": "17.5",
        "battery_level_HV.state": "VALID",
        "battery_level_HV.value": "64",
    })
    d = _map(fields)
    assert d.odometer_km == 48211
    assert d.trunk_open is False
    assert d.hood_open is False
    assert d.outside_temp == 17.5
    assert d.battery_soc == 64


def test_ownerless_open_uuid_still_surfaces_to_the_scout() -> None:
    """c0bb1348 is an unidentified REAL field — never suppressed."""
    uuid = "c0bb1348-0000-0000-0000-000000000000"
    fields = {name: "true" for name in _FLAGS}
    fields["open"] = "true"
    d = _map(fields, field_uuids={"open": {uuid}})
    assert d.raw_unmapped_fields is not None
    assert "open" in d.raw_unmapped_fields
    assert "c0bb1348" in d.raw_unmapped_fields["open"]


def test_underscore_dialect_of_the_flags_is_also_consumed() -> None:
    fields = {name.replace(".", "_"): "true" for name in _FLAGS}
    fields["some_new_field"] = "42"
    d = _map(fields)
    assert d.raw_unmapped_fields is not None
    for name in _FLAGS:
        assert name.replace(".", "_") not in d.raw_unmapped_fields


def test_absent_flags_are_inert() -> None:
    d = _map({"mileage.value": "1000"})
    assert d.odometer_km == 1000
    assert not d.raw_unmapped_fields
