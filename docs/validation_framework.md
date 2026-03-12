# Resume Validation Framework

A deterministic, countable validation system for resume tailoring that ensures output quality through automated checks.

## Overview

The validation framework catches common issues that slip through manual review:

- **Missing roles** - Every role from your profile must appear in output
- **Missing projects** - Projects are flagged if omitted
- **Bullet count issues** - Enforces min/max bullets per role (default: 2-5)
- **Summary quality** - Ensures years, education, and role keywords present
- **Unquantified bullets** - Requires metrics (%, $, x, time) in 70%+ of bullets
- **Weak verbs** - Detects "Responsible for", "Assisted with" and suggests alternatives
- **Education completeness** - Verifies school, degree, and graduation year

## Quick Start

Add to your `profile.json`:

```json
{
  "tailoring_config": {
    "validation": {
      "enabled": true,
      "max_retries": 3,
      
      "min_bullets_per_role": 2,
      "max_bullets_per_role": 5,
      
      "min_total_bullets_senior": 15,
      "max_total_bullets_senior": 25,
      "min_total_bullets_mid": 12,
      "max_total_bullets_mid": 20,
      "min_total_bullets_junior": 8,
      "max_total_bullets_junior": 15,
      
      "min_metrics_ratio": 0.7,
      
      "weak_verbs": [
        "responsible for",
        "assisted with",
        "helped with",
        "worked on",
        "involved in",
        "participated in",
        "contributed to"
      ]
    }
  }
}
```

## Usage

### Basic Validation

```python
from applypilot.scoring.resume_validator import validate_resume

result = validate_resume(resume_data, profile)

if not result["passed"]:
    print(result["retry_prompt"])
```

### With Retry Loop

```python
from applypilot.scoring.resume_validator import validate_resume_with_retry

def regenerate_resume(data, retry_prompt):
    # Your LLM call here with retry_prompt
    return new_resume_data

result = validate_resume_with_retry(
    resume_data, 
    profile, 
    regenerate_resume,
    max_retries=3
)

if result["success"]:
    final_resume = result["resume_data"]
else:
    print(f"Failed after {len(result['attempts'])} attempts")
```

### Manual Validation

```python
from applypilot.scoring.resume_validator import ResumeValidator

validator = ResumeValidator(profile, config)

# Run all checks
result = validator.validate(resume_data)

# Run specific checks only
from applypilot.scoring.resume_validator import (
    check_role_completeness,
    check_bullet_counts
)

result = validator.validate(
    resume_data,
    selected_checks=[check_role_completeness, check_bullet_counts]
)
```

## Validation Checks

### 1. Role Completeness (`check_role_completeness`)

**Purpose**: Ensure all roles from profile appear in resume

**Logic**:
- Extract companies from `profile.work`
- Extract companies from `resume_data.experience`
- Fuzzy match company names
- Flag missing roles with specific dates

**Error Example**:
```
Missing role: Startup Inc (Senior Developer, 2023-Present)
```

**Retry Instruction**:
```
Add missing role to EXPERIENCE section: Startup Inc - Senior Developer (2023 - Present)
```

### 2. Project Completeness (`check_project_completeness`)

**Purpose**: Warn if canonical projects are missing

**Logic**:
- Check project names from `profile.projects`
- Match against `resume_data.projects`
- Returns warning (not error) since projects can be intentionally omitted

### 3. Bullet Counts (`check_bullet_counts`)

**Purpose**: Enforce per-role bullet limits

**Logic**:
- Count bullets per role
- Error if < min or > max

**Configuration**:
```json
{
  "min_bullets_per_role": 2,
  "max_bullets_per_role": 5
}
```

### 4. Total Bullets (`check_total_bullets`)

**Purpose**: Ensure total bullet count appropriate for seniority

**Logic**:
- Uses `profile.experience.years_of_experience_total`
- Senior (10+ years): 15-25 bullets
- Mid (5-9 years): 12-20 bullets
- Junior (<5 years): 8-15 bullets

### 5. Summary Quality (`check_summary_quality`)

**Purpose**: Verify summary includes key credentials

**Checks**:
- Years of experience mentioned
- Education credential present
- Target role keywords included
- Length 30-50 words

### 6. Bullet Metrics (`check_bullet_metrics`)

