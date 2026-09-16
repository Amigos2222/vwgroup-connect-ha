# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""v4.7.11 — Blueprint library. Notifications are the #1 requested feature
across every competitor tracker and we shipped only two blueprints. This branch
adds seven brand-agnostic automation blueprints. These tests lock the *shape*
of every blueprint in the folder so a careless edit can't ship one that fails to
import into Home Assistant or that calls a service we don't expose:

- it parses as YAML (tolerating HA's ``!input`` tag);
- it has ``blueprint.name`` / ``blueprint.description`` and ``domain: automation``;
- every declared input carries a ``selector`` (HA rejects a selector-less input);
- every ``vag_connect.*`` service it calls is a real key in services.yaml.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_BP_DIR = _ROOT / "blueprints" / "automation" / "vag_connect"
_SERVICES_YAML = _ROOT / "custom_components" / "vag_connect" / "services.yaml"

# The seven brand-agnostic blueprints this library ships (plus the two older
# ABRP / Live-Activity ones that live in the same folder).
_EXPECTED = {
    "charge_complete_notify.yaml",
    "low_battery_notify.yaml",
    "left_open_away.yaml",
    "preheat_before_departure.yaml",
    "service_due_notify.yaml",
    "tyre_pressure_notify.yaml",
    "parked_position_changed.yaml",
}


class _BlueprintLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Home Assistant's ``!input`` blueprint tag."""


def _construct_input(loader: yaml.Loader, node: yaml.Node) -> dict:
    return {"__input__": loader.construct_scalar(node)}


_BlueprintLoader.add_constructor("!input", _construct_input)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_BlueprintLoader)


def _yaml_files() -> list[Path]:
    return sorted(_BP_DIR.glob("*.yaml"))


def _service_keys() -> set[str]:
    doc = yaml.safe_load(_SERVICES_YAML.read_text(encoding="utf-8"))
    return set(doc)


def test_expected_blueprints_present() -> None:
    """All seven new blueprints exist on disk."""
    on_disk = {p.name for p in _yaml_files()}
    missing = _EXPECTED - on_disk
    assert not missing, f"missing blueprints: {sorted(missing)}"


def test_every_blueprint_parses_and_is_automation() -> None:
    for path in _yaml_files():
        doc = _load(path)
        bp = doc.get("blueprint")
        assert isinstance(bp, dict), f"{path.name}: no blueprint block"
        assert bp.get("name"), f"{path.name}: blueprint.name missing"
        assert bp.get("description"), f"{path.name}: blueprint.description missing"
        assert bp.get("domain") == "automation", (
            f"{path.name}: domain must be 'automation'"
        )


def test_every_input_has_a_selector() -> None:
    """HA refuses to load a blueprint whose input lacks a selector."""
    for path in _yaml_files():
        inputs = _load(path)["blueprint"].get("input", {}) or {}
        for key, spec in inputs.items():
            # Inputs may be grouped into sections; a section has nested 'input'.
            if isinstance(spec, dict) and "input" in spec:
                for sub_key, sub_spec in (spec["input"] or {}).items():
                    assert isinstance(sub_spec, dict) and "selector" in sub_spec, (
                        f"{path.name}: input '{sub_key}' has no selector"
                    )
                continue
            assert isinstance(spec, dict) and "selector" in spec, (
                f"{path.name}: input '{key}' has no selector"
            )


def test_referenced_vag_connect_services_exist() -> None:
    """Any vag_connect.<service> a blueprint calls must be defined in
    services.yaml — otherwise the automation errors at runtime."""
    known = _service_keys()
    # Blueprints only ever call our own services (notify.* is user-provided).
    assert "start_climate_control" in known, "services.yaml sanity check failed"
    pattern = re.compile(r"vag_connect\.([a-z0-9_]+)")
    for path in _yaml_files():
        for svc in set(pattern.findall(path.read_text(encoding="utf-8"))):
            assert svc in known, (
                f"{path.name}: calls vag_connect.{svc} which is not in services.yaml"
            )


def test_no_literal_urls_except_the_maps_link() -> None:
    """Keep hardcoded URLs out of blueprints — the only sanctioned one is the
    standard maps link built from coordinates in parked_position_changed."""
    url = re.compile(r"https?://([\w.-]+)")
    for path in _yaml_files():
        for host in url.findall(path.read_text(encoding="utf-8")):
            allowed = host in {
                "abetterrouteplanner.com",  # ABRP blueprint description
                "www.google.com",           # maps deep-link in the notification
            }
            assert allowed, f"{path.name}: unexpected literal URL host {host}"
