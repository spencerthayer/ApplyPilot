"""Skill pattern matching and overlap detection."""

import re

__all__ = [
    "SKILL_PATTERNS",
    "contains_phrase",
    "extract_known_skills",
]

SKILL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python", re.compile(r"\bpython\b", re.IGNORECASE)),
    ("java", re.compile(r"\bjava\b", re.IGNORECASE)),
    ("javascript", re.compile(r"\bjavascript\b|\bnode\.?js\b", re.IGNORECASE)),
    ("typescript", re.compile(r"\btypescript\b", re.IGNORECASE)),
    ("c#", re.compile(r"\bc#\b|\bcsharp\b|\b\.net\b|\basp\.?net\b", re.IGNORECASE)),
    ("c++", re.compile(r"\bc\+\+\b", re.IGNORECASE)),
    ("go", re.compile(r"\bgolang\b", re.IGNORECASE)),
    ("rust", re.compile(r"\brust\b", re.IGNORECASE)),
    ("ruby", re.compile(r"\bruby\b", re.IGNORECASE)),
    ("php", re.compile(r"\bphp\b", re.IGNORECASE)),
    ("scala", re.compile(r"\bscala\b", re.IGNORECASE)),
    ("kotlin", re.compile(r"\bkotlin\b", re.IGNORECASE)),
    ("swift", re.compile(r"\bswift\b", re.IGNORECASE)),
    ("react", re.compile(r"\breact\b", re.IGNORECASE)),
    ("angular", re.compile(r"\bangular\b", re.IGNORECASE)),
    ("vue", re.compile(r"\bvue(?:\.js)?\b", re.IGNORECASE)),
    ("next.js", re.compile(r"\bnext\.?js\b", re.IGNORECASE)),
    ("django", re.compile(r"\bdjango\b", re.IGNORECASE)),
    ("flask", re.compile(r"\bflask\b", re.IGNORECASE)),
    ("fastapi", re.compile(r"\bfastapi\b", re.IGNORECASE)),
    ("spring", re.compile(r"\bspring\b", re.IGNORECASE)),
    ("rails", re.compile(r"\brails\b", re.IGNORECASE)),
    ("graphql", re.compile(r"\bgraphql\b", re.IGNORECASE)),
    ("rest api", re.compile(r"\brest(?:ful)?\b|\bapi\b", re.IGNORECASE)),
    ("microservices", re.compile(r"\bmicroservices?\b", re.IGNORECASE)),
    ("sql", re.compile(r"\bsql\b|\bpostgres\b|\bmysql\b", re.IGNORECASE)),
    ("nosql", re.compile(r"\bnosql\b|\bmongodb\b|\bredis\b|\bcassandra\b", re.IGNORECASE)),
    ("aws", re.compile(r"\baws\b|\bamazon web services\b", re.IGNORECASE)),
    ("gcp", re.compile(r"\bgcp\b|\bgoogle cloud\b", re.IGNORECASE)),
    ("azure", re.compile(r"\bazure\b", re.IGNORECASE)),
    ("docker", re.compile(r"\bdocker\b", re.IGNORECASE)),
    ("kubernetes", re.compile(r"\bkubernetes\b|\bk8s\b", re.IGNORECASE)),
    ("terraform", re.compile(r"\bterraform\b", re.IGNORECASE)),
    ("ci/cd", re.compile(r"\bci/?cd\b|\bjenkins\b|\bgithub actions\b", re.IGNORECASE)),
    ("spark", re.compile(r"\bspark\b", re.IGNORECASE)),
    ("hadoop", re.compile(r"\bhadoop\b", re.IGNORECASE)),
    ("airflow", re.compile(r"\bairflow\b", re.IGNORECASE)),
    ("machine learning", re.compile(r"\bmachine learning\b|\bml\b", re.IGNORECASE)),
    ("deep learning", re.compile(r"\bdeep learning\b", re.IGNORECASE)),
    ("tensorflow", re.compile(r"\btensorflow\b", re.IGNORECASE)),
    ("pytorch", re.compile(r"\bpytorch\b", re.IGNORECASE)),
    ("llm", re.compile(r"\bllm\b|\blarge language model\b|\bgenerative ai\b", re.IGNORECASE)),
    ("nlp", re.compile(r"\bnlp\b|\bnatural language processing\b", re.IGNORECASE)),
]


def contains_phrase(text_lower: str, phrase: str) -> bool:
    candidate = phrase.lower().strip()
    if not candidate:
        return False
    if re.search(r"[+#./]", candidate):
        return candidate in text_lower
    pattern = r"\b" + re.escape(candidate).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, text_lower) is not None


def extract_known_skills(text: str) -> set[str]:
    found: set[str] = set()
    for canonical, pattern in SKILL_PATTERNS:
        if pattern.search(text):
            found.add(canonical)
    return found
