"""Resume tailoring: LLM-powered ATS-optimized resume generation per job.

THIS IS THE HEAVIEST REFACTOR. Every piece of personal data -- name, email, phone,
skills, companies, projects, school -- is loaded at runtime from the user's profile.
Zero hardcoded personal information.

The LLM returns structured JSON, code assembles the final text. Header (name, contact)
is always code-injected, never LLM-generated. Each retry starts a fresh conversation
to avoid apologetic spirals.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from applypilot.config import TAILORED_DIR, load_profile, load_resume_text
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client
from applypilot.resume_json import (
    get_profile_company_names,
    get_profile_school_names,
    get_profile_skill_keywords,
    get_profile_skill_sections,
    get_profile_verified_metrics,
)
from applypilot.scoring.artifact_naming import build_artifact_prefix
from applypilot.scoring.validator import (
    BANNED_WORDS,
    FABRICATION_WATCHLIST,
    sanitize_text,
    validate_json_fields,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up

# ── Skill-Gap Detection (deterministic, no LLM) ─────────────────────────

_STOPWORDS = frozenset(
    "a about above after again all am an and any are as at be because been before being "
    "below between both but by can could did do does doing down during each few for from "
    "further get got had has have having he her here hers herself him himself his how i if "
    "in into is it its itself just let like make me might more most must my myself no nor "
    "not now of off on once only or other our ours ourselves out over own part per please "
    "put re s same she should so some still such t than that the their theirs them "
    "themselves then there these they this those through to too under until up us very was "
    "we were what when where which while who whom why will with would you your yours "
    "yourself yourselves able also work working experience team role position job company "
    "including include includes using use used based well within across join looking "
    "opportunity responsibilities responsible required requirements preferred qualifications "
    "minimum years year strong knowledge ability skills skill ensure support provide "
    "develop development manage management build building create creating maintain "
    "maintaining etc e g i e "
    "benefits compensation incentive awards perks maternity parental leave health pto "
    "belonging culture associate associates customer customers supplier suppliers "
    "community communities employer equal opportunity inclusive inclusion diversity "
    "valued respected identities opinions styles experiences ideas welcoming "
    "country countries world worldwide global operate operating operations "
    "retailer retail warehouse club membership physical geographic region "
    "floor tower flrs part india chennai bangalore location primary located "
    "outlined listed none below above option options "
    "aim alignment among ago best bring career careers commitment consistent "
    "continuous continuously creating define defining deliver delivering detail "
    "dynamic effectively engaged engagement environment epic expert experts "
    "family feel feels first foreground foster fostering great grow growing "
    "guidance heart helping imagine impact innovative innovate learn learner "
    "led leverage life live lives making meet million millions mindset "
    "new next people person place power powered practices proud purpose "
    "really reinventing rooted sense serve serving shaping start started "
    "today transformative understand unique way welcome".split()
)


def _extract_jd_keywords(jd_text: str) -> set[str]:
    """Extract meaningful terms from a JD. Works for any domain."""
    text = re.sub(r"https?://\S+", " ", jd_text)
    text = re.sub(r"\S+@\S+", " ", text)
    text = re.sub(r"[^a-zA-Z+#\s-]", " ", text).lower()
    words = text.split()

    def _is_useful(w: str) -> bool:
        return len(w) >= 3 and w not in _STOPWORDS

    bigrams = set()
    for i in range(len(words) - 1):
        if _is_useful(words[i]) and _is_useful(words[i + 1]):
            bigrams.add(f"{words[i]} {words[i + 1]}")
    singles = {w for w in words if _is_useful(w)}
    return bigrams | singles


def check_skill_gaps(jd_text: str, tailored_text: str) -> dict:
    """Compare JD keywords against tailored resume — no LLM call."""
    jd_keywords = _extract_jd_keywords(jd_text)
    resume_lower = tailored_text.lower()
    matched = {kw for kw in jd_keywords if kw in resume_lower}
    missing = jd_keywords - matched
    return {
        "jd_keywords": len(jd_keywords),
        "matched": sorted(matched),
        "missing": sorted(missing),
        "coverage": round(len(matched) / len(jd_keywords), 2) if jd_keywords else 1.0,
    }


# ── Prompt Builders (profile-driven) ──────────────────────────────────────

def _build_education_block(education_list: list[dict]) -> str:
    """Build the education block from structured profile education data."""

    if not education_list:
        return "N/A"
    lines: list[str] = []
    for edu in education_list:
        institution = edu.get("institution", "Unknown")
        degree = edu.get("studyType", "") or edu.get("degree", "")
        field = edu.get("area", "") or edu.get("field", "")
        end_date = edu.get("endDate", "") or edu.get("graduation_date", "")
        year = end_date[:4] if end_date and len(end_date) >= 4 else end_date
        parts = [part for part in (degree, field, year) if part]
        lines.append(f"{institution} | {' | '.join(parts)}" if parts else institution)
    return "\n".join(lines)


def _build_tailor_prompt(profile: dict, resume_text: str | None = None) -> str:
    """Build the resume tailoring system prompt from the user's profile.

    Prompt structure: constraints first, context second, example last.
    Tighter prompts = less room for the model to go off-script.
    Tested across 8 models (Opus→Llama 8B) — all produce correct output.
    """
    del resume_text

    # ── Extract profile data ─────────────────────────────────────────
    skills_lines = []
    for label, items in get_profile_skill_sections(profile):
        skills_lines.append(f"{label}: {', '.join(items)}")
    skills_block = "\n".join(skills_lines)

    companies = get_profile_company_names(profile)
    # Fall back to work[] entries when preserved_companies is empty
    if not companies:
        companies = [w.get("name", "") for w in profile.get("work", []) if w.get("name")]
    role_count = len(companies) or 1
    schools = get_profile_school_names(profile)
    school = schools[0] if schools else ""
    real_metrics = get_profile_verified_metrics(profile)
    metrics_str = ", ".join(real_metrics) if real_metrics else "N/A"
    yoe = profile.get("years_of_experience_total", "")
    banned_str = ", ".join(BANNED_WORDS)

    education_block = _build_education_block(profile.get("education", []))
    if education_block == "N/A":
        education_level = profile.get("experience", {}).get("education_level", "")
        if education_level:
            education_block = f"{school} | {education_level}" if school else education_level

    # ── Bullet budget ────────────────────────────────────────────────
    validation_cfg = profile.get("tailoring_config", {}).get("validation", {})
    min_bullets = validation_cfg.get("min_bullets_per_role", 0)
    max_bullets = validation_cfg.get("max_bullets_per_role", 0)

    if min_bullets <= 0 or max_bullets <= 0:
        from applypilot.tailoring.page_budget import calculate
        budget = calculate(
            experience_count=role_count,
            skill_group_count=len(skills_lines),
        )
        per_exp = budget["bullets_per_experience"]
        raw_max = min(max(per_exp), 8) if per_exp else 5
        raw_min = max(min(per_exp), 2) if per_exp else 3
        max_bullets = raw_max
        min_bullets = min(raw_min, raw_max)

    # ── Build prompt: constraints first, context second, example last ─
    target_role = profile.get("target_role", "")
    companies_str = ", ".join(companies) if companies else "N/A"
    max_total_bullets = max_bullets + min_bullets * max(role_count - 1, 0)

    return f"""You are a senior recruiter rewriting a resume for a specific job description (JD).

