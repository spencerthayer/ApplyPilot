"""Quality checks — summary quality, bullet metrics, weak verbs."""

from __future__ import annotations

import re

from applypilot.scoring.resume_validator.models import ValidationConfig, ValidationResult


def check_summary_quality(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify summary includes key credentials and keywords.

    Checks for:
    - Years of experience mentioned
    - Education level included
    - Target role keywords present
    """
    errors = []
    retry_instructions = []
    warnings = []

    summary = str(resume_data.get("summary", "")).strip()

    if not summary:
        errors.append("Summary section is empty")
        retry_instructions.append("Add a professional summary section (3-4 lines)")
        return ValidationResult(
            passed=False,
            check_name="summary_quality",
            errors=errors,
            retry_instructions=retry_instructions,
        )

    summary_lower = summary.lower()

    # Check 1: Years of experience
    years_exp = str(profile.get("experience", {}).get("years_of_experience_total", ""))
    if years_exp and years_exp != "0":
        # Look for years mentioned (e.g., "8+ years", "10 years", "over 5 years")
        year_patterns = [
            rf"\b{years_exp}\+?\s*years?\b",
            rf"\bover\s+{years_exp}\s*years?\b",
            rf"\bmore\s+than\s+{years_exp}\s*years?\b",
        ]
        has_years = any(re.search(p, summary_lower) for p in year_patterns)

        if not has_years:
            errors.append(f"Summary missing years of experience ({years_exp}+ years)")
            retry_instructions.append(f"Add years of experience to summary: '{years_exp}+ years of experience in...'")

    # Check 2: Education level mentioned
    edu_level = str(profile.get("experience", {}).get("education_level", "")).strip()
    if edu_level:
        edu_keywords = {
            "bachelor": ["b.s.", "b.a.", "bachelor", "bs ", "ba ", "undergraduate"],
            "master": ["m.s.", "m.a.", "m.b.a.", "master", "mba", "ms ", "ma ", "graduate"],
            "phd": ["ph.d", "phd", "doctorate", "doctoral", "ph.d."],
        }

        edu_lower = edu_level.lower()
        has_edu = False

        for degree_type, keywords in edu_keywords.items():
            if degree_type in edu_lower:
                has_edu = any(kw in summary_lower for kw in keywords)
                if has_edu:
                    break

        if not has_edu:
            errors.append(f"Summary missing education credential ({edu_level})")
            retry_instructions.append(
                f"Add education credential to summary: '{edu_level} in...' or 'with a {edu_level}...'"
            )

    # Check 3: Target role keywords
    target_role = str(profile.get("experience", {}).get("target_role", "")).strip()
    job_context = profile.get("job_context", {})
    job_title = str(job_context.get("title", target_role)).strip()

    if job_title:
        # Extract meaningful keywords (exclude generic terms)
        generic_terms = {
            "senior",
            "junior",
            "lead",
            "principal",
            "staff",
            "the",
            "a",
            "an",
            "and",
            "or",
            "in",
            "of",
            "to",
            "for",
        }

        key_terms = [word for word in job_title.lower().split() if word not in generic_terms and len(word) > 3]

        missing_terms = [term for term in key_terms if term not in summary_lower]

        if missing_terms and len(missing_terms) == len(key_terms):
            # None of the key terms found
            errors.append(f"Summary missing role keywords from '{job_title}'")
            retry_instructions.append(f"Include these keywords in summary: {', '.join(key_terms[:3])}")
        elif missing_terms:
            # Some terms missing - just warn
            warnings.append(f"Summary could include more role keywords: {', '.join(missing_terms[:2])}")

    # Check 4: Summary length
    word_count = len(summary.split())
    if word_count < 20:
        errors.append(f"Summary too short ({word_count} words). Aim for 30-50 words.")
        retry_instructions.append("Expand summary to 2-4 sentences highlighting key credentials")
    elif word_count > 80:
        warnings.append(f"Summary is long ({word_count} words). Consider condensing to 50-60 words.")

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="summary_quality",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
        metadata={
            "summary_length": word_count,
            "years_experience": years_exp,
            "education_level": edu_level,
            "target_role": job_title,
        },
    )


def check_bullet_metrics(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify bullets contain quantified metrics (% $ x time).

    Tracks the ratio of bullets with metrics per role.
    """
    errors = []
    warnings = []
    retry_instructions = []

    experience = resume_data.get("experience", [])

    if not experience:
        return ValidationResult(
            passed=True,
            check_name="bullet_metrics",
            warnings=["No experience section to check"],
        )

    for exp in experience:
        # Get company name
        company = exp.get("company", "").strip()
        if not company and "|" in str(exp.get("header", "")):
            parts = str(exp.get("header", "")).split("|")
            if len(parts) >= 2:
                company = parts[1].strip()
        if not company:
            company = "Unknown Role"

        bullets = exp.get("bullets", [])
        if not bullets:
            continue

        metrics_count = 0
        weak_bullets = []

        for i, bullet in enumerate(bullets):
            if not isinstance(bullet, str):
                continue

            # Check for any metric pattern
            has_metric = any(re.search(pattern, bullet, re.IGNORECASE) for pattern in config.metric_patterns)

            if has_metric:
                metrics_count += 1
            else:
                weak_bullets.append((i, bullet[:60] + "..." if len(bullet) > 60 else bullet))

        if bullets:
            ratio = metrics_count / len(bullets)

            if ratio < config.min_metrics_ratio:
                errors.append(
                    f"{company}: Only {metrics_count}/{len(bullets)} bullets have metrics "
                    f"({ratio:.0%} < {config.min_metrics_ratio:.0%} required)"
                )

                # Format weak bullets for instructions
                bullet_examples = "; ".join([f"#{i + 1}: '{b[:40]}...'" for i, b in weak_bullets[:3]])

                retry_instructions.append(
                    f"For {company}, add metrics (%, $, x, time) to these bullets: {bullet_examples}. "
                    f"Example: 'Reduced latency by 40%' or 'Processed $2M in transactions'"
                )
            elif ratio < 1.0:
                # Some bullets lack metrics - warn but don't fail
                warnings.append(
                    f"{company}: {len(weak_bullets)} bullet(s) lack metrics. "
                    f"Consider adding quantified impact where possible."
                )

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="bullet_metrics",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
    )


