"""Job fit scoring with deterministic baseline + calibrated LLM reranking."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from rich.console import Console

from applypilot.config import RESUME_JSON_PATH, RESUME_PATH, load_profile, load_resume_text
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client
from applypilot.resume_json import get_profile_skill_keywords

log = logging.getLogger(__name__)
MAX_SCORE_ATTEMPTS_PER_JOB = 3
SCORE_ATTEMPT_BACKOFF_SECONDS = 1.0
_LEGACY_SCORE_ERROR_PATTERN = "%LLM error:%"
_MODEL_RESPONSE_SNIPPET_LIMIT = 320
_SCORE_TRACE_ENABLED = os.environ.get("APPLYPILOT_SCORE_TRACE", "").strip().lower() in {"1", "true", "yes", "on"}
_TRACE_CONSOLE = Console(stderr=True, highlight=False, soft_wrap=True)
_SHORT_REASON_WORD_RE = re.compile(r"[A-Za-z0-9+#./'-]+")


SCORE_PROMPT = """You are a job-fit scoring calibrator.

You will receive:
1) Candidate resume profile and resume text.
2) Job posting context focused on requirements and responsibilities.
3) Deterministic baseline signals from an offline scorer.

Your job:
- Re-evaluate fit quality and provide a calibrated score.
- Respect evidence in requirements over generic title matching.
- Keep reasoning concise and grounded in the provided content.

Return JSON ONLY with this schema:
{
  "score": 1-10 integer,
  "confidence": 0.0-1.0 number,
  "why_short": "3-9 word summary",
  "matched_skills": ["..."],
  "missing_requirements": ["..."],
  "reasoning": "full rationale with key evidence"
}
"""


SCORING_RESPONSE_FORMAT = {"type": "json_object"}


_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "of",
    "the",
    "to",
    "with",
    "at",
    "ii",
    "iii",
    "iv",
    "sr",
    "senior",
    "principal",
    "staff",
    "lead",
    "l4",
    "l5",
    "l6",
    "l7",
}

_JOB_CONTEXT_PRIORITIES: list[tuple[int, tuple[str, ...]]] = [
    (4, ("requirements", "minimum qualifications", "must have", "qualifications")),
    (3, ("preferred qualifications", "nice to have", "preferred", "bonus points")),
    (3, ("responsibilities", "what you'll do", "what you will do", "day to day")),
    (2, ("about the role", "role overview", "about this role")),
]

_ROLE_FAMILY_PATTERNS: dict[str, tuple[str, ...]] = {
    "software_engineering": (
        r"\bsoftware\b",
        r"\bengineer\b",
        r"\bdeveloper\b",
        r"\bbackend\b",
        r"\bfront[\s-]?end\b",
        r"\bfull[\s-]?stack\b",
        r"\bplatform\b",
        r"\bdevops\b",
        r"\bsre\b",
    ),
    "data_ai": (
        r"\bdata\b",
        r"\bmachine learning\b",
        r"\bml\b",
        r"\bai\b",
        r"\bllm\b",
        r"\bresearch engineer\b",
        r"\bapplied scientist\b",
    ),
    "design": (
        r"\bdesigner\b",
        r"\bux\b",
        r"\bui\b",
        r"\bproduct design\b",
        r"\bvisual design\b",
    ),
    "marketing": (
        r"\bmarketing\b",
        r"\baudience\b",
        r"\bdemand gen\b",
        r"\bseo\b",
        r"\bcontent strategy\b",
    ),
    "sales": (
        r"\bsales\b",
        r"\baccount executive\b",
        r"\bbusiness development\b",
        r"\bsdr\b",
    ),
    "operations": (
        r"\boperations\b",
        r"\bprogram manager\b",
        r"\bproject manager\b",
    ),
    "finance": (
        r"\bfinance\b",
        r"\baccounting\b",
        r"\bcpa\b",
        r"\bcontroller\b",
    ),
}

_SENIORITY_PATTERNS: list[tuple[int, tuple[str, ...]]] = [
    (0, ("intern", "internship")),
    (1, ("junior", "jr", "entry", "new grad", "graduate")),
    (2, ("engineer", "developer", "analyst", "specialist", "mid", "associate")),
    (3, ("senior", "sr", "lead")),
    (4, ("staff", "principal", "architect")),
    (5, ("manager", "head of", "director", "vp", "vice president")),
]

_HARD_MISMATCH_TERMS = (
    "clearance",
    "active license",
    "board certification",
    "bar admission",
    "registered nurse",
    "rn license",
    "medical doctor",
    "cpa required",
    "citizenship required",
)

_SKILL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python", re.compile(r"\bpython\b", re.IGNORECASE)),
    ("java", re.compile(r"\bjava\b", re.IGNORECASE)),
    ("javascript", re.compile(r"\bjavascript\b|\bnode\.?js\b", re.IGNORECASE)),
    ("typescript", re.compile(r"\btypescript\b", re.IGNORECASE)),
    ("c#", re.compile(r"\bc#\b|\bcsharp\b|\b\.net\b|\basp\.?net\b", re.IGNORECASE)),
    ("c++", re.compile(r"\bc\+\+\b", re.IGNORECASE)),
    ("go", re.compile(r"\bgolang\b", re.IGNORECASE)),
    ("rust", re.compile(r"\brust\b", re.IGNORECASE)),
    ("ruby", re.compile(r"\bruby\b", re.IGNORECASE)),
    ("php", re.compile(r"\bphp\b", re.IGNORECASE)),
    ("scala", re.compile(r"\bscala\b", re.IGNORECASE)),
    ("kotlin", re.compile(r"\bkotlin\b", re.IGNORECASE)),
    ("swift", re.compile(r"\bswift\b", re.IGNORECASE)),
    ("react", re.compile(r"\breact\b", re.IGNORECASE)),
    ("angular", re.compile(r"\bangular\b", re.IGNORECASE)),
    ("vue", re.compile(r"\bvue(?:\.js)?\b", re.IGNORECASE)),
    ("next.js", re.compile(r"\bnext\.?js\b", re.IGNORECASE)),
    ("django", re.compile(r"\bdjango\b", re.IGNORECASE)),
    ("flask", re.compile(r"\bflask\b", re.IGNORECASE)),
    ("fastapi", re.compile(r"\bfastapi\b", re.IGNORECASE)),
    ("spring", re.compile(r"\bspring\b", re.IGNORECASE)),
    ("rails", re.compile(r"\brails\b", re.IGNORECASE)),
    ("graphql", re.compile(r"\bgraphql\b", re.IGNORECASE)),
    ("rest api", re.compile(r"\brest(?:ful)?\b|\bapi\b", re.IGNORECASE)),
    ("microservices", re.compile(r"\bmicroservices?\b", re.IGNORECASE)),
    ("sql", re.compile(r"\bsql\b|\bpostgres\b|\bmysql\b", re.IGNORECASE)),
    ("nosql", re.compile(r"\bnosql\b|\bmongodb\b|\bredis\b|\bcassandra\b", re.IGNORECASE)),
    ("aws", re.compile(r"\baws\b|\bamazon web services\b", re.IGNORECASE)),
    ("gcp", re.compile(r"\bgcp\b|\bgoogle cloud\b", re.IGNORECASE)),
    ("azure", re.compile(r"\bazure\b", re.IGNORECASE)),
    ("docker", re.compile(r"\bdocker\b", re.IGNORECASE)),
    ("kubernetes", re.compile(r"\bkubernetes\b|\bk8s\b", re.IGNORECASE)),
    ("terraform", re.compile(r"\bterraform\b", re.IGNORECASE)),
    ("ci/cd", re.compile(r"\bci/?cd\b|\bjenkins\b|\bgithub actions\b", re.IGNORECASE)),
    ("spark", re.compile(r"\bspark\b", re.IGNORECASE)),
    ("hadoop", re.compile(r"\bhadoop\b", re.IGNORECASE)),
    ("airflow", re.compile(r"\bairflow\b", re.IGNORECASE)),
    ("machine learning", re.compile(r"\bmachine learning\b|\bml\b", re.IGNORECASE)),
    ("deep learning", re.compile(r"\bdeep learning\b", re.IGNORECASE)),
    ("tensorflow", re.compile(r"\btensorflow\b", re.IGNORECASE)),
    ("pytorch", re.compile(r"\bpytorch\b", re.IGNORECASE)),
    ("llm", re.compile(r"\bllm\b|\blarge language model\b|\bgenerative ai\b", re.IGNORECASE)),
    ("nlp", re.compile(r"\bnlp\b|\bnatural language processing\b", re.IGNORECASE)),
]


class ScoreResponseParseError(ValueError):
    """Raised when LLM score response does not satisfy the scoring JSON schema."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