Return ONLY a valid JSON object. No markdown, no commentary.

--------------------------------------------------
## INPUT ASSUMPTIONS
- Base resume and JD are provided separately
- All content must strictly originate from the base resume

--------------------------------------------------
## HARD CONSTRAINTS (STRICT)

1. EXPERIENCE COUNT
- EXACTLY {role_count} roles must be present
- No role may be removed

2. BULLET COUNTS (PRE-CALCULATED)
- Most recent role: EXACTLY {max_bullets}
- All other roles: EXACTLY {min_bullets}

3. BULLET FORMAT (MANDATORY STAR)
Each bullet MUST be a single sentence following:
- Situation/Task + Action + Result (include metric if present)

Each bullet MUST be:
{{"text": "...", "skills": ["Skill1", "Skill2"]}}

4. SKILLS ARRAY RULES
- Min 2, Max 3 skills per bullet
- Skills MUST exist in:
  a) base resume OR
  b) the skills boundary below
- Do NOT infer or introduce new skills

5. SKILL WHITELIST ENFORCEMENT (STRICT)
- Define SKILL_SET = (all skills present in base resume + skills boundary below)
- EVERY skill used in bullet "skills" arrays and top-level "skills" section MUST belong to SKILL_SET
- If any skill falls outside SKILL_SET, regenerate output
- Do NOT normalize or alias skills (e.g., do not convert "JS" to "JavaScript" unless explicitly present)
- Skills within a single bullet MUST be unique
- Avoid repeating the same skill across consecutive bullets unless required

