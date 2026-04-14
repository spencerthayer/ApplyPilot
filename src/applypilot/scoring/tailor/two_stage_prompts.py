"""Two-stage tailoring pipeline: Planner → Generator.

Stage 1 (Planner): Analyzes JD requirements, maps each to resume evidence,
  identifies gaps, decides bullet strategy. Uses mid-tier model (reasoning).

Stage 2 (Generator): Takes the plan + resume + JD and generates the final
  tailored resume. Uses premium model (writing quality).

This outperforms single-prompt tailoring because:
- Planner isolates reasoning from generation (no mode-switching)
- Generator gets a structured plan, not open-ended instructions
- Each model does what it's best at
"""

from __future__ import annotations

PLANNER_PROMPT = """\
You are a resume tailoring strategist. Map each JD requirement to evidence \
from the candidate's resume.

## CANDIDATE: {yoe} years experience, currently {current_role} at {current_company}

## RESUME
{resume_text}

## JOB DESCRIPTION
{jd_title} at {jd_company}

{jd_text}

## TASK
Return a JSON plan:

```json
{{
  "requirements": [
    {{"req": "JD requirement text", "bullet": "matching resume bullet or null", "role": "company name", "gap": false}}
  ],
  "current_role_bullets_to_keep": ["bullet1", "bullet2"],
  "past_role_bullets_to_keep": ["bullet1", "bullet2"],
  "top_skills": ["skill1", "skill2", "skill3"],
  "summary_metrics": ["metric1", "metric2"],
  "tone": "3-5yr ownership: independent delivery, production impact, system design"
}}
```

RULES:
- Map EVERY must-have JD requirement to a resume bullet
- If no bullet matches, mark gap=true
- Keep ALL bullets that match ANY JD requirement
- Do NOT invent bullets — only reference exact text from resume
- Return ONLY JSON"""

GENERATOR_PROMPT = """\
You are a professional resume writer. Generate a tailored resume following \
the plan exactly.

## TAILORING PLAN (from strategist)
{plan_json}

## BASE RESUME
{resume_text}

## CANDIDATE INFO
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}
Profiles: {profiles}

## OUTPUT RULES

1. FORMAT: Return ONLY a valid JSON object matching this schema:
```json
{{
  "title": "JD-matched title",
  "summary": "2 sentences: ownership-level intro + measurable impact",
  "skills": {{
    "Category Name": "skill1, skill2, skill3"
  }},
  "experience": [
    {{
      "header": "Title | Company | StartDate - EndDate/Present",
      "bullets": [
        {{"text": "Action verb + task + method + measurable result", "skills": ["S1", "S2"]}}
      ]
    }}
  ],
  "education": "Institution | Degree | Field | Year"
}}
```

2. EXPERIENCE ORDER: Current role FIRST (present tense), then past roles (past tense)

3. BULLET RULES:
- Follow the plan's bullets_to_keep — include ALL of them
- Rewrite each bullet in STAR format: Action + Context + Method + Result
- Preserve ALL metrics exactly as they appear in base resume
- Each bullet: 12-22 words, starts with strong action verb
- Spell out acronyms on first use

4. CROSS-ROLE INTEGRITY:
- Each bullet traces to ONE role only
- Technologies stay with their original company
- Do NOT merge facts across roles

5. SKILLS SECTION:
- Prioritize skills from plan's top_skills_for_jd
- Group logically (Languages, Backend, DevOps, etc.)
- Only include skills that exist in the base resume

6. SUMMARY:
- Reflect the plan's summary_strategy
- Show ownership level matching the YOE tone
- Include specific metrics from the plan

7. NO FABRICATION:
- No new companies, tools, metrics, or achievements
- Banned words: {banned_words}

Return ONLY the JSON."""
