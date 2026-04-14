"""Deterministic exclusion gate — pre-LLM filtering."""

from __future__ import annotations

import logging
import re

from applypilot.scoring.deterministic.title_matcher import tokenize

log = logging.getLogger(__name__)

__all__ = [
    "EXCLUSION_RULES",
    "load_user_exclusion_rules",
    "exclusion_result",
    "load_target_title_keywords",
    "evaluate_exclusion",
]

# ── Deterministic Exclusion Gate ──────────────────────────────────────────
# Hardcoded exclusion rules aligned with task-8 contract semantics.
# Future: load from config/rules.yaml per the contract schema.

EXCLUSION_RULES: list[dict] = [
    {
        "id": "r-001",
        "type": "keyword",
        "value": ["intern", "internship"],
        "match_scope": "title",
        "match_type": "exact",
        "reason_code": "excluded_keyword",
        "description": "Exclude internship positions",
    },
    {
        "id": "r-002",
        "type": "keyword",
        "value": ["clearance"],
        "match_scope": "title+description",
        "match_type": "exact",
        "reason_code": "excluded_keyword",
        "description": "Exclude positions requiring security clearance",
    },
]


def load_user_exclusion_rules() -> list[dict]:
    """Load exclude_titles from searches.yaml and convert to exclusion rules.

    ADDED: The hardcoded EXCLUSION_RULES only had 2 entries (intern, clearance).
    The user's searches.yaml has a richer exclude_titles list (VP, director, etc.)
    that was only applied during JobSpy discovery — not during scoring. This means
    Greenhouse/Workday jobs with excluded titles still got scored by the LLM,
    wasting ~$0.04/job on Opus. Now they're excluded deterministically.
    """
    try:
        from applypilot.config import load_search_config

        cfg = load_search_config()
        titles = cfg.get("exclude_titles", [])
        if not titles:
            return []
        return [
            {
                "id": "r-user-exclude",
                "type": "keyword",
                "value": [t.strip().lower() for t in titles if t.strip()],
                "match_scope": "title",
                "match_type": "substring",
                "reason_code": "excluded_title",
                "description": "User exclude_titles from searches.yaml",
            }
        ]
    except Exception:
        return []


def exclusion_result(rule: dict, matched_value: str) -> dict:
    """Build a blocked scoring result for an excluded job."""

    reason_code = rule["reason_code"]
    log.debug("[score] EXCLUDED: rule=%s reason=%s matched='%s'", rule["id"], reason_code, matched_value)
    return {
        "score": 0,
        "keywords": "",
        "reasoning": f"EXCLUDED: {reason_code} — matched '{matched_value}' (rule {rule['id']})",
        "exclusion_reason_code": reason_code,
        "exclusion_rule_id": rule["id"],
    }


def load_target_title_keywords() -> tuple[set[str], list[str]]:
    """Load target role phrases and keywords from searches.yaml queries.

    Returns (single_keywords, phrases). Generic words are derived from data:
    any word appearing in >50% of queries is too common to distinguish roles
    (e.g. "engineer" appears in 12/16 queries) and is excluded from keywords.
    Phrase matching still catches them (e.g. "software engineer" as a whole).
    """
    _EXPANSIONS = {
        "ml": "machine learning",
        "sde": "software development engineer",
        "sre": "site reliability engineer",
        "sdk": "software development kit",
    }

    try:
        from collections import Counter
        from applypilot.config import load_search_config

        cfg = load_search_config()
        queries = cfg.get("queries", [])
        if not queries:
            return set(), []

        phrases: list[str] = []
        # Count how many queries each word appears in
        word_freq: Counter = Counter()
        query_words: list[list[str]] = []

        for q in queries:
            phrase = q.get("query", "").strip().lower()
            if not phrase:
                continue
            phrases.append(phrase)
            for abbr, expansion in _EXPANSIONS.items():
                if abbr in phrase.split():
                    phrases.append(phrase.replace(abbr, expansion))
            words = [w for w in phrase.split() if len(w) >= 2]
            query_words.append(words)
            for w in set(words):
                word_freq[w] += 1

        # Words in >50% of queries are generic — they don't help distinguish roles
        threshold = len(queries) * 0.5
        keywords: set[str] = set()
        for words in query_words:
            for w in words:
                if word_freq[w] <= threshold:
                    keywords.add(w)

        return keywords, phrases
    except Exception:
        return set(), []