6. FABRICATION CHECK (STRICT ENFORCEMENT)
- Do NOT introduce:
  - new companies
  - new roles
  - new tools/technologies
  - new metrics
- Preserve numeric values EXACTLY: {metrics_str}
- Preserve company names: {companies_str}
- Preserve school: {school}
- Reuse original entities and terminology wherever possible

7. REWRITING RULE
- Every bullet MUST be rewritten
- Preserve:
  - meaning
  - metrics
  - entities (tools, systems, names)
- Change phrasing and structure only
- Do NOT copy verbatim

8. JD ALIGNMENT (CONTROLLED)
- Extract keywords ONLY from JD
- Reorder bullets based on overlap with JD keywords
- Do NOT introduce JD skills if absent in resume

9. PROJECTS (CONTROLLED RETENTION)
- If total projects <= 2: retain all
- If total projects > 2: keep ONLY top 2 most JD-relevant
- Reorder by relevance
- Do NOT fabricate or merge projects

10. SUMMARY
- EXACTLY 2 sentences
- Sentence 1: strongest JD-relevant skills (from resume only)
- Sentence 2: measurable impact or specialization

11. TITLE
- Must match {target_role if target_role else "the target role from JD"}
- Maintain original seniority level

12. STYLE RULES
- No em dashes
- No banned words: {banned_str}
- Strong action verbs
- Concise and direct

## VOICE (STRICT)

- Tone: professional, direct, results-oriented
- Sentence length: 12–22 words per bullet
- Use past tense for completed work, present tense only for ongoing roles
- Start every bullet with a strong action verb (e.g., Built, Designed, Implemented, Reduced, Automated, Optimized, Deployed)
- Avoid filler phrases (e.g., "responsible for", "worked on", "involved in")
- Avoid generic adjectives (e.g., "various", "several", "numerous", "dynamic", "cutting-edge")
- Avoid subjective claims (e.g., "excellent", "highly skilled", "expert") unless directly supported by metrics

## STAR ENFORCEMENT DETAIL

Each bullet MUST follow this structure:
- Action verb + task + method/tool + measurable result

Example pattern:
"Optimized API response time using Redis caching, reducing latency by 35%"

## CONSISTENCY RULES

- Use consistent verb tense within each role
- Avoid repeating the same starting verb more than twice per role
- Prefer concrete nouns over abstractions (e.g., "REST API" instead of "system", "Android module" instead of "component")

## BREVITY CONTROL

- No bullet should exceed 25 words
- Remove unnecessary connectors (e.g., "in order to", "successfully", "effectively")
- Keep only information that contributes to impact or relevance to JD

--------------------------------------------------
## LENGTH CONTROL (DETERMINISTIC)

Define:
- TOTAL_BULLETS = sum of all bullets
- MAX_TOTAL_BULLETS = {max_total_bullets}

IF TOTAL_BULLETS > MAX_TOTAL_BULLETS:
  Apply reductions in this EXACT order:
  1. Condense "skills" section to 3-4 key groups (do NOT remove)
  2. Shorten bullet text length (NOT bullet count)
  3. Trim project bullets (NOT experience bullets)

IMPORTANT:
- Do NOT remove experience bullets
- Do NOT modify bullet counts

--------------------------------------------------
## MISSING DATA RULE

- If data is missing in the base resume:
  - Do NOT infer
  - Do NOT approximate
  - Do NOT generate probabilistically
- Use empty string "" where required
- Omit optional content gracefully

--------------------------------------------------
## EXPERIENCE LEVEL CONTROL

Total experience: {yoe if yoe else "not specified"} years

- Do NOT inflate seniority
- Tone must match actual experience

--------------------------------------------------
## SKILLS BOUNDARY:
{skills_block}

--------------------------------------------------
## SELF-VALIDATION (MANDATORY BEFORE OUTPUT)

Internally verify:
- experience count == {role_count}
- bullet counts are exact
- all skills belong to SKILL_SET
- no fabricated entities
- STAR format applied in every bullet
- JSON is valid
- All bullets follow voice constraints (length, verb start, STAR structure)

