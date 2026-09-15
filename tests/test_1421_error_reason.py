# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""v4.7.11 (#1421 @skornehl) — EU-Data-Act "ErrorReason" leaf.

The Scout filed a single-UUID leaf ``eu_data_act.ErrorReason`` (dict UUID
b477dd84, type number, cluster "All Data") with a sample value of "13". The dict
documents no enum, so we surface the RAW code and drop the "0"/"0.0"/"#0"
no-error sentinels exactly the way ``charging_error_code`` does. Mapping the leaf
also consumes it, so it stops re-surfacing on the Scout every poll.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.vag_connect.cariad.auth._eu_data_act import (
    map_dataset_to_vehicle_data,
)
from custom_components.vag_connect.cariad.models import VehicleData

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "vag_connect"
_LANGS = ("cs", "da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "sv")


def test_1421_real_code_mapped() -> None:
    d = VehicleData(vin="WVWZZZ1KZAW000000")
    map_dataset_to_vehicle_data({"ErrorReason": "13"}, d)
    assert d.error_reason == "13"


@pytest.mark.parametrize("sentinel", ["0", "#0", "0.0"])
def test_1421_no_error_sentinels_drop_to_none(sentinel: str) -> None:
    d = VehicleData(vin="X")
    map_dataset_to_vehicle_data({"ErrorReason": sentinel}, d)
    assert d.error_reason is None


def test_1421_leaf_is_consumed_not_raw_unmapped() -> None:
    # A mapped real value must leave raw_unmapped_fields (Scout stops re-flagging).
    d = VehicleData(vin="X")
    map_dataset_to_vehicle_data({"ErrorReason": "13"}, d)
    assert "ErrorReason" not in (d.raw_unmapped_fields or {})


def test_1421_sentinel_leaf_also_consumed() -> None:
    # Even the no-error sentinel is consumed (mapped field → not a discovery).
    d = VehicleData(vin="X")
    map_dataset_to_vehicle_data({"ErrorReason": "0"}, d)
    assert "ErrorReason" not in (d.raw_unmapped_fields or {})


def test_1421_name_present_in_all_13_string_files() -> None:
    strings = json.loads((_ROOT / "strings.json").read_text(encoding="utf-8"))
    assert strings["entity"]["sensor"]["error_reason"]["name"]
    for lang in _LANGS:
        data = json.loads(
            (_ROOT / "translations" / f"{lang}.json").read_text(encoding="utf-8")
        )
        assert data["entity"]["sensor"]["error_reason"]["name"], lang
