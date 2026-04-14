"""Scoring trace & telemetry — formatting, logging, and diagnostic output.

Extracted from orchestrator.py to keep the scoring loop focused on logic.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter

from rich.console import Console

log = logging.getLogger(__name__)

_MODEL_RESPONSE_SNIPPET_LIMIT = 320
_SCORE_TRACE_ENABLED = os.environ.get("APPLYPILOT_SCORE_TRACE", "").strip().lower() in {"1", "true", "yes", "on"}
_TRACE_CONSOLE = Console(stderr=True, highlight=False, soft_wrap=True)
_SHORT_REASON_WORD_RE = re.compile(r"[A-Za-z0-9+#./'-]+")


def is_trace_enabled() -> bool:
    return _SCORE_TRACE_ENABLED


def coerce_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def coerce_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def safe_response_snippet(text: str, limit: int = _MODEL_RESPONSE_SNIPPET_LIMIT) -> str:
    snippet = (text or "").replace("\n", "\\n")
    return snippet if len(snippet) <= limit else snippet[: limit - 3] + "..."


def truncate_piece(text: str, limit: int = 28) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def compact_values(values: list[str], limit: int = 3, item_limit: int = 28) -> str:
    items = [item.strip() for item in values if item and item.strip()]
    if not items:
        return "-"
    shown = [truncate_piece(item, item_limit) for item in items[:limit]]
    remainder = len(items) - len(shown)
    return f"{', '.join(shown)}, +{remainder}" if remainder > 0 else ", ".join(shown)


def compact_reasoning(text: str, limit: int = 110) -> str:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return "-"
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def normalize_short_reason(text: str) -> str:
    words = _SHORT_REASON_WORD_RE.findall((text or "").strip())
    if len(words) < 3:
        return ""
    return " ".join(words[:9])


def derive_short_reason(reasoning: str) -> str:
    text = re.sub(r"\s+", " ", (reasoning or "")).strip()
    if not text:
        return "Mixed fit with notable gaps"
    first_sentence = re.split(r"[.!?]\s+", text, maxsplit=1)[0].strip()
    normalized = normalize_short_reason(first_sentence or text)
    if normalized:
        return normalized
    lowered = text.lower()
    if any(t in lowered for t in ("strong fit", "excellent", "high fit", "good fit")):
        return "Strong fit with clear overlap"
    if any(t in lowered for t in ("poor fit", "weak fit", "mismatch", "not a fit")):
        return "Weak fit with major gaps"
    return "Mixed fit with notable gaps"


def log_file_only(level: int, message: str, *args) -> None:
    root_logger = logging.getLogger()
    file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
    if not file_handlers:
        log.log(level, message, *args)
        return
    record = log.makeRecord(
        log.name, level, __file__, 0, message, args, exc_info=None, func="log_file_only", extra=None
    )
    for h in file_handlers:
        if level >= h.level:
            h.handle(record)


def outcome_markers(outcome: str) -> tuple[str, str]:
    if outcome == "excluded":
        return " [EXCLUDED]", " [yellow][EXCLUDED][/yellow]"
    if outcome == "llm_failed":
        return " [LLM_FAILED]", " [red][LLM_FAILED][/red]"
    return "", ""


def score_color(score: int, outcome: str) -> str:
    if outcome == "excluded":
        return "yellow"
    if outcome == "llm_failed":
        return "red"
    return "green" if score >= 7 else ("yellow" if score >= 4 else "red")


def emit_job_block_header(completed: int, total: int, score: int, title: str, outcome: str) -> None:
    _, marker_rich = outcome_markers(outcome)
    marker_plain, _ = outcome_markers(outcome)
    _TRACE_CONSOLE.print(f"[bold cyan][{completed}/{total}][/bold cyan] {title}{marker_rich}")
    _TRACE_CONSOLE.print(
        f"          [bright_black]└─[/bright_black] [bold]score[/bold] = "
        f"[bold {score_color(score, outcome)}]{score}[/bold {score_color(score, outcome)}]"
    )
    log_file_only(logging.INFO, "[%d/%d] %s%s", completed, total, title, marker_plain)
    log_file_only(logging.INFO, "          └─ score = %d", score)


def emit_score_trace(result: dict) -> None:
    outcome = str(result.get("outcome") or "")
    prefix = "          [bright_black]└─[/bright_black] "

    if outcome == "excluded":
        reason = compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        _TRACE_CONSOLE.print(f"{prefix}[yellow]excluded[/yellow] [dim]{reason}[/dim]")
        return
    if outcome == "llm_failed":
        category = truncate_piece(str(result.get("parse_error_category") or "unknown"), limit=24)
        baseline = result.get("baseline_score")
        error = compact_reasoning(str(result.get("reasoning") or ""), limit=120)
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
    matched = compact_values(coerce_list(result.get("matched_skills")), limit=3, item_limit=22)
    missing = compact_values(coerce_list(result.get("missing_requirements")), limit=2, item_limit=28)
    full_reasoning = str(result.get("llm_reasoning_full") or result.get("reasoning") or "")
    why_short = (
        normalize_short_reason(str(result.get("llm_why_short") or "").strip())
        if result.get("llm_why_short")
        else derive_short_reason(full_reasoning)
    )
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
    if full_reasoning.strip():
        _TRACE_CONSOLE.print(f"{prefix}[bright_black]reasoning[/bright_black] [dim]{full_reasoning.strip()}[/dim]")


def log_score_trace(result: dict) -> None:
    outcome = str(result.get("outcome") or "")
    prefix = "          └─ "

    if outcome == "excluded":
        log_file_only(
            logging.INFO, "%sexcluded %s", prefix, compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        )
        return
    if outcome == "llm_failed":
        log_file_only(
            logging.INFO,
            "%sfailed cat=%s b=%s",
            prefix,
            truncate_piece(str(result.get("parse_error_category") or "unknown"), limit=24),
            result.get("baseline_score") or "-",
        )
        log_file_only(
            logging.INFO, "%swhy %s", prefix, compact_reasoning(str(result.get("reasoning") or ""), limit=120)
        )
        return

    baseline = result.get("baseline_score")
    llm_score = result.get("llm_score")
    confidence = result.get("llm_confidence")
    delta = result.get("score_delta")
    matched = compact_values(coerce_list(result.get("matched_skills")), limit=3, item_limit=22)
    missing = compact_values(coerce_list(result.get("missing_requirements")), limit=2, item_limit=28)
    full_reasoning = str(result.get("llm_reasoning_full") or result.get("reasoning") or "")
    why_short = (
        normalize_short_reason(str(result.get("llm_why_short") or "").strip())
        if result.get("llm_why_short")
        else derive_short_reason(full_reasoning)
    )
    confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
    delta_text = f"{int(delta):+d}" if isinstance(delta, int) else "-"

    log_file_only(
        logging.INFO,
        "%strace b=%s l=%s c=%s Δ=%s m=%s x=%s",
        prefix,
        baseline or "-",
        llm_score or "-",
        confidence_text,
        delta_text,
        matched,
        missing,
    )
    log_file_only(logging.INFO, "%swhy %s", prefix, why_short)
    reasoning_text = re.sub(r"\s+", " ", full_reasoning).strip()
    if reasoning_text:
        log_file_only(logging.INFO, "%sreasoning %s", prefix, reasoning_text)


def score_telemetry_summary(
        baseline_distribution: Counter,
        delta_distribution: Counter,
        parse_failures: Counter,
        title_scores: dict[str, list[int]],
) -> None:
    if baseline_distribution:
        log.info(
            "Scoring telemetry: baseline_distribution=%s", dict(sorted(baseline_distribution.items(), reverse=True))
        )
    if delta_distribution:
        log.info("Scoring telemetry: llm_delta_distribution=%s", dict(sorted(delta_distribution.items())))
    if parse_failures:
        log.info("Scoring telemetry: parse_failures=%s", dict(parse_failures.most_common()))

    volatility_rows = []
    for title_key, scores in title_scores.items():
        if len(scores) < 2:
            continue
        spread = max(scores) - min(scores)
        volatility_rows.append((title_key, spread, len(scores), min(scores), max(scores)))
    if volatility_rows:
        volatility_rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
        log.info(
            "Scoring telemetry: title_volatility(top10)=%s",
            [{"title": t, "spread": s, "count": c, "min": mn, "max": mx} for t, s, c, mn, mx in volatility_rows[:10]],
        )
