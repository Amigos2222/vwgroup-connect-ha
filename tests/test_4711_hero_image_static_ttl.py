# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""v4.7.11 (parity ADOPT ×2, #1229) — hero render selection + static-data TTL.

Two adopted parity behaviours share this file because they serve the same goal
(the vw.de-read VW-EU car) at the two ends of the render pipeline:

  1. ``VehicleImageFetcher.primary_url`` — a tolerant, case-insensitive
     substring preference chain that picks the best SINGLE render for
     ``entity_picture`` across BOTH backends: the vgql (Audi/VW connected)
     media-id keys AND the vw.de ``view_direction_angle`` keys. ``best_url``
     delegates to it, so every entity_picture consumer inherits the choice with
     zero new entities. Mirrors the hero-render selection of the Škoda/Audi
     community integrations.

  2. Per-VIN in-memory TTL caches for the STATIC vw.de reads — master data
     (24 h) and the exterior render URL list (6 h) — which used to run on EVERY
     poll in the get_vehicle_data tail. A hit within TTL skips the GET; a miss
     fetches + stores; a soft-failed fetch keeps the prior entry (fail-soft).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from custom_components.vag_connect.cariad.api.graphql import (
    _PREFERRED_ORDER,
    VehicleImageFetcher,
)
from custom_components.vag_connect.cariad.auth import _website_authproxy
from custom_components.vag_connect.cariad.auth._website_authproxy import (
    _STATIC_IMAGES_TTL_S,
    _STATIC_MASTER_TTL_S,
    WebsiteAuthProxyConnector,
)

VIN = "WVWZZZAUZ1234567"


# ── primary_url tier order ──────────────────────────────────────────────────────

class TestPrimaryUrlTierOrder:
    def test_side_beats_front_beats_arbitrary(self) -> None:
        # vw.de view-direction keys: a side/profile view is the hero, and it must
        # win over a front view, which in turn wins over an unlabelled key.
        urls = {
            "misc_zero": "https://vw.example/z.png",
            "front_center": "https://vw.example/f.png",
            "side_left": "https://vw.example/s.png",
        }
        assert VehicleImageFetcher.primary_url(urls) == "https://vw.example/s.png"

    def test_front_beats_arbitrary_when_no_side(self) -> None:
        urls = {
            "misc_zero": "https://vw.example/z.png",
            "front_center": "https://vw.example/f.png",
        }
        assert VehicleImageFetcher.primary_url(urls) == "https://vw.example/f.png"

    def test_profile_and_german_seiten_also_count_as_tier1(self) -> None:
        assert VehicleImageFetcher.primary_url(
            {"back": "https://x/b.png", "profile_r": "https://x/p.png"}
        ) == "https://x/p.png"
        assert VehicleImageFetcher.primary_url(
            {"back": "https://x/b.png", "Seiten_links": "https://x/s.png"}
        ) == "https://x/s.png"

    def test_three_quarter_angle_is_tier2(self) -> None:
        urls = {"top_zero": "https://x/z.png", "3_4_left": "https://x/a.png"}
        assert VehicleImageFetcher.primary_url(urls) == "https://x/a.png"

    def test_arbitrary_only_returns_first_value(self) -> None:
        urls = {"zzz": "https://x/z.png", "yyy": "https://x/y.png"}
        assert VehicleImageFetcher.primary_url(urls) == "https://x/z.png"

    def test_none_and_empty_are_none(self) -> None:
        assert VehicleImageFetcher.primary_url(None) is None
        assert VehicleImageFetcher.primary_url({}) is None

    def test_blank_value_is_skipped_within_tier(self) -> None:
        # a side key with an empty URL must not win — fall through to the real one.
        urls = {"side_left": "", "front_center": "https://x/f.png"}
        assert VehicleImageFetcher.primary_url(urls) == "https://x/f.png"

    def test_audi_media_ids_still_preferred_for_that_backend(self) -> None:
        # The vgql backend keys by media-id (no side/front/angle/3_4 substring),
        # so tiers 1+2 don't match and tier 3 restores the curated media order:
        # MYAPN8NB (the ⭐ side-large) beats a later media id in the dict.
        urls = {
            _PREFERRED_ORDER[3]: "https://audi.example/late.png",
            _PREFERRED_ORDER[0]: "https://audi.example/best.png",
        }
        assert VehicleImageFetcher.primary_url(urls) == "https://audi.example/best.png"
        assert _PREFERRED_ORDER[0] == "MYAPN8NB"

    def test_unknown_media_id_falls_through_to_first_value(self) -> None:
        urls = {"ZZUNKNOWN": "https://audi.example/u.png"}
        assert VehicleImageFetcher.primary_url(urls) == "https://audi.example/u.png"


# ── best_url delegation ─────────────────────────────────────────────────────────

class TestBestUrlDelegation:
    def test_best_url_delegates_to_primary_url(self) -> None:
        urls = {"front_center": "https://x/f.png", "side_left": "https://x/s.png"}
        assert VehicleImageFetcher.best_url(urls) == VehicleImageFetcher.primary_url(urls)
        # the hero side render, not the first-inserted front one
        assert VehicleImageFetcher.best_url(urls) == "https://x/s.png"

    def test_best_url_none_and_empty(self) -> None:
        assert VehicleImageFetcher.best_url(None) is None
        assert VehicleImageFetcher.best_url({}) is None

    def test_best_url_audi_backend_unchanged(self) -> None:
        # regression: a pure media-id dict still returns the curated best id.
        urls = {_PREFERRED_ORDER[2]: "https://x/2.png", _PREFERRED_ORDER[0]: "https://x/0.png"}
        assert VehicleImageFetcher.best_url(urls) == "https://x/0.png"


