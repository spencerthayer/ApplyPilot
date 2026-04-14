"""Utilities for safe hostname and path matching."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_hostname(hostname: str | None) -> str:
    """Return a normalized hostname for safe comparisons."""
    return (hostname or "").strip().lower().rstrip(".")


def parse_hostname(url: str | None) -> str:
    """Parse and normalize the hostname from a URL."""
    if not url:
        return ""
    return normalize_hostname(urlparse(url).hostname)


def host_matches(host: str | None, domain: str) -> bool:
    """Return True for an exact domain match or a true subdomain."""
    normalized_host = normalize_hostname(host)
    normalized_domain = normalize_hostname(domain)
    if not normalized_host or not normalized_domain:
        return False
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def host_matches_any(host: str | None, domains: list[str] | tuple[str, ...] | set[str]) -> bool:
    """Return True if the host matches any exact domain or true subdomain."""
    return any(host_matches(host, domain) for domain in domains)


def subdomain_prefix(host: str | None, domain: str) -> str | None:
    """Return the subdomain prefix before a matched suffix."""
    normalized_host = normalize_hostname(host)
    normalized_domain = normalize_hostname(domain)
    if not host_matches(normalized_host, normalized_domain):
        return None
    if normalized_host == normalized_domain:
        return None
    return normalized_host[: -(len(normalized_domain) + 1)]


def path_segments(path: str | None) -> list[str]:
    """Return cleaned path segments."""
    return [segment for segment in (path or "").split("/") if segment]


def is_algolia_queries_url(url: str) -> bool:
    """Return True only for real Algolia query endpoints."""
    parsed = urlparse(url)
    if not host_matches(parsed.hostname, "algolia.net"):
        return False
    segments = path_segments(parsed.path)
    return bool(segments) and segments[-1] == "queries"


def extract_company(application_url: str | None) -> str | None:
    """Extract a company name from an application URL domain.

    Handles: Workday, Greenhouse, Lever, iCIMS, Jobvite, Ashby,
    Rippling, Workable, Recruitee, SmartRecruiters, direct domains.
    """
    if not application_url:
        return None
    try:
        import re
        from urllib.parse import parse_qs, unquote

        parsed = urlparse(application_url)
        host = parsed.hostname or ""
        path = parsed.path or ""
        segs = path_segments(path)

        if m := re.match(r"^(?P<c>[^.]+)(?:\.wd[^.]*)?\.myworkdayjobs\.com$", host, re.I):
            return m.group("c").lower()
        if host in {"job-boards.greenhouse.io", "boards.greenhouse.io"}:
            qs = parse_qs(parsed.query)
            if "for" in qs:
                return qs["for"][0].lower()
            parts = [p for p in segs if p not in ("embed", "job_app")]
            return parts[0].lower() if parts else None
        if host_matches(host, "lever.co") and segs:
            return segs[0].lower()
        if host_matches(host, "icims.com"):
            p = subdomain_prefix(host, "icims.com")
            return p.replace("careers-", "").replace("careers.", "").lower() if p else None
        if host_matches(host, "jobvite.com"):
            parts = [p for p in segs if p != "en"]
            return parts[0].lower() if parts else None
        if host_matches(host, "ashbyhq.com") and segs:
            return unquote(segs[0]).lower()
        if host_matches(host, "ats.rippling.com") and segs:
            return segs[0].lower()
        if host_matches(host, "workable.com") and segs and segs[0] != "j":
            return segs[0].lower()
        if host_matches(host, "recruitee.com"):
            s = subdomain_prefix(host, "recruitee.com")
            return s.lower() if s and s not in ("www", "app", "jobs") else None
        if host_matches(host, "smartrecruiters.com") and segs:
            return segs[0].lower()
        if host_matches(host, "oraclecloud.com") or host == "grnh.se":
            return None

        skip = {
            "linkedin.com",
            "indeed.com",
            "glassdoor.com",
            "ziprecruiter.com",
            "dice.com",
            "simplyhired.com",
            "monster.com",
            "careerjet.ca",
            "talent.com",
            "jobbank.gc.ca",
            "wellfound.com",
        }
        if host_matches_any(host, skip):
            return None

        parts = host.split(".")
        if len(parts) >= 2:
            c = parts[1] if parts[0] in ("jobs", "careers", "career", "www", "apply", "hire") else parts[-2]
            return c.lower() if c not in ("com", "org", "net", "io", "co", "ca") else None
        return None
    except Exception:
        return None