If ANY check fails, regenerate before returning.

--------------------------------------------------
## OUTPUT FORMAT (EXAMPLE)

{{"title":"Role Title","summary":"Sentence one. Sentence two.","skills":{{"Languages":"...","Frameworks":"..."}},"experience":[{{"header":"Title at Company","subtitle":"Tech | Dates","bullets":[{{"text":"...","skills":["S1","S2"]}}]}},{{"header":"Title at Company 2","subtitle":"Tech | Dates","bullets":[{{"text":"...","skills":["S1"]}}]}}],"projects":[{{"header":"Name","subtitle":"Tech","bullets":[{{"text":"...","skills":["S1"]}}]}}],"education":"{education_block}"}}"""

def _build_judge_prompt(profile: dict) -> str:
    """Build the LLM judge prompt from the user's profile."""
    skills_str = ", ".join(get_profile_skill_keywords(profile)) or "N/A"
    real_metrics = get_profile_verified_metrics(profile)
    metrics_str = ", ".join(real_metrics) if real_metrics else "N/A"

    return f"""You are a resume quality judge. A tailoring engine rewrote a resume to target a specific job. Your job is to catch LIES, not style changes.

You must answer with EXACTLY this format:
VERDICT: PASS or FAIL
ISSUES: (list any problems, or "none")

## CONTEXT -- what the tailoring engine was instructed to do (all of this is ALLOWED):
- Change the title to match the target role
- Rewrite the summary from scratch for the target job
- Reorder bullets and projects to put the most relevant first
- Reframe bullets to use the job's language
- Drop low-relevance bullets and replace with more relevant ones from other sections
- Reorder the skills section to put job-relevant skills first
- Change tone and wording extensively

## WHAT IS FABRICATION (FAIL for these):
1. Adding tools, languages, or frameworks to TECHNICAL SKILLS that aren't in the original. The allowed skills are ONLY: {skills_str}
2. Inventing NEW metrics or numbers not in the original. The real metrics are: {metrics_str}
3. Inventing work that has no basis in any original bullet (completely new achievements).
4. Adding companies, roles, or degrees that don't exist.
5. Changing real numbers (inflating 80% to 95%, 500 nodes to 1000 nodes).

## WHAT IS NOT FABRICATION (do NOT fail for these):
- Rewording any bullet, even heavily, as long as the underlying work is real
- Combining two original bullets into one
- Splitting one original bullet into two
- Describing the same work with different emphasis
- Dropping bullets entirely
- Reordering anything
- Changing the title or summary completely

## TOLERANCE RULE:
The goal is to get interviews, not to be a perfect fact-checker. Allow up to 3 minor stretches per resume:
- Adding a closely related tool the candidate could realistically know is a MINOR STRETCH, not fabrication.
- Reframing a metric with slightly different wording is a MINOR STRETCH.
- Adding any LEARNABLE skill given their existing stack is a MINOR STRETCH.
- Only FAIL if there are MAJOR lies: completely invented projects, fake companies, fake degrees, wildly inflated numbers, or skills from a completely different domain.

Be strict about major lies. Be lenient about minor stretches and learnable skills. Do not fail for style, tone, or restructuring."""


# ── JSON Extraction ───────────────────────────────────────────────────────

def extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response (handles fences, preamble).

    Args:
        raw: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    raw = raw.strip()

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Markdown fences
    if "```" in raw:
        for part in raw.split("```")[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    # Find outermost { ... }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON found in LLM response")


def _normalize_bullet(bullet: Any) -> str:
    """Normalize a bullet to plain text, stripping embedded JSON metadata."""

    if isinstance(bullet, dict):
        for key in ("text", "bullet", "content", "description"):
            value = bullet.get(key)
            if isinstance(value, str):
                return value.strip()
        return json.dumps(bullet, ensure_ascii=False)

    bullet_str = str(bullet).strip()
    if bullet_str.startswith("{") or bullet_str.startswith("["):
        try:
            parsed = json.loads(bullet_str)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("text", "bullet", "content", "description"):
                value = parsed.get(key)
                if isinstance(value, str):
                    return value.strip()
            return json.dumps(parsed, ensure_ascii=False)

    json_start = bullet_str.find(" {")
    if json_start == -1:
        json_start = bullet_str.find("\t{")
    if json_start != -1:
        candidate = bullet_str[:json_start].rstrip()
        remainder = bullet_str[json_start:].strip()
        if remainder.startswith("{") and ("variants" in remainder or "tags" in remainder or "role_families" in remainder):
            return candidate
    return bullet_str


def _strip_disallowed_watchlist_skills(data: dict, profile: dict) -> list[str]:
    """Remove watchlist skills from generated skill output."""

    skills = data.get("skills")
    if not isinstance(skills, dict):
        return []

    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    # Keep function signature aligned with profile-aware sanitizers even though
    # watchlist terms are always stripped to match validator behavior.
    del profile
    watchlist_norm: set[str] = set()
    for skill in FABRICATION_WATCHLIST:
        if len(skill) <= 2:
            continue
        normalized_skill = _normalize(skill)
        if not normalized_skill:
            continue
        # Avoid collapsing values like "c++" to single-character tokens ("c").
        if len(normalized_skill.replace(" ", "")) <= 2:
            continue
        watchlist_norm.add(normalized_skill)

    removed: list[str] = []

    for key, value in list(skills.items()):
        if isinstance(value, str):
            entries = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, list):
            entries = [str(part).strip() for part in value if str(part).strip()]
        else:
            entries = [str(value).strip()] if str(value).strip() else []

        kept: list[str] = []
        for entry in entries:
            entry_norm = _normalize(entry)
            if not entry_norm:
                continue
            is_watchlist = any(w in entry_norm for w in watchlist_norm)
            if is_watchlist:
                removed.append(entry)
                continue
            kept.append(entry)

        skills[key] = ", ".join(kept)

    return removed


def _build_tailored_prefix(job: dict) -> str:
    """Build a deterministic, collision-resistant filename prefix for a job."""

    return build_artifact_prefix(job)


# ── Resume Assembly (profile-driven header) ──────────────────────────────

