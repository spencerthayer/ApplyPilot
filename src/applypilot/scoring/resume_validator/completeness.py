"""Completeness checks — roles, projects, education, bullet counts."""

from __future__ import annotations

import re
from typing import Any

from applypilot.resume.extraction import get_profile_project_names, get_profile_school_names
from applypilot.scoring.resume_validator.models import ValidationConfig, ValidationResult


def _year_from_date(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"^\s*(\d{4})", text)
    return match.group(1) if match else ""


def check_role_completeness(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify every role in profile.work appears in resume output.

    Prevents missing entire roles (e.g., "MDA role from 2010-2011").
    """
    errors = []
    retry_instructions = []

    # Extract roles from profile
    profile_roles = []
    for role in profile.get("work", []):
        company = str(role.get("company", "")).strip()
        position = str(role.get("position", "")).strip()
        start_year = _year_from_date(role.get("start_date"))
        end_year = _year_from_date(role.get("end_date"))

        if company:
            profile_roles.append(
                {
                    "company": company,
                    "position": position,
                    "start_year": start_year,
                    "end_year": end_year or "Present",
                }
            )

    if not profile_roles:
        # No work history to validate against
        return ValidationResult(
            passed=True,
            check_name="role_completeness",
            warnings=["No work entries found in profile to validate against"],
        )

    # Extract companies from resume output
    resume_companies = set()
    for exp in resume_data.get("experience", []):
        # Check 'company' field
        company = str(exp.get("company", "")).strip().lower()
        if company:
            resume_companies.add(company)

        # Check 'header' field (format: "Position | Company | Dates")
        header = str(exp.get("header", "")).strip()
        if "|" in header:
            parts = header.split("|")
            if len(parts) >= 2:
                header_company = parts[1].strip().lower()
                if header_company:
                    resume_companies.add(header_company)

    # Find missing roles (fuzzy match)
    missing = []
    for role in profile_roles:
        company_lower = role["company"].lower()

        # Check for exact or partial match
        found = any(company_lower == rc or company_lower in rc or rc in company_lower for rc in resume_companies)

        if not found:
            missing.append(role)

    if missing:
        for role in missing:
            years = f"{role['start_year']}-{role['end_year']}" if role["start_year"] else "dates unknown"
            errors.append(f"Missing role: {role['company']} ({role['position']}, {years})")
            retry_instructions.append(
                f"Add missing role to EXPERIENCE section: {role['company']} - {role['position']} "
                f"({role['start_year'] or 'Start Year'} - {role['end_year']})"
            )

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="role_completeness",
        errors=errors,
        retry_instructions=retry_instructions,
        metadata={
            "profile_roles": len(profile_roles),
            "resume_roles": len(resume_companies),
            "missing_count": len(missing),
        },
    )


def check_project_completeness(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify projects from profile appear in resume output.

    Projects may be intentionally filtered out if not relevant to the role,
    so this returns warnings rather than errors unless configured strictly.
    """
    errors = []
    warnings = []
    retry_instructions = []

    # Get preserved projects from profile
    preserved_projects = get_profile_project_names(profile)

    if not preserved_projects:
        return ValidationResult(
            passed=True,
            check_name="project_completeness",
            warnings=["No projects found in profile"],
        )

    # Extract projects from resume
    resume_projects = set()
    for proj in resume_data.get("projects", []):
        # Check 'name' field
        name = str(proj.get("name", "")).strip().lower()
        if name:
            resume_projects.add(name)

        # Check 'header' field (format: "Project Name | Date")
        header = str(proj.get("header", "")).strip()
        if "|" in header:
            proj_name = header.split("|")[0].strip().lower()
            if proj_name:
                resume_projects.add(proj_name)

    # Find missing projects (fuzzy match)
    missing = []
    for pp in preserved_projects:
        pp_lower = pp.strip().lower()

        # Check for exact or partial match
        found = any(pp_lower == rp or pp_lower in rp or rp in pp_lower for rp in resume_projects)

        if not found:
            missing.append(pp)

    if missing:
        # Projects can be intentionally omitted, so use warnings
        for proj in missing:
            warnings.append(f"Project '{proj}' not found in resume (may be intentionally omitted)")

        # Still provide retry instructions
        for proj in missing[:3]:  # Limit to first 3
            retry_instructions.append(f"Add project '{proj}' to PROJECTS section with 2-3 relevant bullets")

    return ValidationResult(
        passed=len(errors) == 0,  # Projects missing is a warning, not an error
        check_name="project_completeness",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
        metadata={
            "profile_projects": len(preserved_projects),
            "resume_projects": len(resume_projects),
            "missing_count": len(missing),
        },
    )


def check_bullet_counts(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify each role has bullets within configured range.

    Prevents both sparse roles (too few bullets) and bloated roles (too many).
    """
    errors = []
    warnings = []
    retry_instructions = []

    experience = resume_data.get("experience", [])

    if not experience:
        errors.append("No experience section found in resume")
        return ValidationResult(
            passed=False,
            check_name="bullet_counts",
            errors=errors,
            retry_instructions=["Add experience section with work history and bullets"],
        )

    for i, exp in enumerate(experience):
        # Get company name for error messages
        company = exp.get("company", "").strip()
        if not company and "|" in str(exp.get("header", "")):
            parts = str(exp.get("header", "")).split("|")
            if len(parts) >= 2:
                company = parts[1].strip()
        if not company:
            company = f"Role {i + 1}"

        bullets = exp.get("bullets", [])

        if not isinstance(bullets, list):
            errors.append(f"{company}: bullets field is not a list")
            retry_instructions.append(f"Fix {company}: bullets must be a list of strings")
            continue

        count = len(bullets)

        # Check minimum
        if count < config.min_bullets_per_role:
            errors.append(f"{company}: Only {count} bullet(s) (minimum: {config.min_bullets_per_role})")
            retry_instructions.append(
                f"Add {config.min_bullets_per_role - count} more bullet(s) for {company}. "
                f"Focus on quantified achievements relevant to the target role."
            )

        # Check maximum
        elif count > config.max_bullets_per_role:
            errors.append(f"{company}: {count} bullets (maximum: {config.max_bullets_per_role})")
            retry_instructions.append(
                f"Remove {count - config.max_bullets_per_role} weakest bullet(s) from {company}. "
                f"Keep only the highest-impact, most relevant achievements."
            )

        # Warn if only minimum
        elif count == config.min_bullets_per_role:
            warnings.append(f"{company}: Exactly at minimum ({count} bullets). Consider adding more if relevant.")

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="bullet_counts",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
    )


def check_total_bullets(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify total bullet count is appropriate for seniority level.

    Uses years_of_experience_total from profile to determine appropriate range.
    """
    errors = []
    retry_instructions = []

    # Calculate total bullets
    total = sum(len(exp.get("bullets", [])) for exp in resume_data.get("experience", []))

    # Determine thresholds based on years of experience
    years_exp_str = str(profile.get("experience", {}).get("years_of_experience_total", "0"))
    try:
        years_exp = int(years_exp_str)
    except (ValueError, TypeError):
        years_exp = 0

    if years_exp >= 10:
        min_total = config.min_total_bullets_senior
        max_total = config.max_total_bullets_senior
        level = "senior"
    elif years_exp >= 5:
        min_total = config.min_total_bullets_mid
        max_total = config.max_total_bullets_mid
        level = "mid-level"
    else:
        min_total = config.min_total_bullets_junior
        max_total = config.max_total_bullets_junior
        level = "junior"

    if total < min_total:
        errors.append(f"Total bullets: {total} (minimum for {level}: {min_total})")
        retry_instructions.append(
            f"Add {min_total - total} more bullets across roles. "
            f"Aim for {min_total}-{max_total} total bullets for {level} professionals."
        )
    elif total > max_total:
        errors.append(f"Total bullets: {total} (maximum for {level}: {max_total})")
        retry_instructions.append(
            f"Remove {total - max_total} bullets to stay concise. "
            f"Focus on highest-impact achievements. Target {min_total}-{max_total} total."
        )

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="total_bullets",
        errors=errors,
        retry_instructions=retry_instructions,
        metadata={
            "total_bullets": total,
            "years_experience": years_exp,
            "level": level,
            "min_threshold": min_total,
            "max_threshold": max_total,
        },
    )


def check_education_completeness(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify education section has complete information.

    Checks for school name, degree type, and graduation year.
    """
    errors = []
    warnings = []
    retry_instructions = []

    education_list = resume_data.get("education", [])

    if not education_list:
        errors.append("Education section is empty or missing")
        retry_instructions.append("Add education section with institution name, degree, and graduation year")
        return ValidationResult(
            passed=False,
            check_name="education_completeness",
            errors=errors,
            retry_instructions=retry_instructions,
        )

    # Get expected school from profile
    schools = get_profile_school_names(profile)
    preserved_school = schools[0] if schools else ""

    for i, edu in enumerate(education_list):
        entry_num = i + 1

        # Handle both string and dict formats
        if isinstance(edu, str):
            edu_text = edu
            edu_lower = edu.lower()
        elif isinstance(edu, dict):
            edu_text = " ".join(str(v) for v in edu.values())
            edu_lower = edu_text.lower()
        else:
            errors.append(f"Education entry #{entry_num}: Invalid format")
            continue

        # Check 1: School name (if we have expected school)
        if preserved_school:
            school_lower = preserved_school.lower()
            if school_lower not in edu_lower:
                errors.append(f"Education entry #{entry_num}: Missing expected school '{preserved_school}'")
                retry_instructions.append(f"Include school name in education entry #{entry_num}: {preserved_school}")

        # Check 2: Degree type
        degree_patterns = [
            r"\b(b\.s\.?|b\.a\.?|b\.e\.?|b\.tech|bachelor)",
            r"\b(m\.s\.?|m\.a\.?|m\.b\.a\.?|m\.e\.?|master|mba)",
            r"\b(ph\.?d\.?|doctorate|doctoral)",
            r"\b(associate|a\.a\.?|a\.s\.?)",
        ]
        has_degree = any(re.search(p, edu_lower) for p in degree_patterns)

        if not has_degree:
            errors.append(f"Education entry #{entry_num}: Missing degree type")
            retry_instructions.append(f"Add degree type to education entry #{entry_num} (e.g., B.S., M.S., Ph.D.)")

        # Check 3: Graduation year
        has_year = re.search(r"\b(19|20)\d{2}\b", edu_text)
        if not has_year:
            errors.append(f"Education entry #{entry_num}: Missing graduation year")
            retry_instructions.append(f"Add graduation year to education entry #{entry_num} (e.g., 2015)")

    return ValidationResult(
        passed=len(errors) == 0,
        check_name="education_completeness",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
        metadata={
            "education_entries": len(education_list),
            "expected_school": preserved_school,
        },
    )