# ── Deterministic Exclusion Gate ──────────────────────────────────────────
# Hardcoded exclusion rules aligned with task-8 contract semantics.
# Future: load from config/rules.yaml per the contract schema.

EXCLUSION_RULES: list[dict] = [
    {
        "id": "r-001",
        "type": "keyword",
        "value": ["intern", "internship"],
        "match_scope": "title",
        "match_type": "exact",
        "reason_code": "excluded_keyword",
        "description": "Exclude internship positions",
    },
    {
        "id": "r-002",
        "type": "keyword",
        "value": ["clearance"],
        "match_scope": "title+description",
        "match_type": "exact",
        "reason_code": "excluded_keyword",
        "description": "Exclude positions requiring security clearance",
    },
]


def _load_user_exclusion_rules() -> list[dict]:
    """Load exclude_titles from searches.yaml and convert to exclusion rules.

    ADDED: The hardcoded EXCLUSION_RULES only had 2 entries (intern, clearance).
    The user's searches.yaml has a richer exclude_titles list (VP, director, etc.)
    that was only applied during JobSpy discovery — not during scoring. This means
    Greenhouse/Workday jobs with excluded titles still got scored by the LLM,
    wasting ~$0.04/job on Opus. Now they're excluded deterministically.
    """
    try:
        from applypilot.config import load_search_config
        cfg = load_search_config()
        titles = cfg.get("exclude_titles", [])
        if not titles:
            return []
        return [{
            "id": "r-user-exclude",
            "type": "keyword",
            "value": [t.strip().lower() for t in titles if t.strip()],
            "match_scope": "title",
            "match_type": "substring",
            "reason_code": "excluded_title",
            "description": "User exclude_titles from searches.yaml",
        }]
    except Exception:
        return []


def _tokenize(text: str) -> list[str]:
    """Tokenize text on non-alphanumeric boundaries, lowercased."""

    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _tokenize_set(text: str) -> set[str]:
    return {token for token in _tokenize(text) if token and token not in _TITLE_STOPWORDS}


def _title_key(title: str) -> str:
    tokens = [token for token in _tokenize(title) if token and token not in _TITLE_STOPWORDS]
    if not tokens:
        return "untitled"
    return " ".join(tokens[:8])


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _safe_response_snippet(text: str, limit: int = _MODEL_RESPONSE_SNIPPET_LIMIT) -> str:
    snippet = (text or "").replace("\n", "\\n")
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 3] + "..."


def _truncate_piece(text: str, limit: int = 28) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _compact_values(values: list[str], limit: int = 3, item_limit: int = 28) -> str:
    items = [item.strip() for item in values if item and item.strip()]
    if not items:
        return "-"
    shown = [_truncate_piece(item, item_limit) for item in items[:limit]]
    remainder = len(items) - len(shown)
    if remainder > 0:
        return f"{', '.join(shown)}, +{remainder}"
    return ", ".join(shown)


def _compact_reasoning(text: str, limit: int = 110) -> str:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return "-"
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _normalize_short_reason(text: str) -> str:
    words = _SHORT_REASON_WORD_RE.findall((text or "").strip())
    if len(words) < 3:
        return ""
    if len(words) > 9:
        words = words[:9]
    return " ".join(words)


def _derive_short_reason(reasoning: str) -> str:
    text = re.sub(r"\s+", " ", (reasoning or "")).strip()
    if not text:
        return "Mixed fit with notable gaps"
    first_sentence = re.split(r"[.!?]\s+", text, maxsplit=1)[0].strip()
    candidate = first_sentence or text
    normalized = _normalize_short_reason(candidate)
    if normalized:
        return normalized

    lowered = text.lower()
    if any(token in lowered for token in ("strong fit", "excellent", "high fit", "good fit")):
        return "Strong fit with clear overlap"
    if any(token in lowered for token in ("poor fit", "weak fit", "mismatch", "not a fit")):
        return "Weak fit with major gaps"
    if any(token in lowered for token in ("moderate fit", "mixed fit", "partial fit")):
        return "Moderate fit with notable gaps"
    return "Mixed fit with notable gaps"


def _log_file_only(level: int, message: str, *args) -> None:
    """Write a log record only to file handlers attached to the root logger."""

    root_logger = logging.getLogger()
    file_handlers = [handler for handler in root_logger.handlers if isinstance(handler, logging.FileHandler)]
    if not file_handlers:
        log.log(level, message, *args)
        return

    record = log.makeRecord(
        log.name,
        level,
        __file__,
        0,
        message,
        args,
        exc_info=None,
        func="_log_file_only",
        extra=None,
    )
    for handler in file_handlers:
        if level >= handler.level:
            handler.handle(record)