def check_weak_verbs(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Detect and flag bullets starting with weak verbs.

    Weak verbs like "Responsible for", "Assisted with" lack impact.
    Suggests strong alternatives.
    """
    errors = []
    warnings = []
    retry_instructions = []

    # Map weak verbs to strong alternatives
    strong_alternatives = {
        "responsible for": ["Led", "Drove", "Owned", "Managed", "Directed"],
        "assisted with": ["Supported", "Enabled", "Accelerated", "Facilitated"],
        "helped with": ["Delivered", "Enabled", "Facilitated", "Contributed to"],
        "worked on": ["Built", "Developed", "Implemented", "Created"],
        "involved in": ["Led", "Drove", "Spearheaded", "Guided"],
        "participated in": ["Collaborated on", "Partnered to", "Jointly delivered"],
        "contributed to": ["Delivered", "Drove", "Accelerated", "Advanced"],
    }

    experience = resume_data.get("experience", [])

    for exp in experience:
        # Get company name
        company = exp.get("company", "").strip()
        if not company and "|" in str(exp.get("header", "")):
            parts = str(exp.get("header", "")).split("|")
            if len(parts) >= 2:
                company = parts[1].strip()
        if not company:
            company = "Unknown Role"

        bullets = exp.get("bullets", [])

        for i, bullet in enumerate(bullets):
            if not isinstance(bullet, str):
                continue

            bullet_lower = bullet.lower().strip()

            # Check for weak verbs at start of bullet
            for weak_verb in config.weak_verbs:
                if bullet_lower.startswith(weak_verb):
                    errors.append(f"{company} bullet #{i + 1}: Starts with weak verb '{weak_verb}'")

                    alternatives = strong_alternatives.get(weak_verb, ["Use a strong action verb"])
                    retry_instructions.append(
                        f"Replace '{weak_verb}' in {company} bullet #{i + 1} with: {', '.join(alternatives[:3])}"
                    )
                    break  # Only flag first weak verb per bullet

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="weak_verbs",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
    )
