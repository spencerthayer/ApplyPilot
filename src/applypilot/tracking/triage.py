"""Pure-Python email triage to minimize LLM usage.

Pattern-matches on sender domain, subject, and snippet to auto-classify
~70-80% of emails without an LLM call. Priority order (first match wins):

1. LLM-required: interview, offer, assessment keywords → always LLM
2. Noise: job alerts, newsletters, known non-application senders → skip
3. Auto-confirm: ATS sender + "thank you for applying" patterns → confirmation
4. Auto-reject: "unfortunately" / "not moving forward" patterns → rejection
5. Ambiguous: → send to LLM
"""

import re
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# --- Priority 1: Always send to LLM (need dates/people/action extraction) ---
LLM_REQUIRED_SUBJECT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\binterview\b",
        r"\bschedul(?:e|ed|ing)\b",
        r"\bnext\s+steps?\b",
        r"\boffer\b",
        r"\bassessment\b",
        r"\bcoding\s+challenge\b",
        r"\btechnical\s+screen\b",
        r"\bphone\s+screen\b",
        r"\bbackground\s+check\b",
        r"\bonboarding\b",
        r"\bcompensation\b",
        r"\bsalary\b",
    ]
]

LLM_REQUIRED_SNIPPET_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bschedule\s+(?:a|an|your)\s+interview\b",
        r"\binvit(?:e|ed|ing)\s+you\s+to\b",
        r"\bnext\s+steps?\b",
        r"\boffer\s+letter\b",
        r"\bcoding\s+(?:challenge|assessment|test)\b",
        r"\btake[\s-]home\b",
    ]
]

# --- Priority 2: Noise — skip entirely ---
NOISE_SENDER_DOMAINS = {
    "linkedin.com",
    "linotify.com",  # LinkedIn alerts
    "indeed.com",  # Indeed alerts
    "glassdoor.com",  # Glassdoor
    "ziprecruiter.com",  # ZipRecruiter alerts
    "dice.com",  # Dice alerts
    "monster.com",
    "simplyapply.com",
    "careerbuilder.com",
    "getpocket.com",
    "substack.com",
    "medium.com",
}

NOISE_SUBJECT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bjob\s+alert",
        r"\bnew\s+jobs?\s+(?:for|matching|near)\b",
        r"\bnewsletter\b",
        r"\bunsubscribe\b",
        r"\bdigest\b",
        r"\bweekly\s+(?:update|recap|summary)\b",
        r"\brecommended\s+jobs?\b",
        r"\bjobs?\s+you\s+might\s+like\b",
        r"\bsimilar\s+jobs?\b",
        r"\bprofile\s+view",
        r"\bwho\s+viewed\s+your\b",
        r"\bconnection\s+request\b",
        r"\bendorsed\b",
    ]
]

# --- Priority 3: Auto-confirm patterns ---
# Known ATS notification sender domains
ATS_SENDER_DOMAINS = {
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
    "applytojob.com",
    "hire.lever.co",
}

ATS_SENDER_PREFIXES = {"noreply", "no-reply", "notifications", "careers", "jobs", "talent", "recruiting"}

CONFIRM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"thank\s+you\s+for\s+(?:your\s+)?appl(?:ying|ication)",
        r"application\s+(?:received|submitted|confirmed)",
        r"we\s+(?:have\s+)?received\s+your\s+application",
        r"successfully\s+(?:submitted|applied)",
        r"your\s+application\s+(?:has\s+been\s+)?(?:received|submitted)",
        r"thanks?\s+for\s+(?:your\s+)?interest",
        r"we(?:'ve|'ll|\s+will)\s+review\s+your\s+(?:application|resume|profile)",
    ]
]

# --- Priority 4: Auto-reject patterns ---
REJECT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bunfortunately\b",
        r"\bnot\s+(?:be\s+)?moving\s+forward\b",
        r"\bposition\s+(?:has\s+been\s+)?filled\b",
        r"\bdecided\s+(?:not\s+)?to\s+(?:move|proceed)\s+(?:forward\s+)?with\s+other\b",
        r"\bwill\s+not\s+be\s+(?:moving|proceeding)\b",
        r"\bpursuing\s+other\s+candidates\b",
        r"\bnot\s+(?:a\s+)?(?:good\s+)?(?:fit|match)\b",
        r"\bafter\s+careful\s+(?:consideration|review)\b.*\bnot\b",
        r"\bregret\s+to\s+inform\b",
        r"\bunable\s+to\s+offer\b",
        r"\bwe\s+(?:have\s+)?decided\s+to\s+go\s+(?:with|in)\s+(?:a\s+)?(?:another|different)\b",
    ]
]