def _outcome_markers(outcome: str) -> tuple[str, str]:
    if outcome == "excluded":
        return " [EXCLUDED]", " [yellow][EXCLUDED][/yellow]"
    if outcome == "llm_failed":
        return " [LLM_FAILED]", " [red][LLM_FAILED][/red]"
    return "", ""


def _score_color(score: int, outcome: str) -> str:
    if outcome == "excluded":
        return "yellow"
    if outcome == "llm_failed":
        return "red"
    if score >= 7:
        return "green"
    if score >= 4:
        return "yellow"
    return "red"


def _emit_job_block_header(
    completed: int,
    total: int,
    score: int,
    title: str,
    outcome: str,
) -> None:
    marker_plain, marker_rich = _outcome_markers(outcome)
    _TRACE_CONSOLE.print(f"[bold cyan][{completed}/{total}][/bold cyan] {title}{marker_rich}")
    _TRACE_CONSOLE.print(
        f"          [bright_black]└─[/bright_black] [bold]score[/bold] = "
        f"[bold {_score_color(score, outcome)}]{score}[/bold {_score_color(score, outcome)}]"
    )
    _log_file_only(logging.INFO, "[%d/%d] %s%s", completed, total, title, marker_plain)
    _log_file_only(logging.INFO, "          └─ score = %d", score)


def _emit_score_trace(result: dict) -> None:
    outcome = str(result.get("outcome") or "")
    prefix = "          [bright_black]└─[/bright_black] "

    if outcome == "excluded":
        reason = _compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        _TRACE_CONSOLE.print(f"{prefix}[yellow]excluded[/yellow] [dim]{reason}[/dim]")
        return

    if outcome == "llm_failed":
        category = _truncate_piece(str(result.get("parse_error_category") or "unknown"), limit=24)
        baseline = result.get("baseline_score")
        error = _compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        _TRACE_CONSOLE.print(
            f"{prefix}[red]failed[/red] [bold]cat[/bold]=[red]{category}[/red] "
            f"[bold]b[/bold]=[yellow]{baseline if baseline is not None else '-'}[/yellow]"
        )
        _TRACE_CONSOLE.print(f"{prefix}[bright_black]why[/bright_black] [dim]{error}[/dim]")
        return

    baseline = result.get("baseline_score")
    llm_score = result.get("llm_score")
    confidence = result.get("llm_confidence")
    delta = result.get("score_delta")
    matched = _compact_values(_coerce_list(result.get("matched_skills")), limit=3, item_limit=22)
    missing = _compact_values(_coerce_list(result.get("missing_requirements")), limit=2, item_limit=28)
    full_reasoning = str(result.get("llm_reasoning_full") or result.get("reasoning") or "")
    why_short = _normalize_short_reason(str(result.get("llm_why_short") or "").strip()) if result.get("llm_why_short") else _derive_short_reason(full_reasoning)
    confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
    delta_value = int(delta) if isinstance(delta, int) else 0
    delta_style = "green" if delta_value > 0 else ("red" if delta_value < 0 else "yellow")
    delta_text = f"{delta_value:+d}" if isinstance(delta, int) else "-"

    _TRACE_CONSOLE.print(
        f"{prefix}[cyan]trace[/cyan] "
        f"[bold]b[/bold]=[yellow]{baseline if baseline is not None else '-'}[/yellow] "
        f"[bold]l[/bold]=[magenta]{llm_score if llm_score is not None else '-'}[/magenta] "
        f"[bold]c[/bold]=[blue]{confidence_text}[/blue] "
        f"[bold]Δ[/bold]=[{delta_style}]{delta_text}[/{delta_style}] "
        f"[bold]m[/bold]=[green]{matched}[/green] "
        f"[bold]x[/bold]=[red]{missing}[/red]"
    )
    _TRACE_CONSOLE.print(f"{prefix}[bright_black]why[/bright_black] [cyan]{why_short}[/cyan]")
    if full_reasoning and full_reasoning.strip():
        _TRACE_CONSOLE.print(f"{prefix}[bright_black]reasoning[/bright_black] [dim]{full_reasoning.strip()}[/dim]")


def _log_score_trace(result: dict) -> None:
    outcome = str(result.get("outcome") or "")
    prefix = "          └─ "

    if outcome == "excluded":
        reason = _compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        _log_file_only(logging.INFO, "%sexcluded %s", prefix, reason)
        return

    if outcome == "llm_failed":
        category = _truncate_piece(str(result.get("parse_error_category") or "unknown"), limit=24)
        baseline = result.get("baseline_score")
        error = _compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        _log_file_only(logging.INFO, "%sfailed cat=%s b=%s", prefix, category, baseline if baseline is not None else "-")
        _log_file_only(logging.INFO, "%swhy %s", prefix, error)
        return

    baseline = result.get("baseline_score")
    llm_score = result.get("llm_score")
    confidence = result.get("llm_confidence")
    delta = result.get("score_delta")
    matched = _compact_values(_coerce_list(result.get("matched_skills")), limit=3, item_limit=22)
    missing = _compact_values(_coerce_list(result.get("missing_requirements")), limit=2, item_limit=28)
    full_reasoning = str(result.get("llm_reasoning_full") or result.get("reasoning") or "")
    why_short = (
        _normalize_short_reason(str(result.get("llm_why_short") or "").strip())
        if result.get("llm_why_short")
        else _derive_short_reason(full_reasoning)
    )
    confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
    delta_text = f"{int(delta):+d}" if isinstance(delta, int) else "-"

    _log_file_only(
        logging.INFO,
        "%strace b=%s l=%s c=%s Δ=%s m=%s x=%s",
        prefix,
        baseline if baseline is not None else "-",
        llm_score if llm_score is not None else "-",
        confidence_text,
        delta_text,
        matched,
        missing,
    )
    _log_file_only(logging.INFO, "%swhy %s", prefix, why_short)
    reasoning_text = re.sub(r"\s+", " ", full_reasoning).strip()
    if reasoning_text:
        _log_file_only(logging.INFO, "%sreasoning %s", prefix, reasoning_text)


def _contains_phrase(text_lower: str, phrase: str) -> bool:
    candidate = phrase.lower().strip()
    if not candidate:
        return False
    if re.search(r"[+#./]", candidate):
        return candidate in text_lower
    pattern = r"\b" + re.escape(candidate).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, text_lower) is not None


def _extract_known_skills(text: str) -> set[str]:
    found: set[str] = set()
    for canonical, pattern in _SKILL_PATTERNS:
        if pattern.search(text):
            found.add(canonical)
    return found


def _infer_role_family(text: str) -> str:
    haystack = (text or "").lower()
    for family, patterns in _ROLE_FAMILY_PATTERNS.items():
        if any(re.search(pattern, haystack) for pattern in patterns):
            return family
    return "unknown"


