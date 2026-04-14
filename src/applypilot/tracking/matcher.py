"""Match incoming emails to applied jobs using multi-signal scoring.

Signals and weights:
  - Sender domain matches company/application_url domain: 40
  - Company name appears in subject or body: 25
  - Job title keyword overlap in subject: 20
  - ATS sender pattern (noreply, greenhouse, lever...): 10
  - Temporal proximity (within 30 days of applied_at): 5
  - Company extracted from subject matches job company: 35

Threshold: 40 points minimum.
"""

import logging
import re
from datetime import datetime
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Known ATS notification sender patterns
ATS_SENDER_PATTERNS = {
    "greenhouse.io",
    "lever.co",
    "icims.com",
    "myworkdayjobs.com",
    "jobvite.com",
    "smartrecruiters.com",
    "workable.com",
    "ashbyhq.com",
    "breezy.hr",
    "recruitee.com",
    "jazz.co",
}

ATS_SENDER_PREFIXES = {"noreply", "no-reply", "notifications", "careers", "jobs", "talent", "recruiting"}

# Regex patterns to extract company name from email subjects.
# Each pattern has one capture group for the company name.
_COMPANY_SUBJECT_PATTERNS = [
    # "Thank you for applying to Honor" / "applied at McGraw Hill"
    r"(?:applying|applied)\s+(?:to|at)\s+(.+?)(?:\s*[!.,]|\s+\b(?:has|have|had|was|is|are|will|would)\b|$)",
    # "position at TDS Telecom" / "role at Grafana" / "opportunity at Nova"
    r"(?:position|role|job|opportunity)\s+at\s+(.+?)(?:\s*[!.,]|\s+\b(?:has|have|had|was|is|are|will|would)\b|$)",
    # "application to Harness" / "application at Grafana Labs" / "interest in Nova"
    r"(?:application|interest)\s+(?:to|at|in|for)\s+(.+?)(?:\s*[!.,]|\s+\b(?:has|have|had|was|is|are|will|would)\b|$)",
    # "for your application to Openly" / "for your interest in ..."
    r"for\s+your\s+(?:application|interest)\s+(?:to|at|in)\s+(.+?)(?:\s*[!.,]|\s+\b(?:has|have|had|was|is|are|will|would)\b|$)",
    # "Security code for your application to Openly"
    r"security\s+code\s+for\s+your\s+application\s+to\s+(.+?)(?:\s*[!.,]|\s+\b(?:has|have|had|was|is|are)\b|$)",
    # "Important information about your application to Coinbase"
    r"information\s+about\s+your\s+application\s+to\s+(.+?)(?:\s*[!.,]|\s+\b(?:has|have|had|was|is|are)\b|$)",
    # "Thank you from Peach Finance"
    r"thank\s+you\s+from\s+(.+?)(?:\s*[!.,]|$)",
    # "Steer: Thank You for Your Application" / "ESO | We received your application"
    r"^(.+?)\s*[:|]\s+(?:thank\s+you|we\s+received|application|your\s+application)",
]

# Words too generic to be a company name
_GENERIC_WORDS = {
    "us",
    "the",
    "you",
    "your",
    "we",
    "our",
    "it",
    "a",
    "an",
    "this",
    "that",
    "they",
    "their",
    "its",
}

# Words that indicate a job title was captured instead of a company name
_JOB_TITLE_WORDS = {
    "engineer",
    "developer",
    "manager",
    "director",
    "analyst",
    "scientist",
    "architect",
    "designer",
    "consultant",
    "specialist",
    "coordinator",
    "senior",
    "junior",
    "staff",
    "principal",
    "associate",
    "lead",
    "head",
    "vp",
    "vice",
    "president",
    "officer",
    "cto",
    "ceo",
    "cfo",
    "coo",
}

# Legal suffixes to strip when normalizing
_LEGAL_SUFFIXES = re.compile(
    r"\s*,?\s+(?:inc|llc|ltd|corp|co|plc|gmbh|ag|sa|bv|nv|pty|pte|srl|sas|sro)\.?\s*$",
    re.IGNORECASE,
)


