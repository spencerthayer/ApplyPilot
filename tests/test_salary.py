"""Tests for applypilot.salary — PPP, FX, range derivation, exploitation warnings."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from applypilot.salary import (
    PPPResult,
    SalaryRange,
    clean_number,
    parse_range,
    to_usd,
    _resolve_country,
    _resolve_country_from_currency,
)

# ---------------------------------------------------------------------------
# Fixtures — deterministic PPP/FX data (no network calls)
# ---------------------------------------------------------------------------

_MOCK_PPP = {"US": 1.0, "IN": 20.0, "DE": 0.7, "SG": 0.8, "VN": 6800.0, "GB": 0.67}
_MOCK_FX = {"USD": 1.0, "INR": 83.0, "EUR": 0.92, "SGD": 1.34, "VND": 24500.0, "GBP": 0.79}


@pytest.fixture(autouse=True)
def _mock_rates(monkeypatch):
    """Patch live API calls with deterministic data for all tests."""
    monkeypatch.setattr("applypilot.salary.get_ppp_rates", lambda: _MOCK_PPP)
    monkeypatch.setattr("applypilot.salary.get_fx_rates", lambda: _MOCK_FX)


# ---------------------------------------------------------------------------
# clean_number
# ---------------------------------------------------------------------------


class TestCleanNumber:
    def test_strips_currency_symbols(self):
        assert clean_number("$120,000") == 120000.0

    def test_strips_spaces(self):
        assert clean_number("  80 000  ") == 80000.0

    def test_empty_returns_zero(self):
        assert clean_number("") == 0.0

    def test_non_numeric_returns_zero(self):
        assert clean_number("abc") == 0.0

    def test_decimal(self):
        assert clean_number("99999.50") == 99999.50


# ---------------------------------------------------------------------------
# parse_range
# ---------------------------------------------------------------------------


class TestParseRange:
    def test_dash_separated(self):
        assert parse_range("80000-120000") == ("80000", "120000")

    def test_with_currency_symbols(self):
        assert parse_range("$80,000 - $120,000") == ("80000", "120000")

    def test_empty_uses_fallback(self):
        assert parse_range("", fallback=100000) == ("100000", "100000")

    def test_empty_no_fallback(self):
        assert parse_range("") == ("", "")


# ---------------------------------------------------------------------------
# Country resolution
# ---------------------------------------------------------------------------


class TestResolveCountry:
    def test_full_name(self):
        assert _resolve_country("India") == "IN"

    def test_case_insensitive(self):
        assert _resolve_country("germany") == "DE"

    def test_city_with_country(self):
        assert _resolve_country("Berlin, Germany") == "DE"

    def test_alpha2_passthrough(self):
        assert _resolve_country("US") == "US"

    def test_remote_returns_empty(self):
        assert _resolve_country("Remote") == ""

    def test_unknown_returns_empty(self):
        assert _resolve_country("Narnia") == ""


class TestResolveCountryFromCurrency:
    def test_inr(self):
        assert _resolve_country_from_currency("INR") == "IN"

    def test_usd(self):
        assert _resolve_country_from_currency("USD") == "US"

    def test_unknown(self):
        assert _resolve_country_from_currency("XYZ") == ""


# ---------------------------------------------------------------------------
# SalaryRange.from_current (simple, no PPP)
# ---------------------------------------------------------------------------


class TestSalaryRangeSimple:
    def test_default_multipliers(self):
        r = SalaryRange.from_current(100000)
        assert r.range_min == 140000
        assert r.range_max == 200000
        assert r.expected == 170000

    def test_custom_multipliers(self):
        r = SalaryRange.from_current(100000, low_mult=1.2, high_mult=1.5)
        assert r.range_min == 120000
        assert r.range_max == 150000

    def test_no_warning(self):
        assert SalaryRange.from_current(100000).warning == ""


# ---------------------------------------------------------------------------
# SalaryRange.from_current_ppp — the core PPP scenarios
# ---------------------------------------------------------------------------


class TestSalaryRangePPP:
    """
    PPP math: current_local / source_ppp × target_ppp = equivalent in target currency.
    Then apply hike multipliers on that base.
    """

    def test_india_to_us_upward_move(self):
        """₹20L in India → US. Should give ~$100k equivalent, no warning."""
        r = SalaryRange.from_current_ppp(2_000_000, "INR", "United States")
        # 2_000_000 / 20.0 (IN PPP) × 1.0 (US PPP) = 100_000 base
        assert r.currency == "USD"
        assert r.range_min == 140_000  # 100k × 1.4
        assert r.range_max == 200_000  # 100k × 2.0
        assert r.expected == 170_000  # 100k × 1.7
        assert r.warning == ""  # upward move — no warning

    def test_us_to_vietnam_downward_warns(self):
        """$150k US → Vietnam. Massively cheaper economy — must warn."""
        r = SalaryRange.from_current_ppp(150_000, "USD", "Vietnam")
        # 150_000 / 1.0 × 6800.0 = 1_020_000_000 VND base
        assert r.currency == "VND"
        assert r.warning  # non-empty
        assert "lower-cost economy" in r.warning
        assert "+15%" in r.warning

    def test_india_to_vietnam_downward_warns(self):
        """₹20L India → Vietnam. VN is even cheaper than India — must warn."""
        r = SalaryRange.from_current_ppp(2_000_000, "INR", "Vietnam")
        assert r.currency == "VND"
        assert r.warning
        assert "lower-cost economy" in r.warning

    def test_us_to_singapore_no_warning(self):
        """$150k US → Singapore. SG PPP 0.8 < US PPP 1.0 — more expensive, no warning."""
        r = SalaryRange.from_current_ppp(150_000, "USD", "Singapore")
        assert r.currency == "SGD"
        assert r.warning == ""

    def test_same_country_no_warning(self):
        """India → India. Same PPP, just a normal hike."""
        r = SalaryRange.from_current_ppp(2_000_000, "INR", "India")
        assert r.currency == "INR"
        assert r.range_min == 2_800_000  # 2M × 1.4
        assert r.range_max == 4_000_000  # 2M × 2.0
        assert r.warning == ""

    def test_remote_no_ppp(self):
        """Remote resolves to empty country — falls back gracefully."""
        r = SalaryRange.from_current_ppp(2_000_000, "INR", "Remote")
        # source_ppp=20.0, target_ppp=1.0 (default), target_currency=INR (fallback)
        assert r.warning == ""  # can't determine direction without target country

    def test_unknown_location_no_crash(self):
        """Unknown location should not crash."""
        r = SalaryRange.from_current_ppp(100_000, "USD", "Narnia")
        assert r.warning == ""


# ---------------------------------------------------------------------------
# PPPResult.convert
# ---------------------------------------------------------------------------


class TestPPPResult:
    def test_known_country(self):
        p = PPPResult.convert(100_000, "India")
        assert p.known is True
        assert p.currency == "INR"
        assert p.equivalent == 2_000_000  # 100k × 20.0
        assert "World Bank" in p.source

    def test_unknown_country(self):
        p = PPPResult.convert(100_000, "Narnia")
        assert p.known is False
        assert p.currency == "USD"
        assert p.equivalent == 100_000

    def test_remote_not_mapped(self):
        p = PPPResult.convert(100_000, "Remote")
        assert p.known is False

    def test_display_format(self):
        p = PPPResult.convert(100_000, "India")
        assert p.display() == "INR 2,000,000"


# ---------------------------------------------------------------------------
# to_usd
# ---------------------------------------------------------------------------


class TestToUsd:
    def test_inr_to_usd(self):
        result = to_usd(830_000, "INR")  # 830k / 83.0
        assert abs(result - 10_000) < 1

    def test_usd_passthrough(self):
        assert to_usd(100_000, "USD") == 100_000

    def test_zero_amount(self):
        assert to_usd(0, "INR") == 0
