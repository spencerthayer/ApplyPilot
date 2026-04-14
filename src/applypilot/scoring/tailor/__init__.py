"""Resume tailoring — backward compatibility re-exports."""

from applypilot.scoring.tailor.orchestrator import run_tailoring, tailor_resume, judge_tailored_resume, \
    MAX_ATTEMPTS  # noqa: F401
from applypilot.scoring.tailor.keyword_extractor import STOPWORDS, extract_jd_keywords  # noqa: F401
from applypilot.scoring.tailor.skill_gap_detector import check_skill_gaps  # noqa: F401
from applypilot.scoring.tailor.prompt_builder import build_tailor_prompt, build_judge_prompt  # noqa: F401
from applypilot.scoring.tailor.response_assembler import extract_json, normalize_bullet, \
    assemble_resume_text  # noqa: F401
from applypilot.scoring.tailor.response_assembler import strip_disallowed_watchlist_skills  # noqa: F401

# Backward-compat aliases for private names used by tests
_normalize_bullet = normalize_bullet
_strip_disallowed_watchlist_skills = strip_disallowed_watchlist_skills
_extract_jd_keywords = extract_jd_keywords
_build_tailor_prompt = build_tailor_prompt
_build_judge_prompt = build_judge_prompt

# More backward-compat aliases for tests
from applypilot.scoring.tailor.orchestrator import _build_tailored_prefix  # noqa: F401, E402

# Re-export names that tests monkeypatch on the tailor module
from applypilot.config import load_resume_text, load_profile  # noqa: F401, E402
from applypilot.config import TAILORED_DIR  # noqa: F401, E402
