"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from applypilot.config import RESUME_JSON_PATH, RESUME_PATH, load_resume_text
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client

log = logging.getLogger(__name__)
MAX_SCORE_RETRIES = 5
_LEGACY_SCORE_ERROR_PATTERN = "%LLM error:%"


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2-3 sentences explaining the score]"""



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


def _tokenize(text: str) -> list[str]:
    """Tokenize text on non-alphanumeric boundaries, lowercased.

    Follows task-8 contract: tokenization on non-alphanumeric characters,
    matching performed on tokens normalized to lower-case.
    """
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _exclusion_result(rule: dict, matched_value: str) -> dict:
    """Build a blocked scoring result for an excluded job.

    Returns a dict with score fields plus audit metadata:
      - exclusion_reason_code: stable reason code from the rule
      - exclusion_rule_id: rule identifier for traceability
    """
    reason_code = rule["reason_code"]
    return {
        "score": 0,
        "keywords": "",
        "reasoning": f"EXCLUDED: {reason_code} \u2014 matched '{matched_value}' (rule {rule['id']})",
        "exclusion_reason_code": reason_code,
        "exclusion_rule_id": rule["id"],
    }


def evaluate_exclusion(job: dict) -> dict | None:
    """Evaluate deterministic exclusion rules against a job.

    Returns exclusion result dict if job is excluded, None if job passes.
    Uses case-insensitive exact/prefix token matching per task-8 contract.
    No LLM calls, no network, fully deterministic.

    Args:
        job: Job dict with keys: title, site, full_description.

    Returns:
        {"score": 0, "keywords": "", "reasoning": "EXCLUDED: ..."} or None.
    """
    title = job.get("title") or ""
    description = job.get("full_description") or job.get("description") or ""
    site = job.get("site") or ""

    title_tokens = _tokenize(title)
    desc_tokens = _tokenize(description)
    combined_tokens = title_tokens + desc_tokens

    for rule in EXCLUSION_RULES:
        values = rule["value"]
        if isinstance(values, str):
            values = [values]

        match_scope = rule.get("match_scope", "title+description")
        match_type = rule.get("match_type", "exact")

        # Site-scoped matching: substring/exact against raw site field
        if match_scope == "site":
            field_lower = site.lower()
            for val in values:
                val_lower = val.lower()
                if match_type == "substring" and val_lower in field_lower:
                    return _exclusion_result(rule, val)
                elif match_type == "exact" and val_lower == field_lower:
                    return _exclusion_result(rule, val)
            continue

        # Select tokens based on scope
        if match_scope == "title":
            tokens = title_tokens
        elif match_scope == "description":
            tokens = desc_tokens
        else:  # title+description (default)
            tokens = combined_tokens

        # Token-based matching
        for val in values:
            val_lower = val.lower()
            if match_type == "exact":
                if val_lower in tokens:
                    return _exclusion_result(rule, val)
            elif match_type == "prefix":
                if any(t.startswith(val_lower) for t in tokens):
                    return _exclusion_result(rule, val)

    return None

def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    score = 0
    keywords = ""
    reasoning = response

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning}


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client()
        response = client.chat(messages, max_output_tokens=512)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": 0, "keywords": "", "reasoning": f"LLM error: {e}"}


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


def _next_score_retry_at_iso(current_retry_count: int) -> str | None:
    if current_retry_count >= MAX_SCORE_RETRIES:
        return None
    delay_minutes = min(5 * (4 ** current_retry_count), 24 * 60)
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

        # Preserve evidence of a prior failure while ensuring these jobs are retryable.
        next_retry_count = min(max(retry_count, 1), MAX_SCORE_RETRIES - 1)
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


def run_scoring(limit: int = 0, rescore: bool = False) -> dict:
    """Score unscored jobs that have full descriptions.
    Jobs are first evaluated against deterministic exclusion rules. Excluded
    jobs bypass the LLM and receive score=0 with an EXCLUDED reason marker.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list,
         "excluded": int, "auto_healed": int}
    """
    try:
        resume_text = _load_scoring_resume_text()
    except FileNotFoundError:
        log.error("Resume file not found. Run 'applypilot init' first.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "excluded": 0, "auto_healed": 0}
    conn = get_connection()
    auto_healed = _autoheal_legacy_llm_failures(conn)
    if auto_healed:
        log.info("Auto-healed %d legacy scoring failure row(s).", auto_healed)

    if rescore:
        if limit > 0:
            jobs = conn.execute("SELECT * FROM jobs WHERE full_description IS NOT NULL LIMIT ?", (limit,)).fetchall()
        else:
            jobs = conn.execute("SELECT * FROM jobs WHERE full_description IS NOT NULL").fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "excluded": 0, "auto_healed": auto_healed}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    excluded_count = 0
    results: list[dict] = []
    for job in jobs:
        # Deterministic exclusion gate: check before LLM scoring
        exclusion = evaluate_exclusion(job)
        if exclusion is not None:
            result = exclusion
            excluded_count += 1
        else:
            result = score_job(resume_text, job)
        result["outcome"] = _classify_score_outcome(result)
        result["url"] = job["url"]
        result["score_retry_count"] = int(job.get("score_retry_count") or 0)
        completed += 1
        if result["outcome"] == "llm_failed":
            errors += 1
        results.append(result)
        marker = " [EXCLUDED]" if result["outcome"] == "excluded" else (" [LLM_FAILED]" if result["outcome"] == "llm_failed" else "")
        log.info(
            "[%d/%d] score=%d  %s%s",
            completed, len(jobs), int(result.get("score", 0)), job.get("title", "?")[:60],
            marker,
        )

    # Write scores to DB
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        outcome = r.get("outcome")
        if outcome == "excluded":
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ?, "
                "exclusion_reason_code = ?, exclusion_rule_id = ?, excluded_at = ?, "
                "score_error = NULL, score_retry_count = 0, score_next_retry_at = NULL "
                "WHERE url = ?",
                (
                    0,
                    _compose_score_reasoning(r),
                    now,
                    r.get("exclusion_reason_code"),
                    r.get("exclusion_rule_id"),
                    now,
                    r["url"],
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
                    int(r["score"]),
                    _compose_score_reasoning(r),
                    now,
                    r["url"],
                ),
            )
            continue

        retry_count = int(r.get("score_retry_count") or 0)
        next_retry_count = min(retry_count + 1, MAX_SCORE_RETRIES)
        next_retry_at = _next_score_retry_at_iso(retry_count) if retry_count < MAX_SCORE_RETRIES else None
        error_text = _normalize_llm_error(str(r.get("reasoning") or ""))
        conn.execute(
            "UPDATE jobs SET fit_score = NULL, score_reasoning = ?, scored_at = NULL, "
            "exclusion_reason_code = NULL, exclusion_rule_id = NULL, excluded_at = NULL, "
            "score_error = ?, score_retry_count = ?, score_next_retry_at = ? "
            "WHERE url = ?",
            (error_text, error_text, next_retry_count, next_retry_at, r["url"]),
        )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Done: %d scored (%d excluded) in %.1fs (%.1f jobs/sec)", len(results), excluded_count, elapsed, len(results) / elapsed if elapsed > 0 else 0)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": len(results),
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
        "excluded": excluded_count,
        "auto_healed": auto_healed,
    }