@dataclass
class TriageResult:
    """Result of pure-Python email triage."""

    classification: str  # confirmation, rejection, noise, or "llm_needed"
    confidence: float
    summary: str = ""
    reason: str = ""  # Why this classification was chosen
    people: list = field(default_factory=list)
    dates: list = field(default_factory=list)
    action_items: list = field(default_factory=list)

    def to_classifier_dict(self) -> dict:
        """Convert to the same format as classifier.py output."""
        return {
            "classification": self.classification,
            "confidence": self.confidence,
            "summary": self.summary,
            "people": self.people,
            "dates": self.dates,
            "action_items": self.action_items,
        }


@dataclass
class TriageStats:
    """Aggregate triage statistics for logging."""

    total: int = 0
    auto_confirmed: int = 0
    auto_rejected: int = 0
    noise: int = 0
    llm_needed: int = 0

    @property
    def auto_classified(self) -> int:
        return self.auto_confirmed + self.auto_rejected

    @property
    def savings_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.auto_classified + self.noise) / self.total * 100

    def summary(self) -> str:
        return (
            f"Triage: {self.auto_classified} auto-classified, "
            f"{self.noise} noise, {self.llm_needed} need LLM "
            f"({self.savings_pct:.0f}% savings)"
        )


def _sender_domain(sender: str) -> str:
    """Extract domain from email address."""
    if "@" in sender:
        return sender.split("@")[-1].strip().lower()
    return sender.strip().lower()


def _is_ats_sender(sender: str) -> bool:
    """Check if sender is a known ATS notification address."""
    domain = _sender_domain(sender)
    local = sender.split("@")[0].lower() if "@" in sender else ""
    return any(ats in domain for ats in ATS_SENDER_DOMAINS) or local in ATS_SENDER_PREFIXES


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    """Check if text matches any of the compiled patterns."""
    return any(p.search(text) for p in patterns)


def triage_email(email: dict) -> TriageResult:
    """Triage a single email using pure pattern matching.

    Args:
        email: Normalized email dict with subject, sender, snippet, etc.
               Does NOT need body — works on metadata only.

    Returns:
        TriageResult with classification or "llm_needed" if ambiguous.
    """
    subject = email.get("subject", "")
    sender = email.get("sender", "")
    snippet = email.get("snippet", "")
    sender_domain = _sender_domain(sender)

    # Combine subject + snippet for pattern matching
    text = f"{subject} {snippet}"

    # --- Priority 1: LLM-required (interviews, offers, assessments) ---
    if _matches_any(subject, LLM_REQUIRED_SUBJECT_PATTERNS):
        return TriageResult(
            classification="llm_needed",
            confidence=0.0,
            reason="subject matches LLM-required pattern",
        )
    if _matches_any(snippet, LLM_REQUIRED_SNIPPET_PATTERNS):
        return TriageResult(
            classification="llm_needed",
            confidence=0.0,
            reason="snippet matches LLM-required pattern",
        )

    # --- Priority 2: Noise ---
    if sender_domain in NOISE_SENDER_DOMAINS:
        return TriageResult(
            classification="noise",
            confidence=0.95,
            reason=f"noise sender domain: {sender_domain}",
        )
    if _matches_any(subject, NOISE_SUBJECT_PATTERNS):
        return TriageResult(
            classification="noise",
            confidence=0.90,
            reason="noise subject pattern",
        )

    # --- Priority 3: Auto-confirm (ATS + confirmation language) ---
    if _matches_any(text, CONFIRM_PATTERNS):
        conf = 0.95 if _is_ats_sender(sender) else 0.85
        return TriageResult(
            classification="confirmation",
            confidence=conf,
            summary=f"Application confirmation from {sender_domain}",
            reason="confirmation pattern match" + (" + ATS sender" if conf == 0.95 else ""),
        )

    # --- Priority 4: Auto-reject ---
    if _matches_any(text, REJECT_PATTERNS):
        conf = 0.92 if _is_ats_sender(sender) else 0.82
        return TriageResult(
            classification="rejection",
            confidence=conf,
            summary=f"Rejection from {sender_domain}",
            reason="rejection pattern match" + (" + ATS sender" if conf == 0.92 else ""),
        )

    # --- Priority 5: Ambiguous → LLM ---
    return TriageResult(
        classification="llm_needed",
        confidence=0.0,
        reason="no pattern matched",
    )


def triage_batch(emails: list[dict]) -> tuple[list[tuple[dict, TriageResult]], TriageStats]:
    """Triage a batch of emails, returning results and stats.

    Returns:
        (results, stats) where results is a list of (email, triage_result) tuples.
    """
    stats = TriageStats(total=len(emails))
    results = []

    for email in emails:
        result = triage_email(email)
        results.append((email, result))

        if result.classification == "noise":
            stats.noise += 1
        elif result.classification == "confirmation":
            stats.auto_confirmed += 1
        elif result.classification == "rejection":
            stats.auto_rejected += 1
        elif result.classification == "llm_needed":
            stats.llm_needed += 1

    log.info(stats.summary())
    return results, stats