# ── static-data TTL caches ──────────────────────────────────────────────────────

def _conn() -> WebsiteAuthProxyConnector:
    c = WebsiteAuthProxyConnector.__new__(WebsiteAuthProxyConnector)
    c._master_cache = {}
    c._images_cache = {}
    return c


class TestMasterDataTtl:
    def test_second_poll_within_ttl_skips_the_gets(self) -> None:
        c = _conn()
        # details + data endpoints; both return a body on the first poll.
        c._get_json = AsyncMock(side_effect=[
            {"modelName": "Tiguan", "engine": "110 kW (150 PS)"},
            {"vin": VIN, "exteriorColor": "0R"},
        ])
        first = asyncio.run(c.get_master_data(VIN))
        assert first.model_name == "Tiguan"
        assert c._get_json.await_count == 2
        # second poll within the 24 h window → no further GET, same object back.
        second = asyncio.run(c.get_master_data(VIN))
        assert second is first
        assert c._get_json.await_count == 2

    def test_poll_after_ttl_refetches(self) -> None:
        c = _conn()
        c._get_json = AsyncMock(side_effect=[
            {"modelName": "Tiguan"},
            {"vin": VIN},
            {"modelName": "Passat"},
            {"vin": VIN},
        ])
        asyncio.run(c.get_master_data(VIN))
        assert c._get_json.await_count == 2
        # age the stored entry past the TTL, then poll again → fresh fetch.
        ts, info = c._master_cache[VIN]
        c._master_cache[VIN] = (ts - _STATIC_MASTER_TTL_S - 1.0, info)
        refetched = asyncio.run(c.get_master_data(VIN))
        assert refetched.model_name == "Passat"
        assert c._get_json.await_count == 4

    def test_failed_fetch_keeps_prior_cache(self) -> None:
        c = _conn()
        c._get_json = AsyncMock(side_effect=[
            {"modelName": "Tiguan"},
            {"vin": VIN},
        ])
        good = asyncio.run(c.get_master_data(VIN))
        # expire it, then make BOTH endpoints soft-fail on the next poll.
        ts, info = c._master_cache[VIN]
        c._master_cache[VIN] = (ts - _STATIC_MASTER_TTL_S - 1.0, info)
        c._get_json = AsyncMock(return_value=None)
        kept = asyncio.run(c.get_master_data(VIN))
        assert kept is good                     # prior good info preserved
        assert kept.model_name == "Tiguan"
        assert c._get_json.await_count == 2     # it did try both endpoints

    def test_cold_miss_with_total_failure_returns_empty_and_caches_nothing(self) -> None:
        c = _conn()
        c._get_json = AsyncMock(return_value=None)
        info = asyncio.run(c.get_master_data(VIN))
        assert info.model_name is None
        assert VIN not in c._master_cache   # nothing worth caching


class TestExteriorImagesTtl:
    def test_second_poll_within_ttl_skips_the_get(self) -> None:
        c = _conn()
        body = {"images": [
            {"url": "https://vw.example/s.png", "angle": "Left", "viewDirection": "Side"},
        ]}
        c._get_json = AsyncMock(return_value=body)
        first = asyncio.run(c.get_exterior_images(VIN))
        assert len(first) == 1
        assert c._get_json.await_count == 1
        second = asyncio.run(c.get_exterior_images(VIN))
        assert second is first
        assert c._get_json.await_count == 1

    def test_poll_after_ttl_refetches(self) -> None:
        c = _conn()
        c._get_json = AsyncMock(side_effect=[
            {"images": [{"url": "https://vw.example/a.png"}]},
            {"images": [{"url": "https://vw.example/b.png"}]},
        ])
        asyncio.run(c.get_exterior_images(VIN))
        assert c._get_json.await_count == 1
        ts, imgs = c._images_cache[VIN]
        c._images_cache[VIN] = (ts - _STATIC_IMAGES_TTL_S - 1.0, imgs)
        refetched = asyncio.run(c.get_exterior_images(VIN))
        assert refetched[0].url == "https://vw.example/b.png"
        assert c._get_json.await_count == 2

    def test_failed_fetch_keeps_prior_cache(self) -> None:
        c = _conn()
        c._get_json = AsyncMock(return_value={"images": [{"url": "https://vw.example/a.png"}]})
        good = asyncio.run(c.get_exterior_images(VIN))
        ts, imgs = c._images_cache[VIN]
        c._images_cache[VIN] = (ts - _STATIC_IMAGES_TTL_S - 1.0, imgs)
        c._get_json = AsyncMock(return_value=None)
        kept = asyncio.run(c.get_exterior_images(VIN))
        assert kept is good
        assert kept[0].url == "https://vw.example/a.png"
        assert c._get_json.await_count == 1

    def test_cold_miss_with_failure_returns_empty(self) -> None:
        c = _conn()
        c._get_json = AsyncMock(return_value=None)
        assert asyncio.run(c.get_exterior_images(VIN)) == []
        assert VIN not in c._images_cache


def test_module_ttl_constants_are_24h_and_6h() -> None:
    # pin the adopted TTLs so a later edit can't silently shorten/lengthen them.
    assert _website_authproxy._STATIC_MASTER_TTL_S == 86400
    assert _website_authproxy._STATIC_IMAGES_TTL_S == 21600
