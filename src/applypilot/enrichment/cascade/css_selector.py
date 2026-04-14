"""Tier 2: Deterministic CSS pattern matching — zero LLM tokens."""

from __future__ import annotations

from applypilot.enrichment.cascade.html_utils import clean_description

APPLY_SELECTORS = [
    'a[href*="apply"]',
    'a[data-testid*="apply"]',
    'a[class*="apply"]',
    'a[aria-label*="pply"]',
    'button[data-testid*="apply"]',
    "a#apply_button",
    ".postings-btn-wrapper a",
    "a.ashby-job-posting-apply-button",
    '#grnhse_app a[href*="apply"]',
    'a[data-qa="btn-apply"]',
    'a[class*="btn-apply"]',
    'a[class*="apply-btn"]',
    'a[class*="apply-button"]',
]

DESCRIPTION_SELECTORS = [
    "#job-description",
    "#job_description",
    "#jobDescriptionText",
    ".job-description",
    ".job_description",
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[data-testid*="description"]',
    '[data-testid="job-description"]',
    ".posting-page .posting-categories + div",
    "#content .posting-page",
    "#app_body .content",
    "#grnhse_app .content",
    ".ashby-job-posting-description",
    '[class*="posting-description"]',
    '[class*="job-detail"]',
    '[class*="jobDetail"]',
    '[class*="job-content"]',
    '[class*="job-body"]',
    '[role="main"] article',
    "main article",
    'article[class*="job"]',
    ".job-posting-content",
]


def extract_apply_url_deterministic(page) -> str | None:
    """Try known CSS patterns for apply buttons/links."""
    for sel in APPLY_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                href = el.get_attribute("href")
                if href and href != "#":
                    return href
                tag = el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "button":
                    parent_href = el.evaluate("el => el.parentElement?.querySelector('a')?.href || null")
                    if parent_href:
                        return parent_href
                    return page.url
        except Exception:
            continue

    try:
        links = page.query_selector_all("a")
        for link in links:
            text = link.inner_text().strip().lower()
            if "apply" in text and len(text) < 50:
                href = link.get_attribute("href")
                if href and href != "#" and "javascript:" not in href:
                    return href
    except Exception:
        pass

    return None


def extract_description_deterministic(page) -> str | None:
    """Try known CSS patterns for the job description block."""
    for sel in DESCRIPTION_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if len(text) >= 100:
                    return clean_description(text)
        except Exception:
            continue

    # Fallback: heading-based extraction for SPA sites (Amazon, custom ATS)
    # Find h2/h3 with JD-related text and grab all content until next heading
    try:
        text = page.evaluate("""() => {
            const headings = [...document.querySelectorAll('h2, h3')];
            const jdKeywords = ['description', 'qualifications', 'requirements',
                'responsibilities', 'about the role', 'about the job', 'what you',
                'basic qualifications', 'preferred qualifications', 'key job'];
            const parts = [];
            for (const h of headings) {
                const hText = h.textContent.trim().toLowerCase();
                if (jdKeywords.some(k => hText.includes(k))) {
                    parts.push(h.textContent.trim());
                    let el = h.nextElementSibling;
                    while (el && !['H1','H2','H3'].includes(el.tagName)) {
                        const t = el.textContent.trim();
                        if (t.length > 10) parts.push(t);
                        el = el.nextElementSibling;
                    }
                }
            }
            return parts.join('\\n\\n');
        }""")
        if text and len(text) >= 100:
            return clean_description(text)
    except Exception:
        pass

    return None
