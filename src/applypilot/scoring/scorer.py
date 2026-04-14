"""Job fit scoring — backward compatibility re-exports.

All logic has been decomposed into:
  - scoring/deterministic/  (title_matcher, skill_overlap, exclusion_gate, baseline_scorer, job_context_extractor)
  - scoring/llm/            (prompt_builder, calibrator, response_parser)
  - scoring/orchestrator.py  (run_scoring, score_job, trace/telemetry)

This file re-exports everything for backward compatibility with existing imports.
"""

# Re-export from orchestrator (the main entry points)
from applypilot.scoring.orchestrator import (  # noqa: F401
    run_scoring,
    score_job,
    evaluate_exclusion,
    MAX_SCORE_ATTEMPTS_PER_JOB,
    SCORE_ATTEMPT_BACKOFF_SECONDS,
)

# Re-export from deterministic modules
from applypilot.scoring.deterministic.title_matcher import (  # noqa: F401
    TITLE_STOPWORDS,
    ROLE_FAMILY_PATTERNS,
    SENIORITY_PATTERNS,
    tokenize,
    tokenize_set,
    title_key,
    infer_role_family,
    seniority_from_text,
    jaccard_similarity,
)
from applypilot.scoring.deterministic.skill_overlap import (  # noqa: F401
    SKILL_PATTERNS,
    extract_known_skills,
    contains_phrase,
)
from applypilot.scoring.deterministic.exclusion_gate import (  # noqa: F401
    EXCLUSION_RULES,
)
from applypilot.scoring.deterministic.job_context_extractor import (  # noqa: F401
    JOB_CONTEXT_PRIORITIES,
    extract_requirement_focused_text,
)
from applypilot.scoring.deterministic.baseline_scorer import (  # noqa: F401
    HARD_MISMATCH_TERMS,
    compute_deterministic_baseline,
    build_scoring_profile,
    load_scoring_profile,
)

# Re-export from LLM modules
from applypilot.scoring.llm.prompt_builder import (  # noqa: F401
    SCORE_PROMPT,
    SCORING_RESPONSE_FORMAT,
    format_scoring_profile_for_prompt,
)
from applypilot.scoring.llm.calibrator import (  # noqa: F401
    ScoreResponseParseError,
    extract_json_object,
    parse_score_response,
    has_hard_mismatch_evidence,
    apply_score_calibration,
)

# Backward-compat aliases for private names used by tests
_tokenize = tokenize
_title_key = title_key
_exclusion_result = None  # re-export from exclusion_gate
_load_target_title_keywords = None  # re-export from exclusion_gate

from applypilot.scoring.deterministic.exclusion_gate import (  # noqa: F401, E402
    exclusion_result as _exclusion_result,
    load_target_title_keywords as _load_target_title_keywords,
)

# More backward-compat aliases for tests
_parse_score_response = parse_score_response
_extract_json_object = extract_json_object
_apply_score_calibration = apply_score_calibration
_compute_deterministic_baseline = compute_deterministic_baseline
_build_scoring_profile = build_scoring_profile
_load_scoring_profile = load_scoring_profile
_format_scoring_profile_for_prompt = format_scoring_profile_for_prompt
_has_hard_mismatch_evidence = has_hard_mismatch_evidence
_SKILL_PATTERNS = SKILL_PATTERNS
_TITLE_STOPWORDS = TITLE_STOPWORDS
_HARD_MISMATCH_TERMS = HARD_MISMATCH_TERMS

# Re-export names tests access via scorer module
from applypilot.config import load_resume_text  # noqa: F401, E402
from applypilot.scoring.orchestrator import (  # noqa: F401, E402
    _load_scoring_resume_text,
    _score_telemetry_summary,
    _classify_score_outcome,
    _compose_score_reasoning,
    _autoheal_legacy_llm_failures,
)
from applypilot.llm import get_client  # noqa: F401, E402
