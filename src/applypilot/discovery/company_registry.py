"""Company registry — canonical company records with per-runner routing.

Merges package-shipped config/companies.yaml with user overrides at
~/.applypilot/companies.yaml. Provides resolution by key, name, alias,
or domain for --company filtering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from applypilot.config.paths import APP_DIR, CONFIG_DIR

log = logging.getLogger(__name__)

_registry: CompanyRegistry | None = None


@dataclass(frozen=True)
class CompanyRecord:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)
    domain: str = ""
    career_url: str = ""
    runners: dict[str, str] = field(default_factory=dict)  # runner_name → employer_key
    ats: str = "unknown"
    source: str = "package"  # "package" | "user"


class CompanyRegistry:
    """Load once, resolve many. User YAML overrides package YAML."""

    def __init__(self) -> None:
        self._companies: dict[str, CompanyRecord] = {}
        self._alias_index: dict[str, str] = {}

    def load(self) -> None:
        self._companies.clear()
        self._alias_index.clear()
        # Package defaults first
        self._load_yaml(CONFIG_DIR / "companies.yaml", source="package")
        # User overrides
        self._load_yaml(APP_DIR / "companies.yaml", source="user")
        # Auto-generate from existing per-ATS YAMLs if companies.yaml doesn't exist
        if not self._companies:
            self._generate_from_ats_yamls()
        log.debug("Company registry: %d companies loaded", len(self._companies))

    def _load_yaml(self, path: Path, source: str) -> None:
        if not path.exists():
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.warning("Failed to load %s: %s", path, e)
            return
        for key, entry in (data.get("companies") or {}).items():
            if not isinstance(entry, dict):
                continue
            rec = CompanyRecord(
                key=key,
                name=entry.get("name", key),
                aliases=[a.lower() for a in entry.get("aliases", [])],
                domain=entry.get("domain", ""),
                career_url=entry.get("career_url", ""),
                runners=entry.get("runners", {}),
                ats=entry.get("ats", "unknown"),
                source=source,
            )
            self._companies[key] = rec
            self._alias_index[rec.name.lower()] = key
            for alias in rec.aliases:
                self._alias_index[alias] = key

    def _generate_from_ats_yamls(self) -> None:
        """Bootstrap registry from existing employers.yaml, greenhouse.yaml, etc."""
        # Workday
        wp = CONFIG_DIR / "employers.yaml"
        if wp.exists():
            try:
                data = yaml.safe_load(wp.read_text(encoding="utf-8")) or {}
                for key, emp in (data.get("employers") or {}).items():
                    if not isinstance(emp, dict):
                        continue
                    name = emp.get("name", key)
                    self._add_generated(key, name, "workday")
            except Exception:
                pass
        # Greenhouse
        gp = CONFIG_DIR / "greenhouse.yaml"
        if gp.exists():
            try:
                data = yaml.safe_load(gp.read_text(encoding="utf-8")) or {}
                for key, emp in (data.get("employers") or {}).items():
                    if not isinstance(emp, dict):
                        continue
                    name = emp.get("name", key)
                    self._add_generated(key, name, "greenhouse")
            except Exception:
                pass

    def _add_generated(self, key: str, name: str, runner: str) -> None:
        if key in self._companies:
            # Merge runner into existing record
            old = self._companies[key]
            merged_runners = {**old.runners, runner: key}
            self._companies[key] = CompanyRecord(
                key=key,
                name=old.name,
                aliases=old.aliases,
                domain=old.domain,
                career_url=old.career_url,
                runners=merged_runners,
                ats=old.ats,
                source=old.source,
            )
        else:
            rec = CompanyRecord(key=key, name=name, runners={runner: key}, source="generated")
            self._companies[key] = rec
            self._alias_index[name.lower()] = key

    def resolve(self, query: str) -> CompanyRecord | None:
        """Resolve by key, name, alias, or domain. Case-insensitive."""
        q = query.lower().strip()
        if q in self._companies:
            return self._companies[q]
        if q in self._alias_index:
            return self._companies[self._alias_index[q]]
        for rec in self._companies.values():
            if rec.domain and (q == rec.domain or q in rec.domain):
                return rec
        for rec in self._companies.values():
            if q in rec.name.lower():
                return rec
        return None

    def resolve_many(self, queries: list[str]) -> tuple[list[CompanyRecord], list[str]]:
        """Returns (resolved_records, unresolved_query_strings)."""
        resolved, unresolved = [], []
        for q in queries:
            rec = self.resolve(q)
            if rec:
                resolved.append(rec)
            else:
                unresolved.append(q)
        return resolved, unresolved

    def matches_scraped_name(self, scraped: str, record: CompanyRecord) -> bool:
        """Check if a scraped company name matches this registry entry."""
        s = scraped.lower().strip()
        candidates = [record.name.lower()] + list(record.aliases)
        return any(c in s or s in c for c in candidates)

    def save_user_entry(self, record: CompanyRecord) -> None:
        """Append to ~/.applypilot/companies.yaml."""
        path = APP_DIR / "companies.yaml"
        data: dict = {}
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        companies = data.setdefault("companies", {})
        companies[record.key] = {
            "name": record.name,
            "aliases": record.aliases,
            "domain": record.domain,
            "career_url": record.career_url,
            "runners": record.runners,
            "ats": record.ats,
        }
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")


def get_registry() -> CompanyRegistry:
    """Singleton access to the company registry."""
    global _registry
    if _registry is None:
        _registry = CompanyRegistry()
        _registry.load()
    return _registry