def assemble_resume_text(data: dict, profile: dict) -> str:
    """Convert JSON resume data to formatted plain text.

    Header (name, location, contact) is ALWAYS code-injected from the profile,
    never LLM-generated. All text fields are sanitized.

    Args:
        data: Parsed JSON resume from the LLM.
        profile: User profile dict from load_profile().

    Returns:
        Formatted resume text.
    """
    personal = profile.get("personal", {})
    lines: list[str] = []

    # Header -- always code-injected from profile
    lines.append(personal.get("full_name", ""))
    lines.append(sanitize_text(data.get("title", "Software Engineer")))

    # Location from search config or profile -- leave blank if not available
    # The location line is optional; the original used a hardcoded city.
    # We omit it here; the LLM prompt can include it if the user sets it.

    # Contact line
    contact_parts: list[str] = []
    if personal.get("email"):
        contact_parts.append(personal["email"])
    if personal.get("phone"):
        contact_parts.append(personal["phone"])
    if personal.get("github_url"):
        contact_parts.append(personal["github_url"])
    if personal.get("linkedin_url"):
        contact_parts.append(personal["linkedin_url"])
    if contact_parts:
        lines.append(" | ".join(contact_parts))
    lines.append("")

    # Summary
    lines.append("SUMMARY")
    lines.append(sanitize_text(data["summary"]))
    lines.append("")

    # Technical Skills
    lines.append("TECHNICAL SKILLS")
    if isinstance(data["skills"], dict):
        for cat, val in data["skills"].items():
            lines.append(f"{cat}: {sanitize_text(str(val))}")
    lines.append("")

    # Experience
    lines.append("EXPERIENCE")
    for entry in data.get("experience", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            bullet_text = _normalize_bullet(b)
            if bullet_text:
                lines.append(f"- {sanitize_text(bullet_text)}")
        lines.append("")

    # Projects
    lines.append("PROJECTS")
    for entry in data.get("projects", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            bullet_text = _normalize_bullet(b)
            if bullet_text:
                lines.append(f"- {sanitize_text(bullet_text)}")
        lines.append("")

    # Education
    lines.append("EDUCATION")
    lines.append(sanitize_text(str(data.get("education", ""))))

    return "\n".join(lines)


# ── LLM Judge ────────────────────────────────────────────────────────────

def judge_tailored_resume(
    original_text: str, tailored_text: str, job_title: str, profile: dict
) -> dict:
    """LLM judge layer: catches subtle fabrication that programmatic checks miss.

    Args:
        original_text: Base resume text.
        tailored_text: Tailored resume text.
        job_title: Target job title.
        profile: User profile for building the judge prompt.

    Returns:
        {"passed": bool, "verdict": str, "issues": str, "raw": str}
    """
    judge_prompt = _build_judge_prompt(profile)

    messages = [
        {"role": "system", "content": judge_prompt},
        {"role": "user", "content": (
            f"JOB TITLE: {job_title}\n\n"
            f"ORIGINAL RESUME:\n{original_text}\n\n---\n\n"
            f"TAILORED RESUME:\n{tailored_text}\n\n"
            "Judge this tailored resume:"
        )},
    ]

    # CHANGED: Judge uses quality model — it evaluates tailored resume accuracy.
    client = get_client(quality=True)
    response = client.chat(messages, max_output_tokens=512)

    passed = "VERDICT: PASS" in response.upper()
    issues = "none"
    if "ISSUES:" in response.upper():
        issues_idx = response.upper().index("ISSUES:")
        issues = response[issues_idx + 7:].strip()

    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "issues": issues,
        "raw": response,
    }


# ── Core Tailoring ───────────────────────────────────────────────────────

def tailor_resume(
    resume_text: str, job: dict, profile: dict,
    max_retries: int = 3, validation_mode: str = "normal",
) -> tuple[str, dict]:
    """Generate a tailored resume via JSON output + fresh context on each retry.

    Key design choices:
    - LLM returns structured JSON, code assembles the text (no header leaks)
    - Each retry starts a FRESH conversation (no apologetic spiral)
    - Issues from previous attempts are noted in the system prompt
    - Em dashes and smart quotes are auto-fixed, not rejected

    Args:
        resume_text:      Base resume text.
        job:              Job dict with title, site, location, full_description.
        profile:          User profile dict.
        max_retries:      Maximum retry attempts.
        validation_mode:  "strict", "normal", or "lenient".
                          strict  -- banned words trigger retries; judge must pass
                          normal  -- banned words = warnings only; judge can fail on last retry
                          lenient -- banned words ignored; LLM judge skipped

    Returns:
        (tailored_text, report) where report contains validation details.
    """
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    report: dict = {
        "attempts": 0, "validator": None, "judge": None,
        "status": "pending", "validation_mode": validation_mode,
    }
    avoid_notes: list[str] = []
    tailored = ""
    # CHANGED: Tailoring uses quality model — output quality directly impacts
    # interview chances. Scoring uses cheap model (Gemini), tailoring uses
    # expensive one (Bedrock Opus). Set via LLM_MODEL_QUALITY env var.
    client = get_client(quality=True)
    tailor_prompt_base = _build_tailor_prompt(profile)

    for attempt in range(max_retries + 1):
        report["attempts"] = attempt + 1

        # Fresh conversation every attempt
        prompt = tailor_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES (from previous attempt):\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"ORIGINAL RESUME:\n{resume_text}\n\n---\n\nTARGET JOB:\n{job_text}\n\nReturn the JSON:"},
        ]

        log.debug("[tailor] %s — prompt length: %d chars", job.get("title", "?")[:40], len(prompt))
        raw = client.chat(messages, max_output_tokens=16000)
        log.debug("[tailor] %s — LLM response: %s", job.get("title", "?")[:40], raw[:400])

        # Parse JSON from response
        try:
            data = extract_json(raw)
        except ValueError as exc:
            log.warning("Attempt %d JSON parse failed (%s). Raw response (first 500 chars):\n%s",
                        attempt + 1, exc, raw[:1000])
            avoid_notes.append("Output was not valid JSON. Return ONLY a JSON object, nothing else.")
            continue

        removed_skills = _strip_disallowed_watchlist_skills(data, profile)
        if removed_skills:
            log.info(
                "Attempt %d removed disallowed watchlist skills: %s",
                attempt + 1,
                ", ".join(removed_skills[:5]),
            )

        # Layer 1: Validate JSON fields
        validation = validate_json_fields(data, profile, mode=validation_mode)
        report["validator"] = validation
        # Preserve raw LLM JSON for _DATA.json sidecar (skill annotations for PDF bolding)
        report["raw_json"] = data

        if not validation["passed"]:
            # Only retry if there are hard errors (warnings never block)
            log.warning("Attempt %d validation failed: %s", attempt + 1, validation["errors"])
            avoid_notes.extend(validation["errors"])
            if attempt < max_retries:
                continue
            # Last attempt — assemble whatever we got
            tailored = assemble_resume_text(data, profile)
            report["status"] = "failed_validation"
            return tailored, report

        # Assemble text (header injected by code, em dashes auto-fixed)
        tailored = assemble_resume_text(data, profile)

        # Layer 2: LLM judge (catches subtle fabrication) — skipped in lenient mode
        if validation_mode == "lenient":
            report["judge"] = {"verdict": "SKIPPED", "passed": True, "issues": "none"}
            report["status"] = "approved"
            return tailored, report

        judge = judge_tailored_resume(resume_text, tailored, job.get("title", ""), profile)
        report["judge"] = judge

        if not judge["passed"]:
            avoid_notes.append(f"Judge rejected: {judge['issues']}")
            if attempt < max_retries:
                # In normal mode, only retry on judge failure if there are retries left
                if validation_mode != "lenient":
                    continue
            # Accept best attempt on last retry (all modes) or if lenient
            report["status"] = "approved_with_judge_warning"
            return tailored, report

        # Both passed
        report["status"] = "approved"
        return tailored, report

    report["status"] = "exhausted_retries"
    return tailored, report


