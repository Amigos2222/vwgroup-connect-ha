# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#465 (@anju1337) — CUPRA/SEAT device-attestation wall runtime revive.

anju1337's CUPRA Born 2025 + Tavascan 2024 (DE) logged in fine, the garage
call answered 200, and the coordinator reported "Manual refresh OK" — yet every
telemetry field stayed None. VW's device-attestation wall (#464/#779) 403'd
EVERY per-VIN OLA endpoint while the garage still answered, so the login-time
portal fallback never armed (it only fires on a garage 403) and get_status kept
returning an all-None VehicleData WITHOUT ``no_data`` and WITHOUT raising —
nothing revived, reauth was a no-op onto the same dead OLA state.

get_status now detects the confirmed wall (every SUBSTANTIVE per-VIN endpoint
403'd AND the exhausted-fallback 403 counter is at/over the repair threshold)
and mirrors get_vehicles' fallback: arm the read-only EU Data Act portal ONCE
and serve status from there. If the portal login itself fails, it flags the poll
``no_data`` so the coordinator keeps last-known-good visible and its revive /
runtime-kickoff machinery engages instead of clobbering good telemetry.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.vag_connect.cariad.exceptions import APIError
from custom_components.vag_connect.cariad.models import VehicleData

_BASE = "https://ola.prod.code.seat.cloud.vwgroup.com"
_VIN = "TESTVIN00WALL01"


def _seatcupra(brand="cupra"):
    from custom_components.vag_connect.cariad.api.seat_cupra import SeatCupraClient

    client = SeatCupraClient(MagicMock(), brand, "u@t.de", "pw")
    client._user_id = "uid-123"
    # capabilities-count is a tail diagnostic that would otherwise hit the
    # (mocked-to-403) OLA host; keep it quiet so the parse tail is clean.
    client._get_capabilities_count = AsyncMock(return_value=None)
    # charging-stats host is a separate vhost (not part of the wall verdict).
    client._get_from_charging_host = AsyncMock(return_value={})
    return client


def _portal(vin=_VIN):
    p = MagicMock()
    p.get_vehicle_data = AsyncMock(return_value=VehicleData(vin=vin, battery_soc=57))
    p.list_vehicle_vins = AsyncMock(return_value=[vin])
    p.login = AsyncMock()
    return p


def _wall_403(url, **kwargs):
    """Every OLA per-VIN endpoint answers with the attestation 403."""
    raise APIError(403, url)


class TestOlaWallArmsPortal:
    def test_all_403_above_threshold_arms_portal_and_returns_portal_data(self):
        # garage answered 200 earlier (VINs known); every per-VIN endpoint 403s
        # and the exhausted-fallback counter is already at/over threshold.
        client = _seatcupra("cupra")
        client._get = AsyncMock(side_effect=_wall_403)
        client._ola_consecutive_403 = 5  # == _OLA_REPAIR_THRESHOLD
        portal = _portal()

        async def _arm():
            client._eu_portal = portal

        client._arm_eu_portal = AsyncMock(side_effect=_arm)

        data = asyncio.run(client.get_status(_VIN))

        client._arm_eu_portal.assert_awaited_once()
        portal.get_vehicle_data.assert_awaited_once_with(_VIN)
        # the returned snapshot is the portal's, not the all-None OLA one.
        assert data.battery_soc == 57
        assert data.no_data is False
        assert client._ola_portal_fallback_tried is True

    def test_arms_at_most_once_per_lifetime(self):
        # after a successful arm, the top-of-method portal branch takes over,
        # so the wall block never re-arms (latch stays set, no second WARNING).
        client = _seatcupra("seat")
        client._get = AsyncMock(side_effect=_wall_403)
        client._ola_consecutive_403 = 9
        portal = _portal(vin="SEATWALLVIN0002")

        async def _arm():
            client._eu_portal = portal

        client._arm_eu_portal = AsyncMock(side_effect=_arm)

        asyncio.run(client.get_status("SEATWALLVIN0002"))
        asyncio.run(client.get_status("SEATWALLVIN0002"))

        # armed once by the wall block; the 2nd poll routed via the portal branch.
        client._arm_eu_portal.assert_awaited_once()
        assert portal.get_vehicle_data.await_count == 2


class TestOlaWallGuards:
    def test_below_threshold_does_not_arm(self):
        # every substantive endpoint 403s, but a low counter means this could be
        # a transient backend flutter — do NOT flip the car to the portal yet.
        client = _seatcupra("cupra")
        client._get = AsyncMock(side_effect=_wall_403)
        client._ola_consecutive_403 = 4  # < _OLA_REPAIR_THRESHOLD (5)
        client._arm_eu_portal = AsyncMock()

        data = asyncio.run(client.get_status(_VIN))

        client._arm_eu_portal.assert_not_awaited()
        assert client._eu_portal is None
        assert client._ola_portal_fallback_tried is False
        # native path ran to completion: an all-None snapshot (no portal data).
        assert data.battery_soc is None

    def test_transient_mixed_failures_do_not_arm(self):
        # a single transient 403 among otherwise-healthy reads must NOT arm:
        # at least one substantive endpoint returns a dict, so wall != True.
        client = _seatcupra("cupra")

        def _mixed(url, **kwargs):
            # mycar + parkingposition succeed; everything else 403s.
            if url.endswith("/mycar") or url.endswith("/parkingposition"):
                return {}
            raise APIError(403, url)

        client._get = AsyncMock(side_effect=_mixed)
        client._ola_consecutive_403 = 20  # high, but the wall check gates it out
        client._arm_eu_portal = AsyncMock()

        asyncio.run(client.get_status(_VIN))

        client._arm_eu_portal.assert_not_awaited()
        assert client._eu_portal is None
        assert client._ola_portal_fallback_tried is False

    def test_non_403_wall_does_not_arm(self):
        # a 500 outage across every endpoint is not the attestation wall.
        client = _seatcupra("cupra")
        client._get = AsyncMock(side_effect=lambda url, **kw: (_ for _ in ()).throw(APIError(500, url)))
        client._ola_consecutive_403 = 9
        client._arm_eu_portal = AsyncMock()

        asyncio.run(client.get_status(_VIN))

        client._arm_eu_portal.assert_not_awaited()
        assert client._eu_portal is None

    def test_arming_failure_sets_no_data(self):
        # portal login itself fails → surface a no_data poll (coordinator keeps
        # last-known-good + engages revive) instead of clobbering with all-None.
        client = _seatcupra("cupra")
        client._get = AsyncMock(side_effect=_wall_403)
        client._ola_consecutive_403 = 7
        client._arm_eu_portal = AsyncMock(side_effect=RuntimeError("portal login failed"))

        data = asyncio.run(client.get_status(_VIN))

        client._arm_eu_portal.assert_awaited_once()
        assert data.no_data is True
        assert client._eu_portal is None
        # latched so a failed arm never loops/re-logs on every subsequent poll.
        assert client._ola_portal_fallback_tried is True

    def test_failed_arm_does_not_retry_next_poll(self):
        client = _seatcupra("cupra")
        client._get = AsyncMock(side_effect=_wall_403)
        client._ola_consecutive_403 = 7
        client._arm_eu_portal = AsyncMock(side_effect=RuntimeError("portal login failed"))

        asyncio.run(client.get_status(_VIN))
        asyncio.run(client.get_status(_VIN))

        # arm attempted only once across two walled polls (latch honoured).
        client._arm_eu_portal.assert_awaited_once()


# ── point (2): manual-refresh parity with the poll loop ──────────────────────

class TestManualRefreshRevivesLikePollLoop:
    """A no_data primary with cached data must trigger the runtime kickoff +
    supplementary revive on the MANUAL/post-command refresh path too, not only
    on the timed poll (the poll loop already did both)."""

    def _coord(self):
        import threading

        from custom_components.vag_connect.coordinator import VagConnectCoordinator

        coord = VagConnectCoordinator.__new__(VagConnectCoordinator)
        coord.hass = MagicMock()
        coord.entry = MagicMock()
        coord._vehicles_lock = threading.Lock()
        coord._was_available = True
        coord._started = True
        coord._cariad_client = MagicMock()
        coord._push_official_mode = MagicMock()
        coord._wire_dataset_archive = MagicMock()
        coord._refresh_mbb_command_capabilities = AsyncMock()
        coord._persist_website_cookies = MagicMock()
        coord._persist_supplementary_cookies = MagicMock()
        coord._persist_companion_rate_limit = MagicMock()
        coord._maybe_runtime_data_act_kickoff = AsyncMock()
        coord._revive_from_supplementary = AsyncMock(return_value=None)
        coord.vehicles = {"VIN1": {"battery_soc": 80, "odometer_km": 12345}}
        return coord

    def test_no_data_refresh_triggers_kickoff_and_revive(self):
        coord = self._coord()
        empty = VehicleData(vin="VIN1", no_data=True)
        coord._cariad_client.get_status = AsyncMock(return_value=empty)

        result = asyncio.run(coord._async_update_data())

        # parity with the poll loop: both engaged.
        coord._maybe_runtime_data_act_kickoff.assert_awaited_once()
        coord._revive_from_supplementary.assert_awaited_once()
        # revive returned None → last-known-good still survives (no clobber).
        assert result["VIN1"]["battery_soc"] == 80
        assert result["VIN1"]["odometer_km"] == 12345

    def test_revive_success_replaces_stale_and_skips_double_merge(self):
        coord = self._coord()
        empty = VehicleData(vin="VIN1", no_data=True)
        coord._cariad_client.get_status = AsyncMock(return_value=empty)
        revived = VehicleData(vin="VIN1", battery_soc=61, no_data=False)
        revived.source_channel = "eu_data_act"
        coord._revive_from_supplementary = AsyncMock(return_value=revived)
        # if the revived snapshot were re-merged, this would run; it must not.
        coord._merge_supplementary = AsyncMock(
            side_effect=AssertionError("revived snapshot must not be re-merged")
        )
        coord._enrich = AsyncMock(side_effect=lambda d: d)

        result = asyncio.run(coord._async_update_data())

        coord._revive_from_supplementary.assert_awaited_once()
        coord._merge_supplementary.assert_not_awaited()
        assert result["VIN1"]["battery_soc"] == 61
