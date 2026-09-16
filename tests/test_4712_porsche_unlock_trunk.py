# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Porsche ``vag_connect.unlock_trunk`` service (My Porsche 20.26.37).

APK grounding: ``TRUNK_UNLOCK`` exists as a command key in both captured
builds; 20.26.37 ships it as a real user action (an "Unlock tailgate" action
sheet with a security disclaimer, plus its own ``TrunkCommandStatus``
Idle/Loading/CommandSuccess state machine). The UNLOCK payload model is
``EmptyPayload{spin}`` and TrunkUnlock's command class has the same shape as
``UnlockCommand``, so the S-PIN challenge/response protocol is the grounded
assumption for it too — NOT live-verified against a real car.

These tests cover the wiring only (service → coordinator → client method,
brand gate, capability gate, S-PIN pre-flight). The wire format itself is
covered by ``test_porsche_command_protocol.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.vag_connect.coordinator import VagConnectCoordinator

VIN = "WP0ZZZ99ZTS300001"
_ROOT = Path(__file__).resolve().parents[1] / "custom_components/vag_connect"
_LANGS = ("cs", "da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "sv")


def _coord(brand: str = "porsche", *, spin: str = "1234") -> VagConnectCoordinator:
    """A coordinator stubbed down to what ``async_unlock_trunk`` touches."""
    coord = VagConnectCoordinator.__new__(VagConnectCoordinator)
    coord.entry = MagicMock()
    coord.entry.data = {"brand": brand}
    coord.vehicles = {VIN: {}}
    coord._cariad_cmd = AsyncMock()  # type: ignore[method-assign]
    coord._spin_from_entry = MagicMock(return_value=spin)  # type: ignore[method-assign]
    coord.command_capability_supported = MagicMock(  # type: ignore[method-assign]
        return_value=None
    )
    return coord


class TestCoordinatorRouting:
    @pytest.mark.asyncio
    async def test_routes_to_command_unlock_trunk_with_spin(self) -> None:
        coord = _coord()
        await coord.async_unlock_trunk(VIN)
        coord._cariad_cmd.assert_awaited_once_with(
            VIN, "command_unlock_trunk", spin="1234"
        )

    @pytest.mark.asyncio
    async def test_per_vin_spin_override_is_used(self) -> None:
        """#759 — the per-VIN S-PIN lookup must be asked for THIS vin, not the
        shared entry PIN blindly (the bug ``async_lock`` had for seat/cupra)."""
        coord = _coord()
        await coord.async_unlock_trunk(VIN)
        coord._spin_from_entry.assert_called_once_with(VIN)

    @pytest.mark.asyncio
    async def test_missing_spin_raises_before_dispatch(self) -> None:
        coord = _coord(spin="")
        with pytest.raises(ServiceValidationError) as err:
            await coord.async_unlock_trunk(VIN)
        assert err.value.translation_key == "spin_required"
        coord._cariad_cmd.assert_not_awaited()


class TestBrandGate:
    @pytest.mark.parametrize(
        "brand", ["volkswagen", "audi", "skoda", "seat", "cupra", "volkswagen_na", ""]
    )
    @pytest.mark.asyncio
    async def test_non_porsche_brand_rejected(self, brand: str) -> None:
        """Only ``PorscheClient`` implements ``command_unlock_trunk`` — every
        other brand must get the honest not-supported error instead of an
        AttributeError from deep inside the dispatch pipeline."""
        coord = _coord(brand=brand)
        with pytest.raises(ServiceValidationError) as err:
            await coord.async_unlock_trunk(VIN)
        assert err.value.translation_key == "feature_not_supported"
        coord._cariad_cmd.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_brand_match_is_case_insensitive(self) -> None:
        coord = _coord(brand="Porsche")
        await coord.async_unlock_trunk(VIN)
        coord._cariad_cmd.assert_awaited_once()


class TestCapabilityGate:
    @pytest.mark.asyncio
    async def test_explicit_false_blocks_the_command(self) -> None:
        coord = _coord()
        coord.command_capability_supported = MagicMock(  # type: ignore[method-assign]
            return_value=False
        )
        with pytest.raises(ServiceValidationError) as err:
            await coord.async_unlock_trunk(VIN)
        assert err.value.translation_key == "feature_not_supported"
        coord._cariad_cmd.assert_not_awaited()
        coord.command_capability_supported.assert_called_once_with(
            VIN, "command_unlock_trunk"
        )

    @pytest.mark.parametrize("gate", [True, None])
    @pytest.mark.asyncio
    async def test_true_and_unknown_both_pass(self, gate: bool | None) -> None:
        """Tri-state, same as every other command gate: only an explicit False
        hides/blocks. ``None`` (empty capability cache — which is exactly what
        Porsche returns today) must never block."""
        coord = _coord()
        coord.command_capability_supported = MagicMock(  # type: ignore[method-assign]
            return_value=gate
        )
        await coord.async_unlock_trunk(VIN)
        coord._cariad_cmd.assert_awaited_once()

    def test_porsche_capability_map_is_permissive_today(self) -> None:
        """Grounding note kept executable: the ``access`` cap-id is registered
        for the CARIAD brands only, and the Porsche client publishes no
        capability list at all — so the gate answers ``None`` for Porsche and
        the command is attempted. If a future live capture adds a Porsche cap
        row, this test is the reminder to re-check the gate direction."""
        from custom_components.vag_connect.cariad._capabilities import cap_id_for

        assert cap_id_for("porsche", "command_unlock_trunk") is None
        assert cap_id_for("volkswagen", "command_unlock_trunk") == "access"


class TestServiceRegistration:
    def test_service_registered_with_vin_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import custom_components.vag_connect as vag

        registered: dict[str, object] = {}
        schemas: dict[str, object] = {}

        def _register(domain, name, handler, *args, **kwargs):  # noqa: ANN001, ANN202
            registered[name] = handler
            if args:
                schemas[name] = args[0]

        hass = MagicMock()
        hass.services.async_register = _register
        vag._register_services(hass)

        assert "unlock_trunk" in registered
        assert schemas["unlock_trunk"] is vag.SERVICE_VIN_SCHEMA

    @pytest.mark.asyncio
    async def test_handler_dispatches_to_coordinator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import custom_components.vag_connect as vag

        handlers: dict[str, object] = {}
        hass = MagicMock()
        hass.services.async_register = (
            lambda domain, name, handler, *a, **k: handlers.__setitem__(name, handler)
        )
        vag._register_services(hass)

        coord = MagicMock()
        coord.is_read_only = MagicMock(return_value=False)
        coord.async_unlock_trunk = AsyncMock()
        monkeypatch.setattr(vag, "_get_coordinator", lambda _h, _vin: coord)

        call = MagicMock()
        call.data = {"vin": VIN}
        await handlers["unlock_trunk"](call)  # type: ignore[operator]
        coord.async_unlock_trunk.assert_awaited_once_with(VIN)

    @pytest.mark.asyncio
    async def test_handler_blocked_in_read_only_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same service-side read-only enforcement as ``unlock`` — a YAML
        automation must not be able to bypass it."""
        import custom_components.vag_connect as vag

        handlers: dict[str, object] = {}
        hass = MagicMock()
        hass.services.async_register = (
            lambda domain, name, handler, *a, **k: handlers.__setitem__(name, handler)
        )
        vag._register_services(hass)

        coord = MagicMock()
        coord.is_read_only = MagicMock(return_value=True)
        coord.is_structural_read_only = MagicMock(return_value=False)
        coord.async_unlock_trunk = AsyncMock()
        monkeypatch.setattr(vag, "_get_coordinator", lambda _h, _vin: coord)

        call = MagicMock()
        call.data = {"vin": VIN}
        with pytest.raises(ServiceValidationError):
            await handlers["unlock_trunk"](call)  # type: ignore[operator]
        coord.async_unlock_trunk.assert_not_awaited()

    def test_removed_on_unload(self) -> None:
        src = (_ROOT / "__init__.py").read_text(encoding="utf-8")
        # The unload path removes services by name; a new service that is not
        # listed there survives the last entry being removed and then fails
        # with "vehicle not found" forever.
        assert src.count('"unlock_trunk"') >= 2


class TestServiceDescriptions:
    def test_services_yaml_entry(self) -> None:
        import yaml

        doc = yaml.safe_load((_ROOT / "services.yaml").read_text(encoding="utf-8"))
        entry = doc["unlock_trunk"]
        assert entry["fields"]["vin"]["required"] is True
        # The S-PIN comes from the config entry, never from the call payload.
        assert "spin" not in entry["fields"]

    def test_i18n_present_in_every_language(self) -> None:
        files = [_ROOT / "strings.json"] + [
            _ROOT / "translations" / f"{lang}.json" for lang in _LANGS
        ]
        assert len(files) == 13
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            svc = data["services"]["unlock_trunk"]
            assert svc["name"].strip()
            assert svc["description"].strip()
            assert svc["fields"]["vin"]["name"].strip()
            assert svc["fields"]["vin"]["description"].strip()
            # Hassfest: no literal URLs in translated strings.
            assert "http://" not in svc["description"]
            assert "https://" not in svc["description"]

    def test_english_name_and_experimental_disclaimer(self) -> None:
        data = json.loads((_ROOT / "strings.json").read_text(encoding="utf-8"))
        svc = data["services"]["unlock_trunk"]
        assert svc["name"] == "Unlock trunk (Porsche, experimental)"
        desc = svc["description"].lower()
        # Must say it is not live-verified and that it opens the tailgate only.
        assert "not yet" in desc and "verified" in desc
        assert "tailgate" in desc
        assert "porsche only" in desc