def _seniority_from_text(text: str) -> int:
    lowered = (text or "").lower()
    for score, terms in reversed(_SENIORITY_PATTERNS):
        if any(term in lowered for term in terms):
            return score
    return 2


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _extract_requirement_focused_text(description: str, max_chars: int = 6000) -> str:
    """Prefer requirements/qualifications/responsibilities when truncating long JDs."""

    cleaned = (description or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned

    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    if not blocks:
        return cleaned[:max_chars]

    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(blocks):
        lowered = block.lower()
        score = 1 if index == 0 else 0
        for weight, terms in _JOB_CONTEXT_PRIORITIES:
            if any(term in lowered for term in terms):
                score += weight
        if re.search(r"\b(required|must|minimum|experience|skills?)\b", lowered):
            score += 1
        if block.count("\n-") + block.count("\n*") > 2:
            score += 1
        scored.append((score, index, block))

    selected_indexes: list[int] = []
    total = 0
    for _, index, block in sorted(scored, key=lambda item: (-item[0], item[1])):
        projected = total + len(block) + 2
        if projected > max_chars and selected_indexes:
            continue
        selected_indexes.append(index)
        total = projected
        if total >= max_chars:
            break

    selected_indexes = sorted(set(selected_indexes))
    sections = [blocks[index] for index in selected_indexes]
    focused = "\n\n".join(sections).strip()
    if len(focused) <= max_chars:
        return focused
    return focused[:max_chars]


def _build_scoring_profile(profile: dict) -> dict:
    experience = profile.get("experience", {}) if isinstance(profile.get("experience"), dict) else {}
    target_role = _coerce_text(experience.get("target_role"))
    years_total = _to_float(experience.get("years_of_experience_total")) or 0.0
    work_entries = profile.get("work", []) if isinstance(profile.get("work"), list) else []

    current_titles: list[str] = []
    for item in work_entries[:4]:
        if not isinstance(item, dict):
            continue
        title = _coerce_text(item.get("position"))
        if title and title not in current_titles:
            current_titles.append(title)

    profile_skills = [skill.lower() for skill in get_profile_skill_keywords(profile)]
    for item in work_entries:
        if not isinstance(item, dict):
            continue
        for tech in _coerce_list(item.get("technologies")):
            lower = tech.lower()
            if lower and lower not in profile_skills:
                profile_skills.append(lower)

    profile_known_skills = set()
    for skill in profile_skills:
        matched = _extract_known_skills(skill)
        if matched:
            profile_known_skills.update(matched)
        elif skill:
            profile_known_skills.add(skill)

    role_text = " ".join([target_role, *current_titles]).strip()
    role_family = _infer_role_family(role_text)
    seniority_from_titles = _seniority_from_text(role_text)
    seniority_from_years = 1
    if years_total >= 11:
        seniority_from_years = 4
    elif years_total >= 7:
        seniority_from_years = 3
    elif years_total >= 3:
        seniority_from_years = 2
    profile_seniority = max(seniority_from_titles, seniority_from_years)

    return {
        "target_role": target_role,
        "years_total": years_total,
        "current_titles": current_titles,
        "skills": profile_skills,
        "known_skills": sorted(profile_known_skills),
        "role_tokens": _tokenize_set(role_text),
        "role_family": role_family,
        "seniority": profile_seniority,
    }


def _load_scoring_profile() -> dict:
    try:
        profile = load_profile()
        return _build_scoring_profile(profile)
    except Exception as exc:
        log.warning("Falling back to minimal scoring profile because profile load failed: %s", exc)
        return {
            "target_role": "",
            "years_total": 0.0,
            "current_titles": [],
            "skills": [],
            "known_skills": [],
            "role_tokens": set(),
            "role_family": "unknown",
            "seniority": 2,
        }


def _compute_deterministic_baseline(scoring_profile: dict, job: dict) -> dict:
    title = _coerce_text(job.get("title"))
    description = _coerce_text(job.get("full_description") or job.get("description"))
    focused_description = _extract_requirement_focused_text(description, max_chars=7000)
    job_text = f"{title}\n{focused_description}".strip()
    job_text_lower = job_text.lower()

    title_tokens = _tokenize_set(title)
    role_tokens = scoring_profile.get("role_tokens", set()) or set()
    title_similarity = _jaccard_similarity(title_tokens, role_tokens)

    profile_known_skills = set(scoring_profile.get("known_skills") or [])
    job_known_skills = _extract_known_skills(job_text)
    matched_known_skills = sorted(job_known_skills & profile_known_skills)
    missing_requirements = sorted(job_known_skills - profile_known_skills)

    profile_custom_skills = [
        skill
        for skill in scoring_profile.get("skills", [])
        if skill not in profile_known_skills and len(skill) >= 3
    ]
    matched_custom_skills = sorted(
        skill for skill in profile_custom_skills if _contains_phrase(job_text_lower, skill)
    )

    if job_known_skills:
        skill_overlap = len(matched_known_skills) / max(1, len(job_known_skills))
    else:
        skill_overlap = min(1.0, len(matched_custom_skills) / 4.0)

    matched_skill_count = len(set(matched_known_skills + matched_custom_skills))
    missing_requirement_count = len(missing_requirements)
    if matched_skill_count + missing_requirement_count > 0:
        requirement_coverage = matched_skill_count / (matched_skill_count + missing_requirement_count)
    else:
        requirement_coverage = skill_overlap

    profile_family = _coerce_text(scoring_profile.get("role_family") or "unknown")
    job_family = _infer_role_family(job_text)
    domain_mismatch_penalty_raw = 0.0
    if profile_family != "unknown" and job_family != "unknown" and profile_family != job_family:
        domain_mismatch_penalty_raw = 2.0

    # Generic evidence score: avoids hardcoded role exceptions while rewarding demonstrable overlap.
    evidence_strength = max(
        0.0,
        min(
            1.0,
            0.45 * skill_overlap
            + 0.30 * min(1.0, matched_skill_count / 6.0)
            + 0.20 * requirement_coverage
            + 0.05 * title_similarity,
        ),
    )
    domain_penalty_scale = max(0.35, 1.0 - 0.70 * evidence_strength)
    domain_mismatch_penalty = domain_mismatch_penalty_raw * domain_penalty_scale

    profile_seniority = int(scoring_profile.get("seniority") or 2)
    job_seniority = _seniority_from_text(title)
    seniority_gap = job_seniority - profile_seniority
    if seniority_gap >= 3:
        seniority_component = -2.0
    elif seniority_gap == 2:
        seniority_component = -1.0
    elif seniority_gap <= -3:
        seniority_component = -0.5
    else:
        seniority_component = 0.8

    role_family_bonus = (
        1.0
        if profile_family == job_family and profile_family in {"software_engineering", "data_ai"}
        else 0.0
    )

    raw_score = (
        1.5
        + 3.0 * title_similarity
        + 4.0 * skill_overlap
        + seniority_component
        + role_family_bonus
        - domain_mismatch_penalty
    )
    if title_similarity >= 0.3 and skill_overlap >= 0.3:
        raw_score += 0.7
    if job_known_skills and not matched_known_skills:
        raw_score -= 0.6

    baseline_score = int(round(max(0.0, min(10.0, raw_score))))
    matched_skills = sorted(set(matched_known_skills + matched_custom_skills))[:12]
    is_software_engineering_role = job_family == "software_engineering"

    return {
        "score": baseline_score,
        "title_similarity": round(title_similarity, 3),
        "skill_overlap": round(skill_overlap, 3),
        "matched_skills": matched_skills,
        "missing_requirements": missing_requirements[:10],
        "matched_skill_count": matched_skill_count,
        "missing_requirement_count": missing_requirement_count,
        "requirement_coverage": round(requirement_coverage, 3),
        "evidence_strength": round(evidence_strength, 3),
        "seniority_gap": seniority_gap,
        "profile_role_family": profile_family,
        "job_role_family": job_family,
        "domain_mismatch_penalty": domain_mismatch_penalty,
        "is_software_engineering_role": is_software_engineering_role,
        "focused_description": focused_description,
        "normalized_title": _title_key(title),
    }


def _extract_json_object(text: str) -> dict:
    payload = (text or "").strip()
    if not payload:
        raise ScoreResponseParseError("empty_response", "LLM returned an empty response.")

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", payload, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        payload = fenced_match.group(1).strip()

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        object_match = re.search(r"\{.*\}", payload, re.DOTALL)
        if not object_match:
            raise ScoreResponseParseError("missing_json_object", "No JSON object found in model response.")
        try:
            parsed = json.loads(object_match.group(0))
        except json.JSONDecodeError as exc:
            raise ScoreResponseParseError(
                "invalid_json",
                f"Could not parse JSON object: {exc.msg} at line {exc.lineno}, column {exc.colno}",
            ) from exc

    if not isinstance(parsed, dict):
        raise ScoreResponseParseError("invalid_shape", "JSON response must be an object.")
    return parsed


def _parse_score_response(response: str) -> dict:
    """Parse and validate the strict scoring JSON schema."""

    data = _extract_json_object(response)
    if "score" not in data:
        raise ScoreResponseParseError("missing_score", "Response JSON did not include a 'score' field.")

    try:
        score = int(round(float(data["score"])))
    except (TypeError, ValueError) as exc:
        raise ScoreResponseParseError("invalid_score_type", "Score must be numeric.") from exc
    score = max(1, min(10, score))

    confidence_raw = data.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    matched_skills = _coerce_list(data.get("matched_skills"))
    missing_requirements = _coerce_list(data.get("missing_requirements"))
    reasoning = _coerce_text(data.get("reasoning")) or "No reasoning provided by model."
    why_short_raw = _coerce_text(data.get("why_short") or data.get("reasoning_short") or data.get("summary_short"))
    if why_short_raw:
        why_short = _normalize_short_reason(why_short_raw) or _derive_short_reason(reasoning)
    else:
        why_short = _derive_short_reason(reasoning)

    return {
        "score": score,
        "confidence": confidence,
        "why_short": why_short,
        "matched_skills": matched_skills[:12],
        "missing_requirements": missing_requirements[:12],
        "reasoning": reasoning,
    }


def _has_hard_mismatch_evidence(baseline: dict, missing_requirements: list[str], job_text: str) -> bool:
    if float(baseline.get("domain_mismatch_penalty") or 0.0) >= 3.0:
        return True
    evidence_blob = " ".join(missing_requirements + [job_text]).lower()
    return any(term in evidence_blob for term in _HARD_MISMATCH_TERMS)


def _apply_score_calibration(
    baseline: dict,
    llm_score: int,
    confidence: float,
    matched_skills: list[str],
    missing_requirements: list[str],
    job_context: str,
) -> tuple[int, int]:
    baseline_score = int(baseline.get("score", 0))
    bounded_llm_score = max(1, min(10, int(llm_score)))
    bounded_confidence = max(0.0, min(1.0, float(confidence)))
    matched_count = int(baseline.get("matched_skill_count") or len(matched_skills))
    missing_count = max(int(baseline.get("missing_requirement_count") or len(missing_requirements)), 0)
    skill_overlap = max(0.0, min(1.0, float(baseline.get("skill_overlap") or 0.0)))
    title_similarity = max(0.0, min(1.0, float(baseline.get("title_similarity") or 0.0)))
    if matched_count + missing_count > 0:
        requirement_coverage = matched_count / (matched_count + missing_count)
    else:
        requirement_coverage = skill_overlap

    max_delta = 2
    if bounded_confidence >= 0.85 and (len(matched_skills) >= 5 or len(missing_requirements) >= 4):
        max_delta = 3

    delta = bounded_llm_score - baseline_score
    delta = max(-max_delta, min(max_delta, delta))
    calibrated = max(1, min(10, baseline_score + delta))

    hard_mismatch = _has_hard_mismatch_evidence(baseline, missing_requirements, job_context)
    if hard_mismatch and bounded_confidence >= 0.8 and bounded_llm_score <= 2:
        calibrated = min(calibrated, 2)

    # Generic floor based on measurable overlap; no role-specific hardcoded buckets.
    evidence_score = max(
        max(0.0, min(1.0, float(baseline.get("evidence_strength") or 0.0))),
        max(
            0.0,
            min(
                1.0,
                0.45 * skill_overlap
                + 0.30 * min(1.0, matched_count / 6.0)
                + 0.20 * requirement_coverage
                + 0.05 * title_similarity,
            ),
        ),
    )
    if not hard_mismatch and evidence_score >= 0.35:
        dynamic_floor = max(3, min(5, int(round(1.0 + 5.0 * evidence_score))))
        calibrated = max(calibrated, dynamic_floor)

    return calibrated, calibrated - baseline_score


def _format_scoring_profile_for_prompt(scoring_profile: dict) -> str:
    return (
        f"Target role: {scoring_profile.get('target_role') or 'N/A'}\n"
        f"Years experience: {scoring_profile.get('years_total') or 0}\n"
        f"Recent titles: {', '.join(scoring_profile.get('current_titles') or []) or 'N/A'}\n"
        f"Skills: {', '.join((scoring_profile.get('known_skills') or [])[:40]) or 'N/A'}"
    )


def _exclusion_result(rule: dict, matched_value: str) -> dict:
    """Build a blocked scoring result for an excluded job."""

    reason_code = rule["reason_code"]
    log.debug("[score] EXCLUDED: rule=%s reason=%s matched='%s'", rule["id"], reason_code, matched_value)
    return {
        "score": 0,
        "keywords": "",
        "reasoning": f"EXCLUDED: {reason_code} — matched '{matched_value}' (rule {rule['id']})",
        "exclusion_reason_code": reason_code,
        "exclusion_rule_id": rule["id"],
    }


def _load_target_title_keywords() -> tuple[set[str], list[str]]:
    """Load target role phrases and keywords from searches.yaml queries.

    Returns (single_keywords, phrases). Generic words are derived from data:
    any word appearing in >50% of queries is too common to distinguish roles
    (e.g. "engineer" appears in 12/16 queries) and is excluded from keywords.
    Phrase matching still catches them (e.g. "software engineer" as a whole).
    """
    _EXPANSIONS = {
        "ml": "machine learning",
        "sde": "software development engineer",
        "sre": "site reliability engineer",
        "sdk": "software development kit",
    }

    try:
        from collections import Counter
        from applypilot.config import load_search_config
        cfg = load_search_config()
        queries = cfg.get("queries", [])
        if not queries:
            return set(), []

        phrases: list[str] = []
        # Count how many queries each word appears in
        word_freq: Counter = Counter()
        query_words: list[list[str]] = []

        for q in queries:
            phrase = q.get("query", "").strip().lower()
            if not phrase:
                continue
            phrases.append(phrase)
            for abbr, expansion in _EXPANSIONS.items():
                if abbr in phrase.split():
                    phrases.append(phrase.replace(abbr, expansion))
            words = [w for w in phrase.split() if len(w) >= 2]
            query_words.append(words)
            for w in set(words):
                word_freq[w] += 1

        # Words in >50% of queries are generic — they don't help distinguish roles
        threshold = len(queries) * 0.5
        keywords: set[str] = set()
        for words in query_words:
            for w in words:
                if word_freq[w] <= threshold:
                    keywords.add(w)

        return keywords, phrases
    except Exception:
        return set(), []


def evaluate_exclusion(job: dict) -> dict | None:
    """Evaluate deterministic exclusion rules against a job.

    Two-pass filter:
    1. Negative: exclude jobs matching exclude_titles (VP, intern, etc.)
    2. Positive: skip jobs with zero title overlap with user's search queries
    """

    title = job.get("title") or ""
    description = job.get("full_description") or job.get("description") or ""
    site = job.get("site") or ""

    title_tokens = _tokenize(title)
    desc_tokens = _tokenize(description)
    combined_tokens = title_tokens + desc_tokens

    # CHANGED: Merge hardcoded rules with user's exclude_titles from searches.yaml.
    # This ensures Greenhouse/Workday jobs with excluded titles (VP, director, etc.)
    # are skipped before the LLM call, saving token costs.
    all_rules = EXCLUSION_RULES + _load_user_exclusion_rules()

    for rule in all_rules:
        values = rule["value"]
        if isinstance(values, str):
            values = [values]

        match_scope = rule.get("match_scope", "title+description")
        match_type = rule.get("match_type", "exact")

        if match_scope == "site":
            field_lower = site.lower()
            for val in values:
                val_lower = val.lower()
                if match_type == "substring" and val_lower in field_lower:
                    return _exclusion_result(rule, val)
                if match_type == "exact" and val_lower == field_lower:
                    return _exclusion_result(rule, val)
            continue

        if match_scope == "title":
            tokens = title_tokens
        elif match_scope == "description":
            tokens = desc_tokens
        else:
            tokens = combined_tokens

        for val in values:
            val_lower = val.lower()
            if match_type == "exact":
                if val_lower in tokens:
                    return _exclusion_result(rule, val)
            elif match_type == "prefix":
                if any(token.startswith(val_lower) for token in tokens):
                    return _exclusion_result(rule, val)
            elif match_type == "substring":
                # ADDED: Word-boundary substring match for multi-word phrases
                # (e.g. "senior director", "vice president" from exclude_titles).
                # Uses \b to avoid "intern" matching "international".
                raw = title.lower() if match_scope == "title" else (
                    description.lower() if match_scope == "description" else f"{title} {description}".lower()
                )
                pattern = r"\b" + re.escape(val_lower).replace(r"\ ", r"\s+") + r"\b"
                if re.search(pattern, raw):
                    return _exclusion_result(rule, val)

    # Pass 2: Positive relevance check — skip jobs with zero title overlap
    # with user's target roles. Prevents wasting LLM tokens on jobs like
    # "Account Manager" when user searches for "Software Engineer".
    target_keywords, target_phrases = _load_target_title_keywords()
    if (target_keywords or target_phrases) and title:
        title_lower = title.lower()
        # Skip relevance check for jobs with missing/generic titles —
        # these may be valid jobs with bad metadata, let the LLM decide.
        if title_lower in ("", "unknown role", "unknown", "n/a", "none"):
            return None
        # Check phrase match first (e.g. "software engineer" in title)
        phrase_match = any(p in title_lower for p in target_phrases)
        if not phrase_match:
            # Fall back to distinctive keyword match (e.g. "android", "devops", "java")
            title_words = {w for w in _tokenize(title) if len(w) >= 3}
            keyword_match = bool(title_words & target_keywords)
            if not keyword_match:
                return _exclusion_result(
                    {"id": "r-relevance", "reason_code": "no_title_overlap",
                     "description": "Title has no overlap with target roles"},
                    f"title='{title[:40]}'",
                )

    return None


def score_job(resume_text: str, job: dict, scoring_profile: dict) -> dict:
    """Score a single job against the resume."""

    baseline = _compute_deterministic_baseline(scoring_profile, job)
    focused_description = baseline.get("focused_description", "")

    log.debug("[score] %s — baseline=%d title_sim=%.2f skill_overlap=%.2f matched=%s missing=%s",
              job.get("title", "?")[:50], baseline["score"],
              baseline["title_similarity"], baseline["skill_overlap"],
              baseline["matched_skills"][:5], baseline["missing_requirements"][:5])

    job_text = (
        f"TITLE: {job.get('title', '')}\n"
        f"COMPANY: {job.get('site', '')}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION (REQUIREMENT-FOCUSED):\n{focused_description}"
    )

    baseline_context = {
        "baseline_score": baseline["score"],
        "title_similarity": baseline["title_similarity"],
        "skill_overlap": baseline["skill_overlap"],
        "matched_skill_count": baseline.get("matched_skill_count"),
        "missing_requirement_count": baseline.get("missing_requirement_count"),
        "requirement_coverage": baseline.get("requirement_coverage"),
        "evidence_strength": baseline.get("evidence_strength"),
        "matched_skills": baseline["matched_skills"],
        "missing_requirements": baseline["missing_requirements"],
        "seniority_gap": baseline["seniority_gap"],
        "profile_role_family": baseline["profile_role_family"],
        "job_role_family": baseline["job_role_family"],
        "domain_mismatch_penalty": baseline["domain_mismatch_penalty"],
    }

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {
            "role": "user",
            "content": (
                f"RESUME PROFILE:\n{_format_scoring_profile_for_prompt(scoring_profile)}\n\n"
                f"RESUME TEXT:\n{resume_text[:12000]}\n\n---\n\n"
                f"JOB POSTING:\n{job_text}\n\n---\n\n"
                f"DETERMINISTIC BASELINE SIGNALS:\n{json.dumps(baseline_context, ensure_ascii=False)}"
            ),
        },
    ]

    client = get_client()
    final_failure: dict | None = None
    for attempt in range(1, MAX_SCORE_ATTEMPTS_PER_JOB + 1):
        raw_response = ""
        try:
            raw_response = client.chat(
                messages,
                max_output_tokens=768,
                temperature=0,
                response_format=SCORING_RESPONSE_FORMAT,
            )
            log.debug("[score] %s — LLM response: %s", job.get("title", "?")[:40], raw_response[:300])
            parsed = _parse_score_response(raw_response)
            log.debug("[score] %s — parsed: score=%s confidence=%s why=%s",
                      job.get("title", "?")[:40], parsed.get("score"), parsed.get("confidence"),
                      str(parsed.get("why_short", ""))[:80])

            llm_score = int(parsed["score"])
            llm_confidence = float(parsed["confidence"])
            matched_skills = parsed["matched_skills"] or baseline["matched_skills"]
            missing_requirements = parsed["missing_requirements"] or baseline["missing_requirements"]
            final_score, delta = _apply_score_calibration(
                baseline=baseline,
                llm_score=llm_score,
                confidence=llm_confidence,
                matched_skills=matched_skills,
                missing_requirements=missing_requirements,
                job_context=job_text,
            )

            return {
                "score": final_score,
                "keywords": ", ".join(matched_skills[:12]),
                "reasoning": (
                    f"Baseline={baseline['score']} LLM={llm_score} Confidence={llm_confidence:.2f} Delta={delta}. "
                    f"{parsed['reasoning']}"
                ),
                "llm_why_short": str(parsed["why_short"]),
                "llm_reasoning_full": str(parsed["reasoning"]),
                "matched_skills": matched_skills[:12],
                "missing_requirements": missing_requirements[:12],
                "baseline_score": baseline["score"],
                "llm_score": llm_score,
                "llm_confidence": round(llm_confidence, 3),
                "score_delta": delta,
                "normalized_title": baseline["normalized_title"],
            }
        except ScoreResponseParseError as exc:
            snippet = _safe_response_snippet(raw_response)
            final_failure = {
                "score": 0,
                "keywords": "",
                "reasoning": f"LLM parse error [{exc.category}]: {exc}. raw='{snippet}'",
                "parse_error_category": exc.category,
                "raw_response_snippet": snippet,
                "baseline_score": baseline["score"],
                "normalized_title": baseline["normalized_title"],
            }
        except Exception as exc:
            final_failure = {
                "score": 0,
                "keywords": "",
                "reasoning": f"LLM error: {exc}",
                "parse_error_category": "llm_request_error",
                "baseline_score": baseline["score"],
                "normalized_title": baseline["normalized_title"],
            }

        if attempt < MAX_SCORE_ATTEMPTS_PER_JOB:
            category = final_failure.get("parse_error_category", "unknown") if final_failure else "unknown"
            log.warning(
                "Retrying score for '%s' after %s (attempt %d/%d)",
                job.get("title", "?"),
                category,
                attempt + 1,
                MAX_SCORE_ATTEMPTS_PER_JOB,
            )
            time.sleep(SCORE_ATTEMPT_BACKOFF_SECONDS * attempt)

    return final_failure or {
        "score": 0,
        "keywords": "",
        "reasoning": "LLM error: unknown scoring failure",
        "parse_error_category": "unknown",
        "baseline_score": baseline["score"],
        "normalized_title": baseline["normalized_title"],
    }