def extract_company_from_subject(subject: str) -> str | None:
    """Extract a company name from a job-related email subject line.

    Tries a series of patterns like "applying to {Company}" and returns
    the first non-trivial match. Returns None if nothing found.
    """
    s = subject.strip()
    for pattern in _COMPANY_SUBJECT_PATTERNS:
        m = re.search(pattern, s, re.IGNORECASE)
        if not m:
            continue
        candidate = m.group(1).strip().strip("!.,")
        if not candidate or len(candidate) < 2:
            continue
        if candidate.lower() in _GENERIC_WORDS:
            continue
        # Skip if it looks like a sentence (>5 words) — probably not a company name
        if len(candidate.split()) > 5:
            continue
        # Skip if it looks like a job title rather than a company name
        candidate_words = set(candidate.lower().split())
        if candidate_words & _JOB_TITLE_WORDS:
            continue
        return candidate
    return None


def _extract_company_from_snippet(snippet: str) -> str | None:
    """Extract company name from an email snippet (first ~100 chars of body).

    Covers patterns like "Thank you for applying to the SWE role at Oracle."
    """
    if not snippet:
        return None
    patterns = [
        r"at\s+([A-Z][A-Za-z0-9 &'.,-]{1,40}?)(?:\s*[.,!]|\s+we\b|\s+for\b|\s+is\b|$)",
        r"from\s+([A-Z][A-Za-z0-9 &'.,-]{1,40}?)(?:\s*[.,!]|\s+team\b|\s+recruiting\b|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, snippet)
        if m:
            candidate = m.group(1).strip().strip(".,!")
            if candidate and len(candidate) >= 2 and candidate.lower() not in _GENERIC_WORDS:
                return candidate
    return None


def normalize_company(name: str) -> str:
    """Normalize a company name for comparison: lowercase, strip legal suffixes and punctuation."""
    if not name:
        return ""
    n = _LEGAL_SUFFIXES.sub("", name)
    n = re.sub(r"[^\w\s]", "", n)  # strip punctuation
    return n.lower().strip()


def _slug(name: str) -> str:
    """Slug-normalize: remove all non-alphanumeric chars for URL-slug vs human-name matching.

    Handles cases like 'grafanalabs' (URL slug) == 'Grafana Labs' (email subject).
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _extract_domain(address: str) -> str:
    """Extract domain from email address or URL."""
    if "@" in address:
        return address.split("@")[-1].strip().lower()
    try:
        parsed = urlparse(address)
        host = parsed.hostname or ""
        return host.lower()
    except Exception:
        return address.lower()


def _domain_root(domain: str) -> str:
    """Get the root domain (last two parts): mail.kentik.com -> kentik.com."""
    parts = domain.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain


def _extract_company_from_url(url: str | None) -> str | None:
    """Extract company name from an application URL (simplified)."""
    if not url:
        return None
    from applypilot.tracking._compat import extract_company

    return extract_company(url)


def _title_keywords(title: str | None) -> set[str]:
    """Extract significant keywords from a job title."""
    if not title:
        return set()
    stops = {"the", "a", "an", "and", "or", "at", "in", "for", "of", "to", "with", "is", "are", "we"}
    words = re.findall(r"[a-z]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in stops}


def match_email_to_job(email: dict, applied_jobs: list[dict]) -> dict | None:
    """Match a single email to the best applied job.

    Args:
        email: Normalized email dict with keys: sender, subject, body, date.
        applied_jobs: List of job dicts from get_applied_jobs().

    Returns:
        Dict with {job_url, score, signals} if matched, else None.
    """
    sender = email.get("sender", "")
    sender_domain = _extract_domain(sender)
    sender_root = _domain_root(sender_domain)
    sender_local = sender.split("@")[0].lower() if "@" in sender else ""
    subject = (email.get("subject") or "").lower()
    body = (email.get("body") or "").lower()
    email_date_str = email.get("date", "")

    email_dt = None
    if email_date_str:
        try:
            email_dt = datetime.fromisoformat(email_date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    # Signal 6 pre-computation: extract company from subject once, before the job loop
    subject_company_raw = extract_company_from_subject(email.get("subject") or "")
    subject_company_norm = normalize_company(subject_company_raw) if subject_company_raw else ""

    best_match = None
    best_score = 0

    for job in applied_jobs:
        score = 0
        signals = []

        job_url = job["url"]
        app_url = job.get("application_url") or ""
        company = (job.get("company") or "").lower()
        title = job.get("title") or ""

        # Bonus for real pipeline jobs (not manual:// stubs) — breaks ties in favour of
        # the job the pipeline actually applied to over a stub created from an email.
        is_pipeline_job = not job_url.startswith("manual://")
        score = 20 if is_pipeline_job else 0

        # --- Signal 1: Sender domain matches company domain (40 pts) ---
        app_domain = _extract_domain(app_url) if app_url else ""
        app_root = _domain_root(app_domain) if app_domain else ""

        if sender_root and app_root and sender_root == app_root:
            score += 40
            signals.append(f"domain_match:{sender_root}")
        elif company and sender_root and len(company) > 3:
            # Require >3 chars to avoid short strings like "us" matching inside "greenhouse"
            if company in sender_root or sender_root.split(".")[0] == company:
                score += 40
                signals.append(f"company_in_domain:{company}")

        # --- Signal 2: Company name in subject/body (25 pts) ---
        if company and len(company) > 2:
            if company in subject:
                score += 25
                signals.append(f"company_in_subject:{company}")
            elif company in body[:2000]:
                score += 15
                signals.append(f"company_in_body:{company}")

        url_company = _extract_company_from_url(app_url)
        # Skip if Signal 2 already fired from direct company name match (avoid double-count).
        # But DO check when company slug != human name: "grafanalabs" vs "Grafana Labs".
        _sig2_fired = company and len(company) > 2 and company in subject
        if url_company and len(url_company) > 2 and not _sig2_fired:
            # Also try slug comparison: "grafanalabs" matches "Grafana Labs" in subject
            if url_company in subject or _slug(url_company) in _slug(subject):
                score += 25
                signals.append(f"url_company_in_subject:{url_company}")

        # --- Signal 3: Job title keyword overlap (20 pts) ---
        title_kw = _title_keywords(title)
        if title_kw:
            subject_words = set(re.findall(r"[a-z]+", subject))
            overlap = title_kw & subject_words
            if len(overlap) >= 2:
                score += 20
                signals.append(f"title_overlap:{','.join(overlap)}")
            elif len(overlap) == 1:
                score += 10
                signals.append(f"title_partial:{','.join(overlap)}")

        # --- Signal 4: ATS sender pattern (10 pts) ---
        is_ats = any(ats in sender_domain for ats in ATS_SENDER_PATTERNS) or sender_local in ATS_SENDER_PREFIXES
        if is_ats:
            score += 10
            signals.append("ats_sender")

        # --- Signal 5: Temporal proximity (5 pts) ---
        if email_dt and job.get("applied_at"):
            try:
                applied_dt = datetime.fromisoformat(job["applied_at"].replace("Z", "+00:00"))
                delta_days = abs((email_dt - applied_dt).days)
                if delta_days <= 30:
                    score += 5
                    signals.append(f"temporal:{delta_days}d")
            except (ValueError, TypeError):
                pass

        # --- Signal 6: Company extracted from subject matches job company (35 pts) ---
        # Fixes ATS relay emails (Greenhouse, Lever) where sender domain ≠ company domain.
        # "Thank you for applying to Honor" → subject_company="honor" matches job.company="honor".
        # Also uses slug comparison: "grafanalabs" (URL slug) == "Grafana Labs" (subject).
        if subject_company_norm and company and len(subject_company_norm) > 2:
            job_company_norm = normalize_company(company)
            subj_slug = _slug(subject_company_norm)
            job_slug = _slug(job_company_norm) if job_company_norm else ""
            if job_company_norm and (
                subject_company_norm == job_company_norm
                or subject_company_norm in job_company_norm
                or job_company_norm in subject_company_norm
                or (
                        subj_slug and job_slug and (
                        subj_slug == job_slug or subj_slug in job_slug or job_slug in subj_slug)
                )
            ):
                score += 35
                signals.append(f"subject_company:{subject_company_raw}")

        # Track best match
        if score >= 40 and score > best_score:
            best_score = score
            best_match = {
                "job_url": job_url,
                "score": score,
                "signals": signals,
            }

    if best_match:
        log.debug(
            "Best match: %s (score: %d, signals: %s)",
            best_match["job_url"][:60],
            best_match["score"],
            ", ".join(best_match["signals"]),
        )
    return best_match