def evaluate_exclusion(job: dict) -> dict | None:
    """Evaluate deterministic exclusion rules against a job.

    Two-pass filter:
    1. Negative: exclude jobs matching exclude_titles (VP, intern, etc.)
    2. Positive: skip jobs with zero title overlap with user's search queries
    """

    title = job.get("title") or ""
    description = job.get("full_description") or job.get("description") or ""
    site = job.get("site") or ""

    title_tokens = tokenize(title)
    desc_tokens = tokenize(description)
    combined_tokens = title_tokens + desc_tokens

    # CHANGED: Merge hardcoded rules with user's exclude_titles from searches.yaml.
    # This ensures Greenhouse/Workday jobs with excluded titles (VP, director, etc.)
    # are skipped before the LLM call, saving token costs.
    all_rules = EXCLUSION_RULES + load_user_exclusion_rules()

    for rule in all_rules:
        values = rule["value"]
        if isinstance(values, str):
            values = [values]

        match_scope = rule.get("match_scope", "title+description")
        match_type = rule.get("match_type", "exact")

        if match_scope == "site":
            field_lower = site.lower()
            for val in values:
                val_lower = val.lower()
                if match_type == "substring" and val_lower in field_lower:
                    return exclusion_result(rule, val)
                if match_type == "exact" and val_lower == field_lower:
                    return exclusion_result(rule, val)
            continue

        if match_scope == "title":
            tokens = title_tokens
        elif match_scope == "description":
            tokens = desc_tokens
        else:
            tokens = combined_tokens

        for val in values:
            val_lower = val.lower()
            if match_type == "exact":
                if val_lower in tokens:
                    return exclusion_result(rule, val)
            elif match_type == "prefix":
                if any(token.startswith(val_lower) for token in tokens):
                    return exclusion_result(rule, val)
            elif match_type == "substring":
                # ADDED: Word-boundary substring match for multi-word phrases
                # (e.g. "senior director", "vice president" from exclude_titles).
                # Uses \b to avoid "intern" matching "international".
                raw = (
                    title.lower()
                    if match_scope == "title"
                    else (description.lower() if match_scope == "description" else f"{title} {description}".lower())
                )
                pattern = r"\b" + re.escape(val_lower).replace(r"\ ", r"\s+") + r"\b"
                if re.search(pattern, raw):
                    return exclusion_result(rule, val)

    # Pass 2: Positive relevance check — skip jobs with zero title overlap
    # with user's target roles. Prevents wasting LLM tokens on jobs like
    # "Account Manager" when user searches for "Software Engineer".
    target_keywords, target_phrases = load_target_title_keywords()
    if (target_keywords or target_phrases) and title:
        title_lower = title.lower()
        # Skip relevance check for jobs with missing/generic titles —
        # these may be valid jobs with bad metadata, let the LLM decide.
        if title_lower in ("", "unknown role", "unknown", "n/a", "none"):
            return None
        # Check phrase match first (e.g. "software engineer" in title)
        phrase_match = any(p in title_lower for p in target_phrases)
        if not phrase_match:
            # Fall back to distinctive keyword match (e.g. "android", "devops", "java")
            title_words = {w for w in tokenize(title) if len(w) >= 3}
            keyword_match = bool(title_words & target_keywords)
            if not keyword_match:
                return exclusion_result(
                    {
                        "id": "r-relevance",
                        "reason_code": "no_title_overlap",
                        "description": "Title has no overlap with target roles",
                    },
                    f"title='{title[:40]}'",
                )

    return None
