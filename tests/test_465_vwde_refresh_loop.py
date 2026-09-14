# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#465 (toglo, VW Tayron PHEV) — kill the self-sustaining vw.de refresh loop.

The coordinator persists rotated vw.de/MBB cookies+tokens back into the entry on
nearly every poll. ``_async_update_listener`` treated each of those writes as a
user settings change and called ``async_request_refresh``, which triggered the
next persist -> a self-sustaining ~10-16 s loop that hammered identity.vwgroup.io
and flip-flopped the per-source (Datenquelle) sensors. Four surgical fixes:

1. every coordinator-owned entry write goes through ``_self_update_entry``, which
   records a fingerprint the listener uses to skip its own writes (a genuine
   options save still soft-applies/reloads);
2. one silent-resume GET per poll cycle — a proactive roll that already found the
   SSO dead makes the reactive refresh re-use the verdict instead of a 2nd GET;
3. the manual/command refresh path recomputes ``channel_status`` like the poll
   loop, so the source sensors don't flip between manual and scheduled polls;
4. when the supplementary contributes nothing, provenance falls back to the
   primary channel name instead of going None.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.vag_connect.const import (
    CONF_BRAND,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)


# ── (1) listener: skip our own writes, honour genuine options saves ──────────

def _run_listener(entry, coord):
    from custom_components.vag_connect import _async_update_listener

    entry.runtime_data = coord
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    asyncio.run(_async_update_listener(hass, entry))
    return hass


def _coord_stub():
    # Minimal listener-facing coordinator: only the attrs the listener touches.
    return SimpleNamespace(
        async_request_refresh=AsyncMock(),
        _self_entry_write_fp=None,
    )


def test_listener_skips_refresh_on_a_coordinator_self_write() -> None:
    """A persistence write whose fingerprint the coordinator recorded is NOT a
    settings change: no reload, no soft-update write, no refresh."""
    from custom_components.vag_connect.coordinator import entry_settings_fingerprint

    data = {
        CONF_BRAND: "volkswagen", CONF_USERNAME: "u", CONF_PASSWORD: "p",
        "supplementary_authproxy_cookies": [{"name": "sso", "value": "rotated"}],
    }
    entry = SimpleNamespace(entry_id="e1", data=data, options={})
    coord = _coord_stub()
    # The coordinator stamped this exact resulting state before it wrote.
    coord._self_entry_write_fp = entry_settings_fingerprint(data, {})

    hass = _run_listener(entry, coord)

    coord.async_request_refresh.assert_not_awaited()
    hass.config_entries.async_reload.assert_not_called()
    hass.config_entries.async_update_entry.assert_not_called()


def test_listener_refreshes_on_a_genuine_options_change() -> None:
    """A real options save (scan interval) has a different fingerprint, so the
    soft-update write AND the immediate refresh still run — v4.7.9 behaviour."""
    from custom_components.vag_connect.coordinator import entry_settings_fingerprint

    data = {CONF_BRAND: "volkswagen", CONF_USERNAME: "u", CONF_PASSWORD: "p"}
    entry = SimpleNamespace(
        entry_id="e1", data=data, options={CONF_SCAN_INTERVAL: 30},
    )
    coord = _coord_stub()
    # Fingerprint of an OLDER state (a prior self-write) — must NOT match the live
    # (data, {scan_interval: 30}) entry, so the listener falls through.
    coord._self_entry_write_fp = entry_settings_fingerprint(data, {})

    hass = _run_listener(entry, coord)

    coord.async_request_refresh.assert_awaited_once()
    hass.config_entries.async_reload.assert_not_called()  # not a reload key
    hass.config_entries.async_update_entry.assert_called_once()  # soft-update ran


def test_listener_reloads_on_a_credential_change() -> None:
    """A password change is a reload key and is never mistaken for a self-write."""
    from custom_components.vag_connect.coordinator import entry_settings_fingerprint

    data = {CONF_BRAND: "volkswagen", CONF_USERNAME: "u", CONF_PASSWORD: "old"}
    entry = SimpleNamespace(
        entry_id="e1", data=data, options={CONF_PASSWORD: "new"},
    )
    coord = _coord_stub()
    coord._self_entry_write_fp = entry_settings_fingerprint(data, {})

    hass = _run_listener(entry, coord)

    hass.config_entries.async_reload.assert_awaited_once()
    coord.async_request_refresh.assert_not_awaited()