def _compose_score_reasoning(result: dict) -> str:
    keywords = str(result.get("keywords") or "").strip()
    reasoning = str(result.get("reasoning") or "").strip()
    if keywords and reasoning:
        return f"{keywords}\n{reasoning}"
    return keywords or reasoning


def _normalize_llm_error(reasoning: str) -> str:
    text = (reasoning or "").strip()
    if not text:
        return "LLM error: unknown scoring failure"
    if text.lower().startswith("llm error:"):
        return text
    return f"LLM error: {text}"


def _next_score_retry_at_iso(current_retry_count: int) -> str:
    # Keep exponential spacing early, cap to daily retries for persistent failures.
    delay_minutes = min(5 * (4 ** min(current_retry_count, 8)), 24 * 60)
    next_retry = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
    return next_retry.isoformat()


def _classify_score_outcome(result: dict) -> str:
    if result.get("exclusion_reason_code"):
        return "excluded"
    try:
        score_value = int(result.get("score", 0))
    except (TypeError, ValueError):
        score_value = 0
    return "scored_success" if score_value > 0 else "llm_failed"


def _autoheal_legacy_llm_failures(conn) -> int:
    """Repair legacy rows where transient LLM failures were stored as fit_score=0."""

    rows = conn.execute(
        """
        SELECT url, score_reasoning, COALESCE(score_retry_count, 0) AS retry_count
        FROM jobs
        WHERE fit_score = 0
          AND COALESCE(exclusion_reason_code, '') = ''
          AND COALESCE(exclusion_rule_id, '') = ''
          AND COALESCE(score_reasoning, '') LIKE ?
        """,
        (_LEGACY_SCORE_ERROR_PATTERN,),
    ).fetchall()

    if not rows:
        return 0

    healed = 0
    for row in rows:
        url = row[0]
        score_reasoning = (row[1] or "").strip()
        retry_count = int(row[2] or 0)
        error_text = _normalize_llm_error(score_reasoning)

        next_retry_count = max(retry_count, 1)
        conn.execute(
            "UPDATE jobs SET fit_score = NULL, score_reasoning = NULL, scored_at = NULL, "
            "score_error = ?, score_retry_count = ?, score_next_retry_at = NULL, "
            "exclusion_reason_code = NULL, exclusion_rule_id = NULL, excluded_at = NULL "
            "WHERE url = ?",
            (error_text, next_retry_count, url),
        )
        healed += 1

    conn.commit()
    return healed


