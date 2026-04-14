"""Resume and cover letter validation — re-exports from decomposed modules."""

from applypilot.scoring.validator.banned_words import (  # noqa: F401
    BANNED_WORDS,
    LLM_LEAK_PHRASES,
    FABRICATION_WATCHLIST,
    REQUIRED_SECTIONS,
    MECHANISM_VERBS,
)
from applypilot.scoring.validator.sanitizer import (  # noqa: F401
    sanitize_text,
    tokenize_words as _tokenize_words,
    build_skills_set as _build_skills_set,
)
from applypilot.scoring.validator.deviation_guard import check_resume_deviation  # noqa: F401
from applypilot.scoring.validator.structural_checks import (  # noqa: F401
    validate_json_fields,
    validate_cover_letter,
)
from applypilot.scoring.validator.fabrication_detector import (  # noqa: F401
    validate_tailored_resume,
)
