"""Title matching, role family inference, seniority detection."""

import re

__all__ = [
    "TITLE_STOPWORDS",
    "ROLE_FAMILY_PATTERNS",
    "SENIORITY_PATTERNS",
    "tokenize",
    "tokenize_set",
    "title_key",
    "infer_role_family",
    "seniority_from_text",
    "jaccard_similarity",
]

TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "of",
    "the",
    "to",
    "with",
    "at",
    "ii",
    "iii",
    "iv",
    "sr",
    "senior",
    "principal",
    "staff",
    "lead",
    "l4",
    "l5",
    "l6",
    "l7",
}

ROLE_FAMILY_PATTERNS: dict[str, tuple[str, ...]] = {
    "software_engineering": (
        r"\bsoftware\b",
        r"\bengineer\b",
        r"\bdeveloper\b",
        r"\bbackend\b",
        r"\bfront[\s-]?end\b",
        r"\bfull[\s-]?stack\b",
        r"\bplatform\b",
        r"\bdevops\b",
        r"\bsre\b",
    ),
    "data_ai": (
        r"\bdata\b",
        r"\bmachine learning\b",
        r"\bml\b",
        r"\bai\b",
        r"\bllm\b",
        r"\bresearch engineer\b",
        r"\bapplied scientist\b",
    ),
    "design": (
        r"\bdesigner\b",
        r"\bux\b",
        r"\bui\b",
        r"\bproduct design\b",
        r"\bvisual design\b",
    ),
    "marketing": (
        r"\bmarketing\b",
        r"\baudience\b",
        r"\bdemand gen\b",
        r"\bseo\b",
        r"\bcontent strategy\b",
    ),
    "sales": (
        r"\bsales\b",
        r"\baccount executive\b",
        r"\bbusiness development\b",
        r"\bsdr\b",
    ),
    "operations": (
        r"\boperations\b",
        r"\bprogram manager\b",
        r"\bproject manager\b",
    ),
    "finance": (
        r"\bfinance\b",
        r"\baccounting\b",
        r"\bcpa\b",
        r"\bcontroller\b",
    ),
}

SENIORITY_PATTERNS: list[tuple[int, tuple[str, ...]]] = [
    (0, ("intern", "internship")),
    (1, ("junior", "jr", "entry", "new grad", "graduate")),
    (2, ("engineer", "developer", "analyst", "specialist", "mid", "associate")),
    (3, ("senior", "sr", "lead")),
    (4, ("staff", "principal", "architect")),
    (5, ("manager", "head of", "director", "vp", "vice president")),
]


def tokenize(text: str) -> list[str]:
    """Tokenize text on non-alphanumeric boundaries, lowercased."""
    return re.findall(r"[a-zA-Z0-9]+", (text or "").lower())


def tokenize_set(text: str) -> set[str]:
    return {token for token in tokenize(text) if token and token not in TITLE_STOPWORDS}


def title_key(title: str) -> str:
    tokens = [token for token in tokenize(title) if token and token not in TITLE_STOPWORDS]
    if not tokens:
        return "untitled"
    return " ".join(tokens[:8])


def infer_role_family(text: str) -> str:
    haystack = (text or "").lower()
    for family, patterns in ROLE_FAMILY_PATTERNS.items():
        if any(re.search(pattern, haystack) for pattern in patterns):
            return family
    return "unknown"


def seniority_from_text(text: str) -> int:
    lowered = (text or "").lower()
    for score, terms in reversed(SENIORITY_PATTERNS):
        if any(term in lowered for term in terms):
            return score
    return 2


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)