def test_self_update_entry_records_the_resulting_fingerprint() -> None:
    """``_self_update_entry`` stamps the fingerprint of the state it is about to
    write, so a listener firing afterwards recognises it. Race-free: HA runs the
    listener as a task after the (synchronous) write, observing the final state."""
    from custom_components.vag_connect.coordinator import (
        VagConnectCoordinator,
        entry_settings_fingerprint,
    )

    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c.entry = SimpleNamespace(data={"a": 1}, options={"keep": True})
    c.hass = MagicMock()
    new_data = {"a": 1, "supplementary_authproxy_cookies": [{"n": "v"}]}

    c._self_update_entry(data=new_data)

    # options untouched -> fingerprint is over (new_data, existing options)
    assert c._self_entry_write_fp == entry_settings_fingerprint(
        new_data, {"keep": True}
    )
    c.hass.config_entries.async_update_entry.assert_called_once()
    assert c.hass.config_entries.async_update_entry.call_args.kwargs == {
        "data": new_data
    }


def test_fingerprint_is_key_order_independent_and_hashed() -> None:
    from custom_components.vag_connect.coordinator import entry_settings_fingerprint

    a = entry_settings_fingerprint({"x": 1, "y": 2}, {"o": 1})
    b = entry_settings_fingerprint({"y": 2, "x": 1}, {"o": 1})
    assert a == b
    assert a != entry_settings_fingerprint({"x": 1, "y": 3}, {"o": 1})
    # A SHA-256 hex digest — no raw cookie/token material lingers in the value.
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)


# ── (2) one silent-resume GET per cycle ──────────────────────────────────────

def _connector():
    from custom_components.vag_connect.cariad.auth._website_authproxy import (
        WebsiteAuthProxyConnector,
    )

    c = WebsiteAuthProxyConnector.__new__(WebsiteAuthProxyConnector)
    c.logged_in = True
    c._last_roll = float("-inf")
    c._resume_dead_this_cycle = False
    c._session = MagicMock()
    c._headers = MagicMock(return_value={})
    return c


def test_reactive_refresh_short_circuits_after_a_dead_proactive_roll() -> None:
    """When the proactive roll already latched the SSO dead this cycle, the
    reactive refresh() re-raises the verdict WITHOUT a second network GET."""
    from custom_components.vag_connect.cariad.exceptions import AuthenticationError

    c = _connector()
    c._resume_dead_this_cycle = True
    with pytest.raises(AuthenticationError, match="SSO session expired"):
        asyncio.run(c.refresh())
    c._session.get.assert_not_called()


def test_reactive_refresh_hits_the_network_when_not_latched() -> None:
    """With no dead-SSO latch, refresh() proceeds to the silent-resume GET (the
    safety net stays intact for cycles where the proactive roll was debounced)."""
    c = _connector()
    c._resume_dead_this_cycle = False
    c._session.get = MagicMock(side_effect=RuntimeError("reached network"))
    with pytest.raises(RuntimeError, match="reached network"):
        asyncio.run(c.refresh())
    c._session.get.assert_called_once()


def test_proactive_roll_latches_dead_sso_and_opens_a_fresh_cycle() -> None:
    """maybe_roll clears the latch BEFORE rolling (new cycle) and re-sets it only
    if THIS roll finds a dead SSO."""
    from custom_components.vag_connect.cariad.exceptions import AuthenticationError

    c = _connector()
    c._resume_dead_this_cycle = True  # stale verdict from a previous cycle
    seen: dict[str, object] = {}

    async def _dead():
        seen["flag_at_roll"] = c._resume_dead_this_cycle
        raise AuthenticationError("SSO session expired — full re-login required")

    c.refresh = _dead  # type: ignore[method-assign]
    asyncio.run(c.maybe_roll(force=True))

    assert seen["flag_at_roll"] is False   # reset before the GET
    assert c._resume_dead_this_cycle is True  # re-latched after the dead verdict


def test_proactive_roll_clears_latch_on_a_live_resume() -> None:
    c = _connector()
    c._resume_dead_this_cycle = True

    async def _ok():
        return None

    c.refresh = _ok  # type: ignore[method-assign]
    asyncio.run(c.maybe_roll(force=True))
    assert c._resume_dead_this_cycle is False


def test_debounced_roll_keeps_the_dead_latch_sticky() -> None:
    """A debounced maybe_roll neither rolls nor clears the latch, so a prior dead
    verdict keeps the reactive path suppressed until the next real roll."""
    import time

    c = _connector()
    c._resume_dead_this_cycle = True
    c._last_roll = time.monotonic()  # just rolled -> debounced
    calls = {"n": 0}

    async def _count():
        calls["n"] += 1

    c.refresh = _count  # type: ignore[method-assign]
    asyncio.run(c.maybe_roll())  # not forced
    assert calls["n"] == 0
    assert c._resume_dead_this_cycle is True


# ── (3) manual refresh recomputes channel_status ─────────────────────────────

