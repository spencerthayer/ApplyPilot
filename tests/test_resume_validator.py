"""Tests for resume_validator module.

These tests verify that all validation checks work correctly and produce
specific, actionable error messages.
"""

import pytest
from applypilot.scoring.resume_validator import (
    ResumeValidator,
    ValidationConfig,
    ValidationResult,
    check_role_completeness,
    check_project_completeness,
    check_bullet_counts,
    check_total_bullets,
    check_summary_quality,
    check_bullet_metrics,
    check_weak_verbs,
    check_education_completeness,
    validate_resume,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def sample_profile():
    """Sample profile for testing."""
    return {
        "personal": {
            "full_name": "Test User",
            "email": "test@example.com",
        },
        "work": [
            {
                "company": "Tech Corp",
                "position": "Software Engineer",
                "start_date": "2019-01-01",
                "end_date": "2023-12-31",
            },
            {
                "company": "Startup Inc",
                "position": "Senior Developer",
                "start_date": "2023-01-01",
                "end_date": "",
            },
        ],
        "education": [
            {
                "institution": "Test University",
                "studyType": "Bachelor's Degree",
                "area": "Computer Science",
                "endDate": "2018",
            }
        ],
        "projects": [
            {"name": "Project Alpha"},
            {"name": "Project Beta"},
        ],
        "experience": {
            "years_of_experience_total": "6",
            "education_level": "Bachelor's Degree",
            "target_role": "Senior Software Engineer",
        },
        "tailoring_config": {
            "validation": {
                "enabled": True,
                "max_retries": 3,
                "min_bullets_per_role": 2,
                "max_bullets_per_role": 5,
                "min_metrics_ratio": 0.7,
            }
        },
    }


@pytest.fixture
def sample_resume_data():
    """Sample valid resume data for testing."""
    return {
        "title": "Senior Software Engineer",
        "summary": "6+ years of experience building scalable systems. Bachelor's in Computer Science with track record of delivering high-impact projects.",
        "skills": ["Python", "AWS", "Kubernetes"],
        "experience": [
            {
                "company": "Tech Corp",
                "header": "Software Engineer | Tech Corp | 2019 - 2023",
                "bullets": [
                    "Built API handling 1M requests/day, reducing latency by 40%",
                    "Designed microservices architecture serving 10M users",
                    "Implemented CI/CD pipeline reducing deployment time by 60%",
                ],
            },
            {
                "company": "Startup Inc",
                "header": "Senior Developer | Startup Inc | 2023 - Present",
                "bullets": [
                    "Led team of 5 engineers delivering \$2M revenue feature",
                    "Optimized database queries improving performance by 3x",
                ],
            },
        ],
        "projects": [
            {
                "name": "Project Alpha",
                "header": "Project Alpha | 2022",
                "bullets": ["Built ML pipeline processing 1TB data daily"],
            },
        ],
        "education": ["B.S. Computer Science, Test University, 2018"],
    }


@pytest.fixture
def validation_config():
    """Default validation config."""
    return ValidationConfig()


# ── Test ValidationConfig ──────────────────────────────────────────────────


class TestValidationConfig:
    """Test ValidationConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ValidationConfig()
        assert config.enabled is True
        assert config.max_retries == 3
        assert config.min_bullets_per_role == 2
        assert config.max_bullets_per_role == 5
        assert config.min_metrics_ratio == 0.7

    def test_from_config(self):
        """Test loading from tailoring_config dict."""
        tailoring_config = {
            "validation": {
                "enabled": False,
                "max_retries": 5,
                "min_bullets_per_role": 3,
                "min_metrics_ratio": 0.8,
            }
        }
        config = ValidationConfig.from_config(tailoring_config)
        assert config.enabled is False
        assert config.max_retries == 5
        assert config.min_bullets_per_role == 3
        assert config.min_metrics_ratio == 0.8

    def test_from_config_defaults(self):
        """Test that missing values use defaults."""
        tailoring_config = {"validation": {}}
        config = ValidationConfig.from_config(tailoring_config)
        assert config.enabled is True  # default
        assert config.max_retries == 3  # default


# ── Test ValidationResult ──────────────────────────────────────────────────


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_passed_result(self):
        """Test passed validation result."""
        result = ValidationResult(
            passed=True,
            check_name="test_check",
            errors=[],
        )
        assert result.passed is True
        assert result.to_retry_prompt() == ""

    def test_failed_result(self):
        """Test failed validation result."""
        result = ValidationResult(
            passed=False,
            check_name="test_check",
            errors=["Error 1", "Error 2"],
            retry_instructions=["Fix error 1", "Fix error 2"],
        )
        prompt = result.to_retry_prompt()
        assert "Fix Required: test_check" in prompt
        assert "Error 1" in prompt
        assert "Fix error 1" in prompt

    def test_result_with_warnings(self):
        """Test result with warnings."""
        result = ValidationResult(
            passed=True,
            check_name="test_check",
            warnings=["Warning 1"],
        )
        assert result.passed is True
        assert len(result.warnings) == 1


# ── Test Role Completeness Check ───────────────────────────────────────────


class TestCheckRoleCompleteness:
    """Test check_role_completeness function."""

    def test_all_roles_present(self, sample_profile, sample_resume_data, validation_config):
        """Test when all profile roles are in resume."""
        result = check_role_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True
        assert len(result.errors) == 0

    def test_missing_role(self, sample_profile, sample_resume_data, validation_config):
        """Test when a role is missing from resume."""
        # Remove one role from resume
        sample_resume_data["experience"] = [sample_resume_data["experience"][0]]

        result = check_role_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert len(result.errors) == 1
        assert "Startup Inc" in result.errors[0]
        assert any("Add missing role" in instr for instr in result.retry_instructions)

    def test_no_work_history(self, sample_profile, sample_resume_data, validation_config):
        """Test when profile has no work entries."""
        sample_profile["work"] = []

        result = check_role_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True
        assert len(result.warnings) == 1

    def test_company_name_variations(self, sample_profile, sample_resume_data, validation_config):
        """Test fuzzy matching of company names."""
        # Change company name slightly
        sample_resume_data["experience"][0]["company"] = "Tech Corporation"

        result = check_role_completeness(sample_resume_data, sample_profile, validation_config)
        # Should pass with fuzzy matching
        assert result.passed is True


# ── Test Project Completeness Check ─────────────────────────────────────────


class TestCheckProjectCompleteness:
    """Test check_project_completeness function."""

    def test_all_projects_present(self, sample_profile, sample_resume_data, validation_config):
        """Test when all projects are in resume."""
        result = check_project_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True

    def test_missing_project(self, sample_profile, sample_resume_data, validation_config):
        """Test when a project is missing (should be warning, not error)."""
        # Remove projects
        sample_resume_data["projects"] = []

        result = check_project_completeness(sample_resume_data, sample_profile, validation_config)
        # Projects missing is a warning, not an error
        assert result.passed is True
        assert len(result.warnings) > 0

    def test_no_preserved_projects(self, sample_profile, sample_resume_data, validation_config):
        """Test when profile has no projects."""
        sample_profile["projects"] = []

        result = check_project_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True


# ── Test Bullet Counts Check ────────────────────────────────────────────────


class TestCheckBulletCounts:
    """Test check_bullet_counts function."""

    def test_valid_bullet_counts(self, sample_profile, sample_resume_data, validation_config):
        """Test when all roles have valid bullet counts."""
        result = check_bullet_counts(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True

    def test_too_few_bullets(self, sample_profile, sample_resume_data, validation_config):
        """Test when a role has too few bullets."""
        # Reduce bullets to 1
        sample_resume_data["experience"][0]["bullets"] = sample_resume_data["experience"][0]["bullets"][:1]

        result = check_bullet_counts(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "Only 1 bullet" in result.errors[0]
        assert "Add 1 more bullet" in result.retry_instructions[0]

    def test_too_many_bullets(self, sample_profile, sample_resume_data, validation_config):
        """Test when a role has too many bullets."""
        # Add more bullets
        sample_resume_data["experience"][0]["bullets"].extend(
            [
                "Extra bullet 1",
                "Extra bullet 2",
                "Extra bullet 3",
            ]
        )

        result = check_bullet_counts(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "6 bullets" in result.errors[0]
        assert "Remove 1 weakest bullet" in result.retry_instructions[0]

    def test_no_experience_section(self, sample_profile, sample_resume_data, validation_config):
        """Test when experience section is missing."""
        sample_resume_data["experience"] = []

        result = check_bullet_counts(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "No experience section" in result.errors[0]


# ── Test Total Bullets Check ────────────────────────────────────────────────


class TestCheckTotalBullets:
    """Test check_total_bullets function."""

    def test_valid_total_junior(self, sample_profile, sample_resume_data, validation_config):
        """Test valid total for junior level (<5 years)."""
        # Set years to 3 (junior level: 8-15 bullets)
        sample_profile["experience"]["years_of_experience_total"] = "3"
        # Add more bullets to meet junior minimum (8)
        sample_resume_data["experience"][0]["bullets"].extend(
            [
                "Improved test coverage by 25%",
                "Refactored legacy codebase",
                "Implemented monitoring system",
            ]
        )

        result = check_total_bullets(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True
        assert result.metadata["level"] == "junior"

    def test_valid_total_senior_with_more_bullets(self, sample_profile, sample_resume_data, validation_config):
        """Test valid total for senior level with sufficient bullets."""
        sample_profile["experience"]["years_of_experience_total"] = "12"
        # Add more bullets to meet senior minimum (15)
        for exp in sample_resume_data["experience"]:
            exp["bullets"].extend(
                [
                    "Improved system reliability by 99.9%",
                    "Mentored 3 junior engineers",
                    "Architected data pipeline handling 10TB daily",
                    "Reduced infrastructure costs by 30%",
                    "Established engineering best practices",
                ]
            )

        result = check_total_bullets(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True
        assert result.metadata["level"] == "senior"

    def test_too_few_total_senior(self, sample_profile, sample_resume_data, validation_config):
        """Test too few bullets for senior level."""
        sample_profile["experience"]["years_of_experience_total"] = "12"
        # Keep default 5 bullets - should fail senior minimum of 15

        result = check_total_bullets(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert result.metadata["level"] == "senior"
        assert "minimum for senior" in result.errors[0]

    def test_too_few_total_junior(self, sample_profile, sample_resume_data, validation_config):
        """Test too few bullets for junior level."""
        sample_profile["experience"]["years_of_experience_total"] = "2"
        # Remove bullets
        for exp in sample_resume_data["experience"]:
            exp["bullets"] = exp["bullets"][:1]

        result = check_total_bullets(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert result.metadata["level"] == "junior"
        assert "minimum for junior" in result.errors[0]

    def test_too_many_total(self, sample_profile, sample_resume_data, validation_config):
        """Test too many total bullets."""
        # Add many bullets
        for exp in sample_resume_data["experience"]:
            exp["bullets"].extend([f"Extra bullet {i}" for i in range(10)])

        result = check_total_bullets(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "maximum" in result.errors[0]


# ── Test Summary Quality Check ──────────────────────────────────────────────


class TestCheckSummaryQuality:
    """Test check_summary_quality function."""

    def test_valid_summary(self, sample_profile, sample_resume_data, validation_config):
        """Test valid summary with all required elements."""
        # Ensure summary has all required elements
        sample_resume_data["summary"] = (
            "Senior Software Engineer with 6+ years of experience building scalable systems. "
            "Bachelor's degree in Computer Science with a track record of delivering high-impact projects."
        )

        result = check_summary_quality(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True

    def test_missing_years(self, sample_profile, sample_resume_data, validation_config):
        """Test summary missing years of experience."""
        sample_resume_data["summary"] = "Software engineer with experience in Python and AWS"

        result = check_summary_quality(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "years of experience" in result.errors[0]

    def test_missing_education(self, sample_profile, sample_resume_data, validation_config):
        """Test summary missing education credential."""
        sample_resume_data["summary"] = "6+ years of experience in software engineering"

        result = check_summary_quality(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "education credential" in result.errors[0]

    def test_empty_summary(self, sample_profile, sample_resume_data, validation_config):
        """Test empty summary."""
        sample_resume_data["summary"] = ""

        result = check_summary_quality(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "empty" in result.errors[0]

    def test_summary_too_short(self, sample_profile, sample_resume_data, validation_config):
        """Test summary that is too short."""
        sample_resume_data["summary"] = "6+ years experience with bachelor's degree"

        result = check_summary_quality(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        # Should fail on multiple checks including word count
        error_messages = " ".join(result.errors)
        assert "too short" in error_messages or len(sample_resume_data["summary"].split()) < 20


# ── Test Bullet Metrics Check ───────────────────────────────────────────────


class TestCheckBulletMetrics:
    """Test check_bullet_metrics function."""

    def test_all_bullets_have_metrics(self, sample_profile, sample_resume_data, validation_config):
        """Test when all bullets have metrics."""
        result = check_bullet_metrics(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True

    def test_some_bullets_missing_metrics(self, sample_profile, sample_resume_data, validation_config):
        """Test when some bullets lack metrics."""
        # Replace one bullet without metrics
        sample_resume_data["experience"][0]["bullets"][0] = "Built API for user management"

        result = check_bullet_metrics(sample_resume_data, sample_profile, validation_config)
        # 2/3 have metrics = 67% which is below 70% threshold
        assert result.passed is False
        assert "metrics" in result.errors[0].lower()

    def test_no_metrics(self, sample_profile, sample_resume_data, validation_config):
        """Test when no bullets have metrics."""
        # Replace all bullets without metrics
        for exp in sample_resume_data["experience"]:
            exp["bullets"] = ["Built API", "Designed system", "Implemented feature"]

        result = check_bullet_metrics(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False


# ── Test Weak Verbs Check ───────────────────────────────────────────────────


class TestCheckWeakVerbs:
    """Test check_weak_verbs function."""

    def test_no_weak_verbs(self, sample_profile, sample_resume_data, validation_config):
        """Test when no bullets start with weak verbs."""
        result = check_weak_verbs(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True

    def test_weak_verb_detected(self, sample_profile, sample_resume_data, validation_config):
        """Test detection of weak verbs."""
        sample_resume_data["experience"][0]["bullets"][0] = "Responsible for API development"

        result = check_weak_verbs(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        # Check for weak verb in error (case insensitive)
        error_lower = result.errors[0].lower()
        assert "responsible for" in error_lower
        # Check for alternatives in retry instructions
        instruction_lower = result.retry_instructions[0].lower()
        assert "led" in instruction_lower or "drove" in instruction_lower

    def test_multiple_weak_verbs(self, sample_profile, sample_resume_data, validation_config):
        """Test detection of multiple weak verbs."""
        sample_resume_data["experience"][0]["bullets"][0] = "Responsible for API"
        sample_resume_data["experience"][0]["bullets"][1] = "Assisted with design"

        result = check_weak_verbs(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert len(result.errors) == 2


# ── Test Education Completeness Check ───────────────────────────────────────


class TestCheckEducationCompleteness:
    """Test check_education_completeness function."""

    def test_complete_education(self, sample_profile, sample_resume_data, validation_config):
        """Test valid education section."""
        result = check_education_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True

    def test_missing_education(self, sample_profile, sample_resume_data, validation_config):
        """Test missing education section."""
        sample_resume_data["education"] = []

        result = check_education_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "empty or missing" in result.errors[0]

    def test_missing_school(self, sample_profile, sample_resume_data, validation_config):
        """Test education missing expected school."""
        sample_resume_data["education"] = ["B.S. Computer Science, Other University, 2018"]

        result = check_education_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "Test University" in result.errors[0]

    def test_missing_degree(self, sample_profile, sample_resume_data, validation_config):
        """Test education missing degree type."""
        sample_resume_data["education"] = ["Computer Science, Test University, 2018"]

        result = check_education_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "degree type" in result.errors[0]

    def test_missing_year(self, sample_profile, sample_resume_data, validation_config):
        """Test education missing graduation year."""
        sample_resume_data["education"] = ["B.S. Computer Science, Test University"]

        result = check_education_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is False
        assert "graduation year" in result.errors[0]

    def test_dict_format_education(self, sample_profile, sample_resume_data, validation_config):
        """Test education section as dict format."""
        sample_resume_data["education"] = [
            {"degree": "B.S. Computer Science", "school": "Test University", "year": "2018"}
        ]

        result = check_education_completeness(sample_resume_data, sample_profile, validation_config)
        assert result.passed is True


# ── Test ResumeValidator Class ──────────────────────────────────────────────


class TestResumeValidator:
    """Test ResumeValidator class."""

    def test_initialization(self, sample_profile):
        """Test validator initialization."""
        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)

        assert validator.profile == sample_profile
        assert validator.validation_config.enabled is True

    def test_validate_all_checks(self, sample_profile, sample_resume_data):
        """Test running all validation checks."""
        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)

        result = validator.validate(sample_resume_data)

        assert "passed" in result
        assert "results" in result
        assert "all_errors" in result
        assert "retry_prompt" in result
        assert len(result["results"]) == len(validator.DEFAULT_CHECKS)

    def test_validate_selected_checks(self, sample_profile, sample_resume_data):
        """Test running only selected checks."""
        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)

        result = validator.validate(sample_resume_data, selected_checks=[check_bullet_counts, check_total_bullets])

        assert len(result["results"]) == 2
        assert all(r.check_name in ["bullet_counts", "total_bullets"] for r in result["results"])

    def test_validation_disabled(self, sample_profile, sample_resume_data):
        """Test when validation is disabled."""
        sample_profile["tailoring_config"]["validation"]["enabled"] = False
        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)

        result = validator.validate(sample_resume_data)

        assert result["passed"] is True
        assert len(result["results"]) == 0

    def test_retry_prompt_generation(self, sample_profile):
        """Test retry prompt generation with failures."""
        # Create invalid resume data
        invalid_data = {
            "title": "",
            "summary": "",  # Empty - will fail
            "skills": [],
            "experience": [],  # Empty - will fail
            "projects": [],
            "education": [],
        }

        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)

        result = validator.validate(invalid_data)

        assert result["passed"] is False
        assert len(result["retry_prompt"]) > 0
        assert "Fix Required:" in result["retry_prompt"]

    def test_check_metadata(self, sample_profile, sample_resume_data):
        """Test that check metadata is collected."""
        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)

        result = validator.validate(sample_resume_data)

        assert "check_metadata" in result
        # total_bullets should have metadata
        assert "total_bullets" in result["check_metadata"]


# ── Test Integration Functions ─────────────────────────────────────────────


class TestValidateResume:
    """Test convenience functions."""

    def test_validate_resume_convenience(self, sample_profile, sample_resume_data):
        """Test validate_resume convenience function."""
        result = validate_resume(sample_resume_data, sample_profile)

        assert "passed" in result
        assert "results" in result

    def test_validate_resume_with_custom_config(self, sample_profile, sample_resume_data):
        """Test validate_resume with custom config."""
        custom_config = {
            "validation": {
                "enabled": True,
                "min_bullets_per_role": 1,  # More lenient
            }
        }

        result = validate_resume(sample_resume_data, sample_profile, custom_config)
        assert "passed" in result


# ── Test Edge Cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_profile_sections(self, sample_resume_data):
        """Test with minimal profile."""
        minimal_profile = {"tailoring_config": {"validation": {"enabled": True}}}

        config = minimal_profile.get("tailoring_config", {})
        validator = ResumeValidator(minimal_profile, config)
        result = validator.validate(sample_resume_data)

        # Should handle missing sections gracefully
        assert "passed" in result

    def test_malformed_resume_data(self, sample_profile):
        """Test with malformed resume data."""
        malformed_data = {
            "experience": [
                {"company": "Test", "bullets": "not a list"},  # Wrong type
            ]
        }

        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)
        result = validator.validate(malformed_data)

        # Should handle gracefully
        assert "passed" in result

    def test_empty_strings(self, sample_profile, sample_resume_data):
        """Test with empty string values."""
        sample_resume_data["summary"] = "   "  # Whitespace only

        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)
        result = validator.validate(sample_resume_data)

        assert result["passed"] is False  # Empty summary should fail

    def test_unicode_characters(self, sample_profile, sample_resume_data):
        """Test with unicode characters in text."""
        sample_resume_data["summary"] = "6+ years of experience — built scalable systems"

        config = sample_profile.get("tailoring_config", {})
        validator = ResumeValidator(sample_profile, config)
        result = validator.validate(sample_resume_data)

        # Should handle unicode gracefully
        assert "passed" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
