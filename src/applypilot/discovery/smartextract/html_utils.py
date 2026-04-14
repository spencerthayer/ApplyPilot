"""HTML cleaning and CAPTCHA detection utilities for SmartExtract.

Single responsibility: transform raw HTML into clean input for LLM selectors.
CAPTCHA signals are centralized here instead of hardcoded in pipeline logic.
"""

import re

from bs4 import BeautifulSoup

# Attribute allowlist — keeps only semantically meaningful attrs for LLM
_ALLOWED_ATTRS = {
    "id",
    "href",
    "data-testid",
    "data-id",
    "data-type",
    "data-slug",
    "role",
    "aria-label",
    "aria-labelledby",
    "type",
    "name",
    "for",
}
_ALLOWED_PREFIXES = ("data-", "aria-")

# Pre-compiled regex for utility/generated CSS classes (stripped to reduce noise)
_UTILITY_CLASS_RE = re.compile(
    r"^("
    r"[a-z]{1,2}-\d+|"
    r"[a-z]{1,3}-[a-z]{1,3}-\d+|"
    r"col-\d+|d-\w+|align-\w+|justify-\w+|flex-\w+|order-\d+|"
    r"text-\w+|font-\w+|bg-\w+|border-\w+|rounded-?\w*|shadow-?\w*|"
    r"w-\d+|h-\d+|position-\w+|overflow-\w+|float-\w+|clearfix|"
    r"visible-\w+|invisible|sr-only|"
    r"css-[a-z0-9]+|sc-[a-zA-Z]+|sc-[a-f0-9]+-\d+"
    r")$"
)

# CAPTCHA/bot-detection signals — centralized for easy extension
CAPTCHA_SIGNALS = [
    "captcha",
    "are you a human",
    "verify you",
    "unusual requests",
    "access denied",
    "please verify",
    "bot detection",
]

# Minimum cleaned HTML size before triggering headful retry
MIN_CONTENT_THRESHOLD = 5000


def _filter_attrs(tag) -> dict:
    """Keep only semantically meaningful attributes on a tag."""
    new_attrs: dict = {}
    for attr, val in list(tag.attrs.items()):
        if attr in _ALLOWED_ATTRS or any(attr.startswith(p) for p in _ALLOWED_PREFIXES):
            new_attrs[attr] = val
        elif attr == "class":
            classes = val if isinstance(val, list) else val.split()
            kept = [c for c in classes if not _UTILITY_CLASS_RE.match(c)]
            if kept:
                new_attrs["class"] = kept
    return new_attrs


def clean_card_html(html: str) -> str:
    """Strip layout noise from card HTML, keep only what the LLM needs for selectors."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        tag.attrs = _filter_attrs(tag)
    return str(soup)


def clean_page_html(html: str, max_chars: int = 150_000) -> str:
    """Strip full page HTML to essential structure for LLM card detection."""
    soup = BeautifulSoup(html, "html.parser")

    # Scope to <main> if present — reduces noise from headers/sidebars
    main = soup.find("main") or soup.find(attrs={"role": "main"})
    if main and len(str(main)) > 1000:
        soup = BeautifulSoup(str(main), "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "svg", "noscript", "iframe", "link", "meta", "head", "footer", "nav"]):
        tag.decompose()

    # Strip noisy attributes
    for tag in soup.find_all(True):
        tag.attrs = _filter_attrs(tag)

    # Remove empty elements (no text, no images, no links)
    for tag in soup.find_all(True):
        if not tag.get_text(strip=True) and not tag.find("img") and not tag.find("a"):
            tag.decompose()

    result = str(soup)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n<!-- TRUNCATED -->"
    return result


def detect_captcha(html: str) -> bool:
    """Check if page body contains CAPTCHA/bot-detection signals.

    Checks only visible body text, not script/style tags, to avoid false
    positives from sites that embed the word 'captcha' in their JS bundles.
    """
    if not html:
        return False
    # Strip script/style content before checking — prevents false positives
    # from JS bundles that reference captcha libraries (e.g. Naukri)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    body_text = soup.get_text(" ", strip=True).lower()
    return any(s in body_text for s in CAPTCHA_SIGNALS)