def test_manual_refresh_recomputes_channel_status(monkeypatch) -> None:
    from custom_components.vag_connect import coordinator as coord_mod
    from custom_components.vag_connect.cariad import vehicle_cache as vc
    from custom_components.vag_connect.cariad.models import VehicleData

    c = coord_mod.VagConnectCoordinator.__new__(coord_mod.VagConnectCoordinator)
    c._started = True
    vin = "WVWZZZ1JZXW000001"
    vd = VehicleData(vin=vin, battery_soc=42)

    client = MagicMock()
    client.get_status = AsyncMock(return_value=vd)
    c._cariad_client = client
    c.vehicles = {vin: {}}
    c._vehicles_lock = threading.Lock()
    c._push_official_mode = MagicMock()
    c._wire_dataset_archive = MagicMock()
    c._refresh_mbb_command_capabilities = AsyncMock()

    async def _merge(_vin, result):
        result.source_channel = "eu_data_act"
        return result

    c._merge_supplementary = _merge

    async def _enrich(data):
        return data

    c._enrich = _enrich
    c._apply_optimistic_hold = lambda _vin, data: data
    c._persist_website_cookies = MagicMock()
    c._persist_supplementary_cookies = MagicMock()
    c._persist_companion_rate_limit = MagicMock()
    c._compute_channel_status = MagicMock(return_value={"eu_data_act": "active"})
    monkeypatch.setattr(vc, "reconcile", lambda old, new: (new, []))

    out = asyncio.run(c._async_update_data())

    c._compute_channel_status.assert_called_once()
    assert c._compute_channel_status.call_args.args[0] == vin
    assert out[vin]["channel_status"] == {"eu_data_act": "active"}


def test_manual_refresh_channel_status_is_fail_soft(monkeypatch) -> None:
    """A compute hiccup must never sink the refresh; the snapshot still lands."""
    from custom_components.vag_connect import coordinator as coord_mod
    from custom_components.vag_connect.cariad import vehicle_cache as vc
    from custom_components.vag_connect.cariad.models import VehicleData

    c = coord_mod.VagConnectCoordinator.__new__(coord_mod.VagConnectCoordinator)
    c._started = True
    vin = "WVWZZZ1JZXW000002"
    vd = VehicleData(vin=vin, battery_soc=10)

    client = MagicMock()
    client.get_status = AsyncMock(return_value=vd)
    c._cariad_client = client
    c.vehicles = {vin: {}}
    c._vehicles_lock = threading.Lock()
    c._push_official_mode = MagicMock()
    c._wire_dataset_archive = MagicMock()
    c._refresh_mbb_command_capabilities = AsyncMock()

    async def _merge(_vin, result):
        return result

    c._merge_supplementary = _merge

    async def _enrich(data):
        return data

    c._enrich = _enrich
    c._apply_optimistic_hold = lambda _vin, data: data
    c._persist_website_cookies = MagicMock()
    c._persist_supplementary_cookies = MagicMock()
    c._persist_companion_rate_limit = MagicMock()
    c._compute_channel_status = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(vc, "reconcile", lambda old, new: (new, []))

    out = asyncio.run(c._async_update_data())  # must not raise
    assert vin in out


# ── (4) provenance falls back to the primary channel ─────────────────────────

def _merge_coord(suppliers):
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c._cariad_client = MagicMock()
    c._cariad_client.supplementary_readers = MagicMock(return_value=suppliers)
    c._primary_channel_name = MagicMock(return_value="website_authproxy")
    c._read_priority_channel = MagicMock(return_value=None)
    c._persist_supplementary_cookies = MagicMock()
    return c


def test_provenance_falls_back_to_primary_when_supplier_contributes_nothing() -> None:
    from custom_components.vag_connect.cariad.models import VehicleData

    async def _none_reader():
        return None

    c = _merge_coord([("eu_data_act", _none_reader())])
    primary = VehicleData(vin="WVWZZZ1JZXW000003", battery_soc=55)
    primary.source_channel = None

    out = asyncio.run(c._merge_supplementary("WVWZZZ1JZXW000003", primary))

    # A supplier that returned nothing must not blank the origin — the sensor
    # would otherwise flip to unknown between polls.
    assert out.source_channel == "website_authproxy"


def test_provenance_is_kept_when_the_supplier_does_contribute() -> None:
    from custom_components.vag_connect.cariad.models import VehicleData

    async def _reader():
        return VehicleData(vin="WVWZZZ1JZXW000004", odometer_km=12345)

    c = _merge_coord([("eu_data_act", _reader())])
    primary = VehicleData(vin="WVWZZZ1JZXW000004", battery_soc=55)

    out = asyncio.run(c._merge_supplementary("WVWZZZ1JZXW000004", primary))

    # Both channels fed a value -> the guard does NOT override the real merge.
    assert out.source_channel is not None
    assert "eu_data_act" in out.source_channel
    assert "website_authproxy" in out.source_channel
