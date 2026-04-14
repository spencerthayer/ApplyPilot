"""Tests for discovery/location_resolver.py"""

from __future__ import annotations

import pytest
from applypilot.discovery.location_resolver import resolve_country


class TestResolveCountry:
    """Country resolution from job board location strings."""

    # ── Standard formats ──────────────────────────────────────────────

    def test_city_country(self):
        assert resolve_country("Barcelona, Spain") == "ES"

    def test_city_state_us(self):
        assert resolve_country("Palo Alto, California") == "US"

    def test_city_state_abbrev_us(self):
        """Two-letter state abbreviation after city."""
        result = resolve_country("Denver, CO")
        assert result == "US"

    def test_state_only(self):
        assert resolve_country("North Carolina") == "US"

    def test_country_only(self):
        assert resolve_country("India") == "IN"

    def test_city_is_country(self):
        assert resolve_country("Singapore") == "SG"

    # ── Remote/hybrid prefixes ────────────────────────────────────────

    def test_remote_prefix_dash(self):
        assert resolve_country("Remote - North Carolina") == "US"

    def test_remote_prefix_comma(self):
        assert resolve_country("Remote, California") == "US"

    def test_remote_only(self):
        assert resolve_country("Remote") is None

    def test_hybrid_prefix(self):
        result = resolve_country("Hybrid - London, UK")
        assert result in ("GB", "UK")

    # ── Disambiguation ────────────────────────────────────────────────

    def test_hyderabad_india(self):
        """Hyderabad exists in both India and Pakistan — country hint wins."""
        assert resolve_country("Hyderabad, India") == "IN"

    def test_georgia_country(self):
        """Tbilisi, Georgia = the country, not the US state."""
        assert resolve_country("Tbilisi, Georgia") == "GE"

    # ── Edge cases ────────────────────────────────────────────────────

    def test_empty_string(self):
        assert resolve_country("") is None

    def test_none(self):
        assert resolve_country(None) is None

    def test_multi_city(self):
        """Multiple cities — should still resolve a country."""
        result = resolve_country("Boston, Massachusetts; New York, New York")
        assert result == "US"

    def test_worldwide(self):
        assert resolve_country("Worldwide") is None

    def test_uk_code(self):
        result = resolve_country("London, UK")
        assert result in ("GB", "UK")

    def test_non_english_city(self):
        """GeoNames has alternative names."""
        result = resolve_country("München, Germany")
        # Should resolve to DE via "Germany" even if München isn't found
        assert result == "DE"
