"""Deterministic validation framework for resume tailoring.

This module provides comprehensive, countable validation checks for tailored resumes.
Every check produces a measurable pass/fail result with specific retry instructions.

The validator compares structured resume output against profile data to ensure:
- All roles from profile appear in output
- Bullet counts are within configured ranges
- Summary includes key credentials
- Every bullet has quantified metrics
- No weak verbs or banned phrases

Usage:
    >>> from applypilot.scoring.resume_validator import ResumeValidator
    >>> validator = ResumeValidator(profile, config)
    >>> result = validator.validate(resume_data)
    >>> if not result["passed"]:
    ...     print(result["retry_prompt"])
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ── Data Classes ───────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """Result of a single validation check.
    
    Attributes:
        passed: Whether the check passed (True) or failed (False).
        check_name: Human-readable name of the check.
        errors: List of specific error messages.
        warnings: List of warning messages (non-blocking).
        retry_instructions: Specific instructions for fixing failures.
        metadata: Additional check-specific data.
    """
    passed: bool
    check_name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retry_instructions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_retry_prompt(self) -> str:
        """Convert validation failures to specific retry instructions."""
        if self.passed:
            return ""
        
        lines = [
            f"## Fix Required: {self.check_name}",
            "",
            "### Issues Found:",
        ]
        for error in self.errors:
            lines.append(f"- {error}")
        
        if self.warnings:
            lines.extend(["", "### Warnings:"])
            for warning in self.warnings:
                lines.append(f"- {warning}")
        
        lines.extend(["", "### Specific Instructions:"])
        for instruction in self.retry_instructions:
            lines.append(f"- {instruction}")
        
        return "\n".join(lines)


@dataclass 
class ValidationConfig:
    """Configuration for validation checks.
    
    Loaded from profile.tailoring_config.validation
    """
    enabled: bool = True
    max_retries: int = 3
    
    # Bullet count thresholds
    min_bullets_per_role: int = 2
    max_bullets_per_role: int = 5
    min_total_bullets_senior: int = 15
    max_total_bullets_senior: int = 25
    min_total_bullets_mid: int = 12
    max_total_bullets_mid: int = 20
    min_total_bullets_junior: int = 8
    max_total_bullets_junior: int = 15
    
    # Quality thresholds
    min_metrics_ratio: float = 0.7
    
    # Weak verbs to detect
    weak_verbs: list[str] = field(default_factory=lambda: [
        "responsible for",
        "assisted with", 
        "helped with",
        "worked on",
        "involved in",
        "participated in",
        "contributed to",
    ])
    
    # Metric patterns to detect
    metric_patterns: list[str] = field(default_factory=lambda: [
        r'\d+%',           # Percentages
        r'\$\d',           # Dollar amounts
        r'\d+x',           # Multipliers
        r'\d+\s*(?:hours?|days?|weeks?|months?|years?)',  # Time
        r'\d+\s*(?:k|k\+|million|m|billion|b)?\s+(?:users?|customers?|requests?|transactions?)',  # Scale
    ])
    
    @classmethod
    def from_config(cls, config: dict) -> "ValidationConfig":
        """Create ValidationConfig from tailoring_config dict."""
        validation_config = config.get("validation", {})
        
        return cls(
            enabled=validation_config.get("enabled", True),
            max_retries=validation_config.get("max_retries", 3),
            min_bullets_per_role=validation_config.get("min_bullets_per_role", 2),
            max_bullets_per_role=validation_config.get("max_bullets_per_role", 5),
            min_total_bullets_senior=validation_config.get("min_total_bullets_senior", 15),
            max_total_bullets_senior=validation_config.get("max_total_bullets_senior", 25),
            min_total_bullets_mid=validation_config.get("min_total_bullets_mid", 12),
            max_total_bullets_mid=validation_config.get("max_total_bullets_mid", 20),
            min_total_bullets_junior=validation_config.get("min_total_bullets_junior", 8),
            max_total_bullets_junior=validation_config.get("max_total_bullets_junior", 15),
            min_metrics_ratio=validation_config.get("min_metrics_ratio", 0.7),
            weak_verbs=validation_config.get("weak_verbs", cls().weak_verbs),
            metric_patterns=validation_config.get("metric_patterns", cls().metric_patterns),
        )


# ── Individual Validation Checks ───────────────────────────────────────────


def check_role_completeness(resume_data: dict, profile: dict, config: ValidationConfig) -> ValidationResult:
    """Verify every role in profile.work_history appears in resume output.
    
    Prevents missing entire roles (e.g., "MDA role from 2010-2011").
    """
    errors = []
    retry_instructions = []
    
    # Extract roles from profile
    profile_roles = []
    for role in profile.get("work_history", []):
        company = str(role.get("company", "")).strip()
        position = str(role.get("position", "")).strip()
        start_year = role.get("start_year", "")
        end_year = role.get("end_year", "")
        
        if company:
            profile_roles.append({
                "company": company,
                "position": position,
                "start_year": start_year,
                "end_year": end_year or "Present",
            })
    
    if not profile_roles:
        # No work history to validate against
        return ValidationResult(
            passed=True,
            check_name="role_completeness",
            warnings=["No work_history found in profile to validate against"],
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
        found = any(
            company_lower == rc or company_lower in rc or rc in company_lower
            for rc in resume_companies
        )
        
        if not found:
            missing.append(role)
    
    if missing:
        for role in missing:
            years = f"{role['start_year']}-{role['end_year']}" if role['start_year'] else "dates unknown"
            errors.append(
                f"Missing role: {role['company']} ({role['position']}, {years})"
            )
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
        }
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
    preserved_projects = profile.get("resume_facts", {}).get("preserved_projects", [])
    
    if not preserved_projects:
        return ValidationResult(
            passed=True,
            check_name="project_completeness",
            warnings=["No preserved_projects found in profile"],
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
        found = any(
            pp_lower == rp or pp_lower in rp or rp in pp_lower
            for rp in resume_projects
        )
        
        if not found:
            missing.append(pp)
    
    if missing:
        # Projects can be intentionally omitted, so use warnings
        for proj in missing:
            warnings.append(f"Project '{proj}' not found in resume (may be intentionally omitted)")
        
        # Still provide retry instructions
        for proj in missing[:3]:  # Limit to first 3
            retry_instructions.append(
                f"Add project '{proj}' to PROJECTS section with 2-3 relevant bullets"
            )
    
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
        }
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
            errors.append(
                f"{company}: Only {count} bullet(s) (minimum: {config.min_bullets_per_role})"
            )
            retry_instructions.append(
                f"Add {config.min_bullets_per_role - count} more bullet(s) for {company}. "
                f"Focus on quantified achievements relevant to the target role."
            )
        
        # Check maximum
        elif count > config.max_bullets_per_role:
            errors.append(
                f"{company}: {count} bullets (maximum: {config.max_bullets_per_role})"
            )
            retry_instructions.append(
                f"Remove {count - config.max_bullets_per_role} weakest bullet(s) from {company}. "
                f"Keep only the highest-impact, most relevant achievements."
            )
        
        # Warn if only minimum
        elif count == config.min_bullets_per_role:
            warnings.append(
                f"{company}: Exactly at minimum ({count} bullets). Consider adding more if relevant."
            )
    
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
    total = sum(
        len(exp.get("bullets", []))
        for exp in resume_data.get("experience", [])
    )
    
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
        errors.append(
            f"Total bullets: {total} (minimum for {level}: {min_total})"
        )
        retry_instructions.append(
            f"Add {min_total - total} more bullets across roles. "
            f"Aim for {min_total}-{max_total} total bullets for {level} professionals."
        )
    elif total > max_total:
        errors.append(
            f"Total bullets: {total} (maximum for {level}: {max_total})"
        )
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
        }
    )


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
            rf'\b{years_exp}\+?\s*years?\b',
            rf'\bover\s+{years_exp}\s*years?\b',
            rf'\bmore\s+than\s+{years_exp}\s*years?\b',
        ]
        has_years = any(re.search(p, summary_lower) for p in year_patterns)
        
        if not has_years:
            errors.append(f"Summary missing years of experience ({years_exp}+ years)")
            retry_instructions.append(
                f"Add years of experience to summary: '{years_exp}+ years of experience in...'"
            )
    
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
            "senior", "junior", "lead", "principal", "staff",
            "the", "a", "an", "and", "or", "in", "of", "to", "for"
        }
        
        key_terms = [
            word for word in job_title.lower().split()
            if word not in generic_terms and len(word) > 3
        ]
        
        missing_terms = [term for term in key_terms if term not in summary_lower]
        
        if missing_terms and len(missing_terms) == len(key_terms):
            # None of the key terms found
            errors.append(f"Summary missing role keywords from '{job_title}'")
            retry_instructions.append(
                f"Include these keywords in summary: {', '.join(key_terms[:3])}"
            )
        elif missing_terms:
            # Some terms missing - just warn
            warnings.append(
                f"Summary could include more role keywords: {', '.join(missing_terms[:2])}"
            )
    
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
        }
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
            has_metric = any(
                re.search(pattern, bullet, re.IGNORECASE)
                for pattern in config.metric_patterns
            )
            
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
                bullet_examples = "; ".join([
                    f"#{i+1}: '{b[:40]}...'" for i, b in weak_bullets[:3]
                ])
                
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
                    errors.append(
                        f"{company} bullet #{i+1}: Starts with weak verb '{weak_verb}'"
                    )
                    
                    alternatives = strong_alternatives.get(weak_verb, ["Use a strong action verb"])
                    retry_instructions.append(
                        f"Replace '{weak_verb}' in {company} bullet #{i+1} with: "
                        f"{', '.join(alternatives[:3])}"
                    )
                    break  # Only flag first weak verb per bullet
    
    return ValidationResult(
        passed=len(errors) == 0,
        check_name="weak_verbs",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
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
        retry_instructions.append(
            "Add education section with institution name, degree, and graduation year"
        )
        return ValidationResult(
            passed=False,
            check_name="education_completeness",
            errors=errors,
            retry_instructions=retry_instructions,
        )
    
    # Get expected school from profile
    preserved_school = profile.get("resume_facts", {}).get("preserved_school", "")
    
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
                errors.append(
                    f"Education entry #{entry_num}: Missing expected school '{preserved_school}'"
                )
                retry_instructions.append(
                    f"Include school name in education entry #{entry_num}: {preserved_school}"
                )
        
        # Check 2: Degree type
        degree_patterns = [
            r'\b(b\.s\.?|b\.a\.?|b\.e\.?|b\.tech|bachelor)',
            r'\b(m\.s\.?|m\.a\.?|m\.b\.a\.?|m\.e\.?|master|mba)',
            r'\b(ph\.?d\.?|doctorate|doctoral)',
            r'\b(associate|a\.a\.?|a\.s\.?)',
        ]
        has_degree = any(re.search(p, edu_lower) for p in degree_patterns)
        
        if not has_degree:
            errors.append(f"Education entry #{entry_num}: Missing degree type")
            retry_instructions.append(
                f"Add degree type to education entry #{entry_num} (e.g., B.S., M.S., Ph.D.)"
            )
        
        # Check 3: Graduation year
        has_year = re.search(r'\b(19|20)\d{2}\b', edu_text)
        if not has_year:
            errors.append(f"Education entry #{entry_num}: Missing graduation year")
            retry_instructions.append(
                f"Add graduation year to education entry #{entry_num} (e.g., 2015)"
            )
    
    return ValidationResult(
        passed=len(errors) == 0,
        check_name="education_completeness",
        errors=errors,
        warnings=warnings,
        retry_instructions=retry_instructions,
        metadata={
            "education_entries": len(education_list),
            "expected_school": preserved_school,
        }
    )


# ── Main Validator Class ───────────────────────────────────────────────────


class ResumeValidator:
    """Deterministic validation framework for tailored resumes.
    
    Runs a suite of validation checks against resume data and generates
    specific retry instructions for any failures.
    
    Example:
        >>> validator = ResumeValidator(profile, config)
        >>> result = validator.validate(resume_data)
        >>> if not result["passed"]:
        ...     print(result["retry_prompt"])
    """
    
    # Default set of validation checks
    DEFAULT_CHECKS: list[Callable] = [
        check_role_completeness,
        check_project_completeness,
        check_bullet_counts,
        check_total_bullets,
        check_summary_quality,
        check_bullet_metrics,
        check_weak_verbs,
        check_education_completeness,
    ]
    
    def __init__(self, profile: dict, config: dict):
        """Initialize validator with profile and tailoring config.
        
        Args:
            profile: User profile dict from load_profile().
            config: Tailoring configuration dict from profile.tailoring_config.
        """
        self.profile = profile
        self.config = config
        self.validation_config = ValidationConfig.from_config(config)
    
    def validate(
        self,
        resume_data: dict,
        selected_checks: Optional[list[Callable]] = None
    ) -> dict[str, Any]:
        """Run all or selected validation checks.
        
        Args:
            resume_data: The resume data to validate (structured JSON format).
            selected_checks: Optional list of specific check functions to run.
                           If None, runs all DEFAULT_CHECKS.
        
        Returns:
            Dict with validation results:
            {
                "passed": bool,
                "results": list[ValidationResult],
                "all_errors": list[str],
                "all_warnings": list[str],
                "retry_prompt": str,
                "failed_checks": list[str],
                "check_metadata": dict[str, dict],
            }
        """
        if not self.validation_config.enabled:
            log.debug("Validation disabled in config")
            return {
                "passed": True,
                "results": [],
                "all_errors": [],
                "all_warnings": [],
                "retry_prompt": "",
                "failed_checks": [],
                "check_metadata": {},
            }
        
        checks = selected_checks or self.DEFAULT_CHECKS
        results = []
        
        for check_func in checks:
            try:
                result = check_func(resume_data, self.profile, self.validation_config)
                results.append(result)
            except Exception as e:
                log.exception(f"Validation check {check_func.__name__} failed")
                results.append(ValidationResult(
                    passed=False,
                    check_name=check_func.__name__,
                    errors=[f"Check failed with error: {str(e)}"],
                    retry_instructions=["Review resume structure and fix formatting issues"]
                ))
        
        # Aggregate results
        all_passed = all(r.passed for r in results)
        all_errors = [e for r in results for e in r.errors]
        all_warnings = [w for r in results for w in r.warnings]
        
        # Generate combined retry prompt
        failed_results = [r for r in results if not r.passed]
        retry_sections = [r.to_retry_prompt() for r in failed_results if r.retry_instructions]
        retry_prompt = "\n\n---\n\n".join(retry_sections) if retry_sections else ""
        
        # Collect metadata
        check_metadata = {r.check_name: r.metadata for r in results if r.metadata}
        
        return {
            "passed": all_passed,
            "results": results,
            "all_errors": all_errors,
            "all_warnings": all_warnings,
            "retry_prompt": retry_prompt,
            "failed_checks": [r.check_name for r in failed_results],
            "check_metadata": check_metadata,
        }
    
    def validate_with_retry(
        self,
        resume_data: dict,
        tailoring_func: Callable[[dict, str], dict],
        max_retries: Optional[int] = None
    ) -> dict[str, Any]:
        """Validate and retry if failed, up to max_retries.
        
        This is the main entry point for validation with automatic retry.
        
        Args:
            resume_data: Initial resume data to validate.
            tailoring_func: Function that takes (current_data, retry_prompt) 
                          and returns new resume data.
            max_retries: Maximum retry attempts. Uses config default if None.
        
        Returns:
            Dict with final result and attempt history:
            {
                "success": bool,
                "resume_data": dict,
                "attempts": list[dict],
                "final_validation": dict,
                "exhausted": bool,
            }
        """
        max_retries = max_retries or self.validation_config.max_retries
        attempts = []
        current_data = resume_data
        
        for attempt in range(max_retries + 1):
            # Run validation
            validation = self.validate(current_data)
            
            attempts.append({
                "attempt": attempt,
                "passed": validation["passed"],
                "error_count": len(validation["all_errors"]),
                "warning_count": len(validation["all_warnings"]),
                "failed_checks": validation["failed_checks"],
            })
            
            if validation["passed"]:
                log.info(f"Validation passed on attempt {attempt}")
                return {
                    "success": True,
                    "resume_data": current_data,
                    "attempts": attempts,
                    "final_validation": validation,
                    "exhausted": False,
                }
            
            if attempt >= max_retries:
                log.warning(f"Validation failed after {max_retries} retries")
                break
            
            # Retry with specific feedback
            retry_prompt = validation["retry_prompt"]
            if not retry_prompt:
                log.warning("Validation failed but no retry instructions generated")
                break
            
            log.info(f"Validation failed on attempt {attempt}, retrying with feedback")
            log.debug(f"Retry prompt:\n{retry_prompt}")
            
            try:
                current_data = tailoring_func(current_data, retry_prompt)
            except Exception as e:
                log.exception(f"Tailoring retry failed on attempt {attempt + 1}")
                attempts.append({
                    "attempt": attempt + 1,
                    "error": str(e),
                    "passed": False,
                })
                break
        
        return {
            "success": False,
            "resume_data": current_data,
            "attempts": attempts,
            "final_validation": validation,
            "exhausted": True,
        }


# ── Convenience Functions ─────────────────────────────────────────────────


def validate_resume(
    resume_data: dict,
    profile: dict,
    config: Optional[dict] = None
) -> dict[str, Any]:
    """Convenience function for one-off validation.
    
    Args:
        resume_data: Resume data to validate.
        profile: User profile dict.
        config: Optional tailoring config. Uses profile.tailoring_config if None.
    
    Returns:
        Validation result dict.
    """
    if config is None:
        config = profile.get("tailoring_config", {})
    
    validator = ResumeValidator(profile, config)
    return validator.validate(resume_data)


def validate_resume_with_retry(
    resume_data: dict,
    profile: dict,
    tailoring_func: Callable[[dict, str], dict],
    config: Optional[dict] = None,
    max_retries: Optional[int] = None
) -> dict[str, Any]:
    """Convenience function for validation with retry.
    
    Args:
        resume_data: Initial resume data.
        profile: User profile dict.
        tailoring_func: Function to regenerate resume with retry feedback.
        config: Optional tailoring config.
        max_retries: Maximum retry attempts.
    
    Returns:
        Validation result with retry history.
    """
    if config is None:
        config = profile.get("tailoring_config", {})
    
    validator = ResumeValidator(profile, config)
    return validator.validate_with_retry(resume_data, tailoring_func, max_retries)