def _load_scoring_resume_text() -> str:
    """Load resume text while preserving canonical precedence and legacy test overrides."""

    if RESUME_JSON_PATH.exists():
        return load_resume_text()
    try:
        return load_resume_text(RESUME_PATH)
    except TypeError:
        return load_resume_text()


def _score_telemetry_summary(
    baseline_distribution: Counter,
    delta_distribution: Counter,
    parse_failures: Counter,
    title_scores: dict[str, list[int]],
) -> None:
    if baseline_distribution:
        log.info("Scoring telemetry: baseline_distribution=%s", dict(sorted(baseline_distribution.items(), reverse=True)))
    if delta_distribution:
        log.info("Scoring telemetry: llm_delta_distribution=%s", dict(sorted(delta_distribution.items())))
    if parse_failures:
        log.info("Scoring telemetry: parse_failures=%s", dict(sorted(parse_failures.items())))

    volatility_rows: list[tuple[str, int, int, int, int]] = []
    for title_key, scores in title_scores.items():
        if len(scores) < 2:
            continue
        minimum = min(scores)
        maximum = max(scores)
        spread = maximum - minimum
        volatility_rows.append((title_key, spread, len(scores), minimum, maximum))
    if volatility_rows:
        volatility_rows.sort(key=lambda row: (-row[1], -row[2], row[0]))
        top_rows = volatility_rows[:10]
        log.info(
            "Scoring telemetry: title_volatility(top10)=%s",
            [
                {
                    "title": title,
                    "spread": spread,
                    "count": count,
                    "min": minimum,
                    "max": maximum,
                }
                for title, spread, count, minimum, maximum in top_rows
            ],
        )