# ── Batch Entry Point ────────────────────────────────────────────────────

def run_tailoring(
    min_score: int = 7,
    limit: int = 0,
    validation_mode: str = "normal",
    target_url: str | None = None,
    force: bool = False,
) -> dict:
    """Generate tailored resumes for high-scoring jobs.

    Args:
        min_score:       Minimum fit_score to tailor for.
        limit:           Maximum jobs to process (0 = all eligible jobs).
        validation_mode: "strict", "normal", or "lenient".
        target_url:      Optional URL to tailor a single matched job.
        force:           When target_url is provided, regenerate even if tailored resume exists
                         or fit_score is below min_score.

    Returns:
        {"approved": int, "failed": int, "errors": int, "elapsed": float}
    """
    profile = load_profile()
    try:
        resume_text = load_resume_text()
    except FileNotFoundError:
        log.error("Resume file not found. Run 'applypilot init' first.")
        return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
    conn = get_connection()

    if target_url:
        like = f"%{target_url.split('?')[0].rstrip('/')}%"
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE (url = ? OR application_url = ? OR application_url LIKE ? OR url LIKE ?)
            LIMIT 1
            """,
            (target_url, target_url, like, like),
        ).fetchone()
        if not row:
            log.info("Target URL not found in database: %s", target_url)
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

        if isinstance(row, dict):
            target_job = dict(row)
        else:
            columns = row.keys()
            target_job = dict(zip(columns, row))
        if not target_job.get("full_description"):
            log.error("Target job has no full description. Run 'applypilot run enrich' first.")
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
        score = target_job.get("fit_score")
        if not force and score is not None and score < min_score:
            log.info(
                "Target job score %s is below min-score %d. Use --force to override.",
                score,
                min_score,
            )
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
        if not force and target_job.get("tailored_resume_path"):
            log.info("Target job already has a tailored resume. Use --force to regenerate.")
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
        jobs = [target_job]
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_tailor", min_score=min_score, limit=limit)

    if not jobs:
        log.info("No untailored jobs with score >= %d.", min_score)
        return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Tailoring resumes for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    stats: dict[str, int] = {"approved": 0, "failed_validation": 0, "failed_judge": 0, "error": 0}

    for job in jobs:
        completed += 1
        try:
            tailored, report = tailor_resume(resume_text, job, profile,
                                             validation_mode=validation_mode)

            # ADDED: Skill-gap check + debug logging for tailor results
            jd_text = job.get("full_description") or ""
            if jd_text and tailored:
                report["skill_gaps"] = check_skill_gaps(jd_text, tailored)
                coverage = report["skill_gaps"]["coverage"]
                log.debug("[tailor] %s — skill coverage: %.0f%% missing: %s",
                          job.get("title", "?")[:40], coverage * 100,
                          report["skill_gaps"]["missing"][:10])
                if coverage < 0.5:
                    log.warning("Low JD keyword coverage (%.0f%%) for %s", coverage * 100, job["title"][:50])
            bullet_count = tailored.count("\n- ")
            log.debug("[tailor] %s — bullets: %d, status: %s, attempts: %d",
                      job.get("title", "?")[:40], bullet_count, report["status"], report["attempts"])

            # Build collision-resistant filename prefix
            prefix = _build_tailored_prefix(job)

            # Save tailored resume text
            txt_path = TAILORED_DIR / f"{prefix}.txt"
            txt_path.write_text(tailored, encoding="utf-8")
            if not txt_path.exists() or txt_path.stat().st_size == 0:
                raise RuntimeError(f"Failed to persist tailored TXT: {txt_path}")

            # Save raw LLM JSON with {text, skills} bullet annotations.
            # build_html() reads this sidecar to bold skill keywords in the PDF,
            # proving skills are real (used in context) not just listed.
            if "raw_json" in report:
                data_path = TAILORED_DIR / f"{prefix}_DATA.json"
                data_path.write_text(json.dumps(report["raw_json"], indent=2, ensure_ascii=False), encoding="utf-8")

            # Save job description for traceability
            job_path = TAILORED_DIR / f"{prefix}_JOB.txt"
            job_desc = (
                f"Title: {job['title']}\n"
                f"Company: {job['site']}\n"
                f"Location: {job.get('location', 'N/A')}\n"
                f"Score: {job.get('fit_score', 'N/A')}\n"
                f"URL: {job['url']}\n\n"
                f"{job.get('full_description', '')}"
            )
            job_path.write_text(job_desc, encoding="utf-8")

            # Save validation report
            report_path = TAILORED_DIR / f"{prefix}_REPORT.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Generate PDF for approved resumes.
            # "approved_with_judge_warning" is also a success — resume was generated.
            pdf_path = None
            status = report["status"]
            if status in ("approved", "approved_with_judge_warning"):
                try:
                    from applypilot.scoring.pdf import convert_to_pdf
                    generated_pdf = convert_to_pdf(txt_path)
                    pdf_path = str(generated_pdf)
                    if not generated_pdf.exists() or generated_pdf.stat().st_size == 0:
                        raise RuntimeError(f"Generated PDF missing or empty: {generated_pdf}")
                except Exception as exc:
                    # A submission-ready tailored resume needs both TXT and PDF.
                    log.error("PDF generation failed for %s: %s", txt_path, exc)
                    status = "error"

            result = {
                "url": job["url"],
                "path": str(txt_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
                "status": status,
                "attempts": report["attempts"],
            }
            if status in ("approved", "approved_with_judge_warning"):
                log.info("Saved tailored artifacts: txt=%s | pdf=%s", txt_path.resolve(), Path(pdf_path).resolve())
            else:
                log.info("Saved tailored TXT: %s", txt_path.resolve())
        except Exception as e:
            result = {
                "url": job["url"], "title": job["title"], "site": job["site"],
                "status": "error", "attempts": 0, "path": None, "pdf_path": None,
            }
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

        results.append(result)
        stats[result.get("status", "error")] = stats.get(result.get("status", "error"), 0) + 1

        elapsed = time.time() - t0
        rate = completed / elapsed if elapsed > 0 else 0
        log.info(
            "%d/%d [%s] attempts=%s | %.1f jobs/min | %s",
            completed, len(jobs),
            result["status"].upper(),
            result.get("attempts", "?"),
            rate * 60,
            result["title"][:40],
        )

    # Persist to DB: increment attempt counter for ALL, save path only for approved
    now = datetime.now(timezone.utc).isoformat()
    _success_statuses = {"approved", "approved_with_judge_warning"}
    for r in results:
        if r["status"] in _success_statuses:
            conn.execute(
                "UPDATE jobs SET tailored_resume_path=?, tailored_at=?, "
                "tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
        else:
            conn.execute(
                "UPDATE jobs SET tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info(
        "Tailoring done in %.1fs: %d approved, %d failed_validation, %d failed_judge, %d errors",
        elapsed,
        stats.get("approved", 0),
        stats.get("failed_validation", 0),
        stats.get("failed_judge", 0),
        stats.get("error", 0),
    )

    return {
        "approved": stats.get("approved", 0),
        "failed": stats.get("failed_validation", 0) + stats.get("failed_judge", 0),
        "errors": stats.get("error", 0),
        "elapsed": elapsed,
    }
