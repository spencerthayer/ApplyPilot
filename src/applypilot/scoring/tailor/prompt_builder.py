"""Prompt builders for resume tailoring and LLM judge evaluation."""

from __future__ import annotations

import logging

from applypilot.resume.extraction import (
    get_profile_company_names,
    get_profile_school_names,
    get_profile_skill_keywords,
    get_profile_skill_sections,
    get_profile_verified_metrics,
)
from applypilot.scoring.validator import BANNED_WORDS

log = logging.getLogger(__name__)

__all__ = ["build_education_block", "build_tailor_prompt", "build_judge_prompt"]


def build_education_block(education_list: list[dict]) -> str:
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


def build_tailor_prompt(profile: dict, resume_text: str | None = None) -> str:
    """Build the resume tailoring system prompt from the user's profile.

    Prompt structure: constraints first, context second, example last.
    Tighter prompts = less room for the model to go off-script.
    Tested across 8 models (Opus->Llama 8B) -- all produce correct output.
    """
    del resume_text

    # -- Extract profile data --
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

    education_block = build_education_block(profile.get("education", []))
    if education_block == "N/A":
        education_level = profile.get("experience", {}).get("education_level", "")
        if education_level:
            education_block = f"{school} | {education_level}" if school else education_level

    # -- Bullet budget --
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
        raw_max = min(max(per_exp), 8) if per_exp else 6
        raw_min = max(min(per_exp), 4) if per_exp else 4  # minimum 4 bullets per past role
        max_bullets = raw_max
        min_bullets = min(raw_min, raw_max)

    # -- Build prompt: constraints first, context second, example last --
    target_role = profile.get("target_role", "")
    companies_str = ", ".join(companies) if companies else "N/A"
    max_total_bullets = max_bullets + min_bullets * max(role_count - 1, 0)

    # -- Track framing: what employers buy for this role type --
    framing_instruction = ""
    try:
        from applypilot.services.track_framing import get_framing

        framing = get_framing(target_role)
        if framing:
            framing_instruction = f"\n## FRAMING GUIDANCE\nFrame the candidate as: {framing}\n"
    except Exception:
        pass

    # -- Resume preset: section order + page budget for job type --
    preset_instruction = ""
    try:
        from applypilot.config.resume_rendering import resolve_config

        rc = resolve_config(job={"title": target_role})
        if rc.max_pages != "auto":
            preset_instruction += f"\nTarget page count: {rc.max_pages}"
        default_order = ["summary", "experience", "skills", "projects", "education", "certificates"]
        if rc.section_order != default_order:
            preset_instruction += f"\nSection order: {', '.join(rc.section_order)}"
    except Exception:
        pass

    return f"""You are a senior recruiter rewriting a resume for a specific job description (JD).

Return ONLY a valid JSON object. No markdown, no commentary.
{framing_instruction}{preset_instruction}
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
- Most recent role: {max_bullets} bullets (prioritize JD-relevant achievements)
- All other roles: AT LEAST {min_bullets} bullets (include ALL bullets that match JD requirements)
- If a past role has bullets matching JD requirements, KEEP THEM — do not cut for brevity

3. JD REQUIREMENT COVERAGE (CRITICAL)
- Extract ALL required skills/technologies from the JD
- For EACH JD requirement, ensure at least one bullet demonstrates that skill
- If the base resume has a bullet matching a JD requirement, it MUST appear in the output
- Missing JD coverage is worse than a longer resume

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
- Sentence 1: role-appropriate intro showing ownership level matching {yoe if yoe else "3-5"} YOE + strongest JD-relevant skills (from resume only)
- Sentence 2: concrete measurable impact with specific metrics from resume
- For 3-5 YOE: show independent delivery capability, not just "experience with"
  Good: "Backend engineer who independently designed and shipped production DDD systems using Python and Flask, reducing costs by 75%"
  Bad: "Software engineer with 4 years of experience in Python and Flask"

11. TITLE
- Must match {target_role if target_role else "the target role from JD"}
- Maintain original seniority level

12. STYLE RULES
- No em dashes
- No banned words: {banned_str}
- Strong action verbs
- Concise and direct
- Spell out acronyms on first use: "Domain-Driven Design (DDD)", "Server-Driven UI (SDUI)"

13. LOCATION
- Include candidate location from base resume in the header
- Do NOT omit location

## VOICE (STRICT)

- Tone: professional, direct, results-oriented
- Sentence length: 12-22 words per bullet
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

## CROSS-ROLE INTEGRITY (CRITICAL)

- NEVER merge facts, technologies, or achievements from different roles into one bullet
- Each bullet must trace back to ONE specific role in the base resume
- If a technology was used at Company A, do NOT attribute it to Company B
- Cloud providers are company-specific: preserve which cloud was used at which company
  Example: if GCP was used at iServeU and AWS at Amazon, do NOT list both under one role
- Client deployments are separate: do not combine multiple deployment targets into one bullet
- Metrics are role-specific: do NOT move a metric from one role to another

## EXPERIENCE ORDERING (CRITICAL)

- Roles MUST be in reverse chronological order: current/most recent role FIRST
- Current role (no end date) always appears before past roles
- Within past roles, most recent start date first

## BULLET RETENTION (CRITICAL)

- Do NOT drop bullets that match JD requirements just to meet a bullet count
- If the base resume has 12 bullets for a role and 7 match the JD, keep all 7
- Prefer MORE relevant bullets over fewer polished ones
- Every JD requirement that has a matching bullet in the base resume MUST appear

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
- YOE determines resume depth and tone:
  - 0-2 years: focus on learning, contributions, technologies used
  - 3-5 years: focus on ownership, independent delivery, measurable impact, system design decisions
  - 5-8 years: focus on architecture, mentoring, cross-team impact, technical leadership
  - 8+ years: focus on org-wide impact, strategy, team building, technical vision
- Bullets must reflect the candidate's ACTUAL level of ownership — if they independently built a system, say "Independently designed and built", not "Worked on"
- For {yoe if yoe else "3-5"} YOE: emphasize end-to-end ownership, production impact, and technical decision-making

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

{{"title":"Role Title","location":"City, Country","summary":"Sentence one with YOE. Sentence two with metrics.","skills":{{"Languages":"...","Frameworks":"..."}},"experience":[{{"header":"Title at Company","subtitle":"Tech | Dates","bullets":[{{"text":"...","skills":["S1","S2"]}}]}},{{"header":"Title at Company 2","subtitle":"Tech | Dates","bullets":[{{"text":"...","skills":["S1"]}}]}}],"projects":[{{"header":"Name","subtitle":"Tech","bullets":[{{"text":"...","skills":["S1"]}}]}}],"education":"{education_block}"}}"""


def build_judge_prompt(profile: dict) -> str:
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