def run_scoring(limit: int = 0, rescore: bool = False, job_url: str | None = None) -> dict:
    """Score unscored jobs that have full descriptions."""

    try:
        resume_text = _load_scoring_resume_text()
    except FileNotFoundError:
        log.error("Resume file not found. Run 'applypilot init' first.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "excluded": 0, "auto_healed": 0}
    scoring_profile = _load_scoring_profile()
    conn = get_connection()
    auto_healed = _autoheal_legacy_llm_failures(conn)
    if auto_healed:
        log.info("Auto-healed %d legacy scoring failure row(s).", auto_healed)

    if rescore:
        jobs = get_jobs_by_stage(conn=conn, stage="enriched", limit=limit, job_url=job_url)
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit, job_url=job_url)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {
            "scored": 0,
            "errors": 0,
            "elapsed": 0.0,
            "distribution": [],
            "excluded": 0,
            "auto_healed": auto_healed,
        }

    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    excluded_count = 0
    results: list[dict] = []
    baseline_distribution: Counter = Counter()
    llm_delta_distribution: Counter = Counter()
    parse_failures: Counter = Counter()
    title_scores: dict[str, list[int]] = defaultdict(list)

    for job in jobs:
        exclusion = evaluate_exclusion(job)
        if exclusion is not None:
            result = exclusion
            excluded_count += 1
        else:
            result = score_job(resume_text, job, scoring_profile)

        baseline_score = result.get("baseline_score")
        if isinstance(baseline_score, int):
            baseline_distribution[baseline_score] += 1
        if isinstance(result.get("score_delta"), int):
            llm_delta_distribution[int(result["score_delta"])] += 1
        if result.get("parse_error_category"):
            parse_failures[str(result["parse_error_category"])] += 1

        result["outcome"] = _classify_score_outcome(result)
        result["url"] = job["url"]
        result["score_retry_count"] = int(job.get("score_retry_count") or 0)
        completed += 1
        if result["outcome"] == "llm_failed":
            errors += 1
        else:
            title_key = str(result.get("normalized_title") or _title_key(job.get("title", "")))
            title_scores[title_key].append(int(result.get("score", 0)))
        results.append(result)

        score_value = int(result.get("score", 0))
        title_text = job.get("title", "?")[:60]
        if _SCORE_TRACE_ENABLED or result["outcome"] == "llm_failed":
            _emit_job_block_header(
                completed=completed,
                total=len(jobs),
                score=score_value,
                title=title_text,
                outcome=str(result.get("outcome") or ""),
            )
            _emit_score_trace(result)
            _log_score_trace(result)
            _TRACE_CONSOLE.print("[bright_black]" + ("─" * 110) + "[/bright_black]")
            _log_file_only(logging.INFO, "          %s", "-" * 110)
        else:
            marker, _ = _outcome_markers(str(result.get("outcome") or ""))
            log.info(
                "[%d/%d] score=%d  %s%s",
                completed,
                len(jobs),
                score_value,
                title_text,
                marker,
            )

    now = datetime.now(timezone.utc).isoformat()
    for result in results:
        outcome = result.get("outcome")
        if outcome == "excluded":
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ?, "
                "exclusion_reason_code = ?, exclusion_rule_id = ?, excluded_at = ?, "
                "score_error = NULL, score_retry_count = 0, score_next_retry_at = NULL "
                "WHERE url = ?",
                (
                    0,
                    _compose_score_reasoning(result),
                    now,
                    result.get("exclusion_reason_code"),
                    result.get("exclusion_rule_id"),
                    now,
                    result["url"],
                ),
            )
            continue

        if outcome == "scored_success":
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ?, "
                "exclusion_reason_code = NULL, exclusion_rule_id = NULL, excluded_at = NULL, "
                "score_error = NULL, score_retry_count = 0, score_next_retry_at = NULL "
                "WHERE url = ?",
                (
                    int(result["score"]),
                    _compose_score_reasoning(result),
                    now,
                    result["url"],
                ),
            )
            continue

        retry_count = int(result.get("score_retry_count") or 0)
        next_retry_count = retry_count + 1
        next_retry_at = _next_score_retry_at_iso(retry_count)
        error_text = _normalize_llm_error(str(result.get("reasoning") or ""))
        conn.execute(
            "UPDATE jobs SET fit_score = NULL, score_reasoning = ?, scored_at = NULL, "
            "exclusion_reason_code = NULL, exclusion_rule_id = NULL, excluded_at = NULL, "
            "score_error = ?, score_retry_count = ?, score_next_retry_at = ? "
            "WHERE url = ?",
            (error_text, error_text, next_retry_count, next_retry_at, result["url"]),
        )
    conn.commit()

    elapsed = time.time() - t0
    jobs_per_second = len(results) / elapsed if elapsed > 0 else 0.0
    log.info(
        "Done: %d scored (%d excluded) in %.1fs (%.1f jobs/sec)",
        len(results),
        excluded_count,
        elapsed,
        jobs_per_second,
    )
    _score_telemetry_summary(
        baseline_distribution=baseline_distribution,
        delta_distribution=llm_delta_distribution,
        parse_failures=parse_failures,
        title_scores=title_scores,
    )

    dist = conn.execute(
        """
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
        """
    ).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": len(results),
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
        "excluded": excluded_count,
        "auto_healed": auto_healed,
    }