**Purpose**: Ensure bullets are quantified

**Metric Patterns**:
- `\d+%` - Percentages
- `\$\d` - Dollar amounts
- `\d+x` - Multipliers
- Time expressions (hours, days, etc.)
- Scale (users, customers, requests)

**Threshold**: 70% of bullets must have metrics (configurable)

### 7. Weak Verbs (`check_weak_verbs`)

**Purpose**: Detect weak opening verbs

**Weak → Strong Mapping**:
- "Responsible for" → "Led", "Drove", "Owned"
- "Assisted with" → "Supported", "Enabled"
- "Helped with" → "Delivered", "Facilitated"
- "Worked on" → "Built", "Developed"

### 8. Education Completeness (`check_education_completeness`)

**Purpose**: Verify education section has required fields

**Checks**:
- School name matches the primary institution in `profile.education`
- Degree type present (B.S., M.S., Ph.D., etc.)
- Graduation year included

## Integration with Tailoring Pipeline

The validation framework integrates with `tailoring_gates.py`:

```python
from applypilot.scoring.tailoring_gates import gate_final_assembly

# After resume assembly
result = gate_final_assembly(resume_data, gate_config, profile)

if not result.passed:
    for suggestion in result.retry_suggestions:
        print(suggestion)
```

## Configuration Reference

### Complete Configuration Schema

```json
{
  "tailoring_config": {
    "validation": {
      "enabled": true,
      "max_retries": 3,
      
      "min_bullets_per_role": 2,
      "max_bullets_per_role": 5,
      
      "min_total_bullets_senior": 15,
      "max_total_bullets_senior": 25,
      "min_total_bullets_mid": 12,
      "max_total_bullets_mid": 20,
      "min_total_bullets_junior": 8,
      "max_total_bullets_junior": 15,
      
      "min_metrics_ratio": 0.7,
      
      "weak_verbs": [
        "responsible for",
        "assisted with",
        "helped with",
        "worked on",
        "involved in",
        "participated in",
        "contributed to"
      ],
      
      "metric_patterns": [
        "\\d+%",
        "\\$\\d",
        "\\d+x",
        "\\d+\\s*(?:hours?|days?|weeks?|months?|years?)",
        "\\d+\\s*(?:k|k\\+|million|m)?\\s+(?:users?|customers?|requests?)"
      ]
    }
  }
}
```

## Retry Mechanism

When validation fails, the framework generates specific retry prompts:

```
## Fix Required: bullet_counts

### Issues Found:
- Tech Corp: Only 1 bullet(s) (minimum: 2)

### Specific Instructions:
- Add 1 more bullet(s) for Tech Corp. Focus on quantified achievements 
  relevant to the target role.

---

## Fix Required: summary_quality

### Issues Found:
- Summary missing years of experience (6+ years)

### Specific Instructions:
- Add years of experience to summary: '6+ years of experience in...'
```

## Testing

Run the test suite:

```bash
uv run pytest tests/test_resume_validator.py -v
```

## Troubleshooting

### False Positives on Company Names

If company name fuzzy matching is too strict:

```python
# In your check function, adjust the matching logic
# Current: exact match or substring match
# You can customize by overriding check_role_completeness
```

### Metrics Not Detected

If legitimate metrics aren't being detected:

```json
{
  "validation": {
    "metric_patterns": [
      "\\d+%",
      "\\$\\d",
      "\\d+x",
      // Add custom patterns
      "\\d+\\s*percent",
      "halved",
      "doubled"
    ]
  }
}
```

### Too Strict on Junior Resumes

Adjust thresholds:

```json
{
  "validation": {
    "min_total_bullets_junior": 6,
    "min_metrics_ratio": 0.5
  }
}
```

## Architecture

```
ResumeValidator
├── ValidationConfig (settings)
├── ValidationResult (per-check output)
└── validate()
    ├── check_role_completeness
    ├── check_project_completeness
    ├── check_bullet_counts
    ├── check_total_bullets
    ├── check_summary_quality
    ├── check_bullet_metrics
    ├── check_weak_verbs
    └── check_education_completeness
```

Each check is a pure function:
- Input: resume_data, profile, config
- Output: ValidationResult
- No side effects
- Deterministic output
