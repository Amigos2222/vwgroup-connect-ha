# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1413 (Audi e-tron GT, hietaki) — a single-charge-port car emits the flap /
plug states as BARE leaves (flap_state / flap_lock_state / flap_error_state =
cluster Vehicle Access; plug_lock_state = Charging) with no charging_plug1_ /
plug2_ prefix. Only the prefixed variants were wired, so the bare names
re-reported to the Vehicle Data Scout every poll. They now map onto their own
sensors and leave the raw/Scout surface; the prefixed twins keep working.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.vag_connect.cariad.auth._eu_data_act import (
    map_dataset_to_vehicle_data,
)
from custom_components.vag_connect.cariad.models import VehicleData

_BARE = ("flap_state", "flap_lock_state", "flap_error_state", "plug_lock_state")


def _map(fields: dict[str, str]) -> VehicleData:
    return map_dataset_to_vehicle_data(dict(fields), VehicleData(vin="X"))


def test_bare_flap_leaves_are_mapped() -> None:
    d = _map({
        "flap_state": "closed",
        "flap_lock_state": "locked",
        "flap_error_state": "no_error",
        "plug_lock_state": "unlocked",
    })
    assert d.flap_state == "closed"
    assert d.flap_lock_state == "locked"
    assert d.flap_error_state == "no_error"
    assert d.plug_lock_state == "unlocked"


def test_bare_flap_leaves_leave_the_scout_surface() -> None:
    d = _map({
        "flap_state": "closed",
        "flap_lock_state": "locked",
        "flap_error_state": "no_error",
        "plug_lock_state": "unlocked",
    })
    raw = d.raw_unmapped_fields or {}
    for name in _BARE:
        assert name not in raw


def test_junk_sentinel_is_skipped_but_consumed() -> None:
    # a single-port / no-session car sends the junk sentinel; the target stays
    # None (no phantom "invalid" entity) but the field is still consumed so it
    # does not re-report to the Scout (v2.25.0 first() sentinel-consume rule).
    d = _map({
        "flap_state": "invalid",
        "plug_lock_state": "notAvailable",
    })
    assert d.flap_state is None
    assert d.plug_lock_state is None
    raw = d.raw_unmapped_fields or {}
    assert "flap_state" not in raw
    assert "plug_lock_state" not in raw


def test_prefixed_plug1_variants_still_mapped() -> None:
    # the bare-leaf wiring must not cannibalise the prefixed plug1 family.
    d = _map({
        "charging_plug1_flap_state": "open",
        "charging_plug1_flap_lock_state": "unlocked",
        "charging_plug1_lock_state": "locked",
        "flap_state": "closed",
    })
    assert d.charging_plug1_flap_state == "open"
    assert d.charging_plug1_flap_lock_state == "unlocked"
    assert d.charging_plug1_lock_state == "locked"
    # bare leaf lands on its OWN target, not folded into plug1.
    assert d.flap_state == "closed"


def test_prefixed_reading_wins_over_none_guard() -> None:
    # bare leaf present alongside a prefixed one: distinct targets, no collision.
    d = _map({"charging_plug1_flap_state": "open", "flap_state": "closed"})
    assert d.charging_plug1_flap_state == "open"
    assert d.flap_state == "closed"


_COMP = Path(__file__).resolve().parents[1] / "custom_components" / "vag_connect"
_LANGS = ("cs", "da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "sv")

_EXPECT_EN = {
    "flap_state": "Charge/Fuel Flap",
    "flap_lock_state": "Flap Lock",
    "flap_error_state": "Flap Error State",
    "plug_lock_state": "Charging Plug Lock",
}
_EXPECT_DE = {
    "flap_state": "Tank-/Ladeklappe",
    "flap_lock_state": "Klappenverriegelung",
    "flap_error_state": "Klappen-Fehlerstatus",
    "plug_lock_state": "Ladestecker-Verriegelung",
}


def _sensor_names(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["entity"]["sensor"]


def test_names_present_in_all_thirteen_string_files() -> None:
    files = [_COMP / "strings.json"] + [
        _COMP / "translations" / f"{lang}.json" for lang in _LANGS
    ]
    assert len(files) == 13
    for path in files:
        ent = _sensor_names(path)
        for key in _BARE:
            assert key in ent, f"{key} missing in {path.name}"
            name = ent[key]["name"]
            assert name and "SoC" not in name


def test_english_and_german_names_match_brief() -> None:
    en = _sensor_names(_COMP / "strings.json")
    de = _sensor_names(_COMP / "translations" / "de.json")
    for key, val in _EXPECT_EN.items():
        assert en[key]["name"] == val
    for key, val in _EXPECT_DE.items():
        assert de[key]["name"] == val
