"""Salary utilities: cleaning, range derivation, PPP conversion, FX rates.

Data sources:
  - PPP: World Bank API (PA.NUS.PPP indicator) — cached locally, refreshed monthly.
  - FX:  open.er-api.com (free, no key) — cached locally, refreshed daily.

Single Responsibility: pure data transformations + caching, no I/O prompting.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".applypilot" / "cache"
_PPP_CACHE = _CACHE_DIR / "ppp.json"
_FX_CACHE = _CACHE_DIR / "fx.json"

_PPP_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days
_FX_MAX_AGE_SECONDS = 24 * 3600  # 1 day
_REQUEST_TIMEOUT = 15

# Country name/code aliases → ISO-3166 alpha-2 (for PPP lookup).
_COUNTRY_ALIASES: dict[str, str] = {
    "USA": "US",
    "United States": "US",
    "America": "US",
    "UK": "GB",
    "United Kingdom": "GB",
    "England": "GB",
    "India": "IN",
    "Germany": "DE",
    "France": "FR",
    "Canada": "CA",
    "Australia": "AU",
    "Singapore": "SG",
    "Japan": "JP",
    "Netherlands": "NL",
    "Ireland": "IE",
    "Sweden": "SE",
    "Switzerland": "CH",
    "Poland": "PL",
    "Brazil": "BR",
    "Italy": "IT",
    "Spain": "ES",
    "Austria": "AT",
    "Belgium": "BE",
    "Portugal": "PT",
    "Finland": "FI",
    "Norway": "NO",
    "Denmark": "DK",
    "New Zealand": "NZ",
    "South Korea": "KR",
    "Korea": "KR",
    "Mexico": "MX",
    "Argentina": "AR",
    "Chile": "CL",
    "Colombia": "CO",
    "South Africa": "ZA",
    "UAE": "AE",
    "Israel": "IL",
    "China": "CN",
    "Hong Kong": "HK",
    "Taiwan": "TW",
    "Czech Republic": "CZ",
    "Czechia": "CZ",
    "Romania": "RO",
    "Hungary": "HU",
    "Philippines": "PH",
    "Vietnam": "VN",
    "Thailand": "TH",
    "Indonesia": "ID",
    "Malaysia": "MY",
}

# ---------------------------------------------------------------------------
# Generic cache helpers
# ---------------------------------------------------------------------------


def _read_cache(path: Path, max_age: int) -> dict | None:
    """Return cached JSON if fresh, else None."""
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > max_age:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _fetch_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "ApplyPilot/1.0"})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# PPP data (World Bank)
# ---------------------------------------------------------------------------


def _fetch_ppp() -> dict[str, float]:
    """Fetch PPP conversion factors from World Bank. Returns {country_code: ppp_factor}."""
    url = "https://api.worldbank.org/v2/country/all/indicator/PA.NUS.PPP?date=2023&format=json&per_page=300"
    raw = _fetch_json(url)
    entries = raw[1] if isinstance(raw, list) and len(raw) > 1 else []
    result: dict[str, float] = {}
    for entry in entries:
        value = entry.get("value")
        # World Bank uses ISO-3166 alpha-3 in countryiso3code, alpha-2 in country.id
        alpha2 = entry.get("country", {}).get("id", "")
        if alpha2 and value is not None:
            result[alpha2] = float(value)
    return result


def get_ppp_rates() -> dict[str, float]:
    """Return {country_alpha2: ppp_factor} with caching."""
    cached = _read_cache(_PPP_CACHE, _PPP_MAX_AGE_SECONDS)
    if cached and "rates" in cached:
        return cached["rates"]
    try:
        rates = _fetch_ppp()
        _write_cache(_PPP_CACHE, {"rates": rates, "fetched": time.time(), "source": "worldbank"})
        log.info("PPP rates fetched: %d countries", len(rates))
        return rates
    except Exception as exc:
        log.warning("Failed to fetch PPP rates: %s — using empty table", exc)
        return {}


# ---------------------------------------------------------------------------
# FX data (open.er-api.com)
# ---------------------------------------------------------------------------


def _fetch_fx() -> dict[str, float]:
    """Fetch exchange rates vs USD. Returns {currency_code: rate_per_usd}."""
    raw = _fetch_json("https://open.er-api.com/v6/latest/USD")
    return {k: float(v) for k, v in raw.get("rates", {}).items()}


def get_fx_rates() -> dict[str, float]:
    """Return {currency_code: units_per_usd} with caching."""
    cached = _read_cache(_FX_CACHE, _FX_MAX_AGE_SECONDS)
    if cached and "rates" in cached:
        return cached["rates"]
    try:
        rates = _fetch_fx()
        _write_cache(_FX_CACHE, {"rates": rates, "fetched": time.time(), "source": "open.er-api.com"})
        log.info("FX rates fetched: %d currencies", len(rates))
        return rates
    except Exception as exc:
        log.warning("Failed to fetch FX rates: %s — using empty table", exc)
        return {}


# ---------------------------------------------------------------------------
# Currency for a country (ISO-4217 mapping, common subset)
# ---------------------------------------------------------------------------

_COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD",
    "IN": "INR",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
    "NL": "EUR",
    "IE": "EUR",
    "CA": "CAD",
    "AU": "AUD",
    "SG": "SGD",
    "JP": "JPY",
    "SE": "SEK",
    "CH": "CHF",
    "PL": "PLN",
    "BR": "BRL",
    "IT": "EUR",
    "ES": "EUR",
    "AT": "EUR",
    "BE": "EUR",
    "PT": "EUR",
    "FI": "EUR",
    "NO": "NOK",
    "DK": "DKK",
    "NZ": "NZD",
    "KR": "KRW",
    "MX": "MXN",
    "AR": "ARS",
    "CL": "CLP",
    "CO": "COP",
    "ZA": "ZAR",
    "AE": "AED",
    "IL": "ILS",
    "CN": "CNY",
    "HK": "HKD",
    "TW": "TWD",
    "VN": "VND",
    "TH": "THB",
    "PH": "PHP",
    "ID": "IDR",
    "MY": "MYR",
    "RO": "RON",
    "HU": "HUF",
    "CZ": "CZK",
}


def _resolve_country(location: str) -> str:
    """Best-effort resolve a location string to ISO alpha-2 country code.

    Returns empty string for unresolvable locations (e.g. 'Remote').
    """
    loc = location.strip()
    # Case-insensitive exact alias match
    for alias, code in _COUNTRY_ALIASES.items():
        if alias.lower() == loc.lower():
            return code
    # Already a 2-letter code?
    if len(loc) == 2 and loc.isalpha():
        return loc.upper()
    # Substring match (e.g. "Berlin, Germany" → DE)
    loc_lower = loc.lower()
    for alias, code in _COUNTRY_ALIASES.items():
        if alias.lower() in loc_lower:
            return code
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def clean_number(raw: str) -> float:
    """Strip currency symbols, commas, spaces and return a float. Returns 0 on empty/invalid."""
    cleaned = re.sub(r"[^0-9.]", "", raw.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def parse_range(range_str: str, fallback: float = 0) -> tuple[str, str]:
    """Parse '80000-120000' into (min, max) strings. Falls back to (fallback, fallback)."""
    cleaned = re.sub(r"[$,\s]", "", range_str)
    if "-" in cleaned:
        parts = cleaned.split("-", 1)
        return parts[0].strip(), parts[1].strip()
    fb = str(int(fallback)) if fallback else ""
    return fb, fb


@dataclass(frozen=True)
class SalaryRange:
    """Derived salary range from current compensation."""

    expected: int
    range_min: int
    range_max: int
    currency: str
    note: str  # explains how it was derived
    warning: str  # non-empty if the ask is unrealistic for the target market

    @staticmethod
    def from_current(current: float, low_mult: float = 1.4, high_mult: float = 2.0) -> SalaryRange:
        """Simple multiplier on raw current salary (same currency)."""
        return SalaryRange(
            expected=int(current * (low_mult + high_mult) / 2),
            range_min=int(current * low_mult),
            range_max=int(current * high_mult),
            currency="",
            note=f"+{int((low_mult - 1) * 100)}% to +{int((high_mult - 1) * 100)}% of current",
            warning="",
        )

    @staticmethod
    def from_current_ppp(
        current: float,
        current_currency: str,
        target_location: str,
        low_mult: float = 1.4,
        high_mult: float = 2.0,
    ) -> SalaryRange:
        """PPP-adjusted range: what salary in the target location has the same
        purchasing power as your current salary, then apply the hike.

        Math:
          1. purchasing_power_usd = current_local / source_ppp_factor
             (how many "international dollars" your salary is worth)
          2. target_local = purchasing_power_usd × target_ppp_factor
             (what local salary in target gives the same purchasing power)
          3. apply hike multipliers on that base

        Example: ₹20L / 20.29 (India PPP) = ~$98.6k intl$ → ×1.0 (US PPP) = $98.6k USD base
                 +40% = $138k, +100% = $197k
        """
        source_country = _resolve_country_from_currency(current_currency)
        target_country = _resolve_country(target_location)

        ppp_rates = get_ppp_rates()
        source_ppp = ppp_rates.get(source_country, 1.0) if source_country else 1.0
        target_ppp = ppp_rates.get(target_country, 1.0) if target_country else 1.0

        # Step 1: current salary → international dollars (purchasing power)
        purchasing_power = current / source_ppp if source_ppp > 0 else current

        # Step 2: international dollars → target local currency
        target_currency = _COUNTRY_CURRENCY.get(target_country, current_currency)
        base_local = purchasing_power * target_ppp

        # Detect "downward PPP" moves — moving to cheaper economy.
        # Higher PPP factor = cheaper economy (more local currency per intl$).
        # If target PPP > source PPP, you're moving somewhere cheaper,
        # so a hike on top of PPP-equivalent gives outsized purchasing power.
        warning = ""
        if target_ppp > source_ppp and target_country and source_country:
            ppp_at_par = base_local  # same purchasing power, no hike
            hiked = base_local * low_mult
            overshoot_pct = int(((hiked / ppp_at_par) - 1) * 100) if ppp_at_par else 0
            warning = (
                f"⚠ Moving to a lower-cost economy ({target_country} PPP {target_ppp:.1f} "
                f"> {source_country} PPP {source_ppp:.1f}). "
                f"A +{int((low_mult - 1) * 100)}% hike = +{overshoot_pct}% more purchasing power "
                f"than you have now — local employers may reject this. "
                f"Consider asking ≈{target_currency} {int(ppp_at_par):,} (PPP parity) "
                f"to {target_currency} {int(ppp_at_par * 1.15):,} (+15% realistic max)."
            )

        return SalaryRange(
            expected=int(base_local * (low_mult + high_mult) / 2),
            range_min=int(base_local * low_mult),
            range_max=int(base_local * high_mult),
            currency=target_currency,
            note=(
                f"PPP-adjusted: {current_currency} {int(current):,} → "
                f"≈{target_currency} {int(base_local):,} equivalent, "
                f"then +{int((low_mult - 1) * 100)}%–+{int((high_mult - 1) * 100)}%"
            ),
            warning=warning,
        )


def _resolve_country_from_currency(currency: str) -> str:
    """Reverse lookup: currency code → country alpha-2 (first match)."""
    for country, cur in _COUNTRY_CURRENCY.items():
        if cur == currency:
            return country
    return ""


def to_usd(amount: float, currency: str) -> float:
    """Convert an amount in any supported currency to USD using live FX rates."""
    if currency == "USD" or amount == 0:
        return amount
    fx = get_fx_rates()
    rate = fx.get(currency, 1.0)
    return amount / rate if rate else amount


@dataclass(frozen=True)
class PPPResult:
    """PPP conversion result for a target location."""

    equivalent: float
    currency: str
    ppp_rate: float
    known: bool
    source: str  # data provenance

    @staticmethod
    def convert(amount_usd: float, location: str) -> PPPResult:
        country = _resolve_country(location)
        if not country:
            return PPPResult(
                equivalent=amount_usd,
                currency="USD",
                ppp_rate=1.0,
                known=False,
                source="location not mapped",
            )
        ppp_rates = get_ppp_rates()
        ppp_factor = ppp_rates.get(country)
        if ppp_factor is None:
            return PPPResult(
                equivalent=amount_usd,
                currency="USD",
                ppp_rate=1.0,
                known=False,
                source="no PPP data for country",
            )
        currency = _COUNTRY_CURRENCY.get(country, "USD")
        return PPPResult(
            equivalent=round(amount_usd * ppp_factor),
            currency=currency,
            ppp_rate=ppp_factor,
            known=True,
            source="World Bank PA.NUS.PPP 2023",
        )

    def display(self) -> str:
        return f"{self.currency} {int(self.equivalent):,}"
