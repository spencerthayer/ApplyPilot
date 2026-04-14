"""Classifier constants — known ATS domains, form service patterns."""

from __future__ import annotations

# Known ATS platform domains → tier T4
ATS_DOMAINS: dict[str, str] = {
    "greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "jobs.lever.co": "lever",
    "myworkdayjobs.com": "workday",
    "wd5.myworkdayjobs.com": "workday",
    "ashbyhq.com": "ashby",
    "jobs.ashbyhq.com": "ashby",
    "icims.com": "icims",
    "smartrecruiters.com": "smartrecruiters",
    "jobvite.com": "jobvite",
    "bamboohr.com": "bamboohr",
    "breezy.hr": "breezy",
    "recruitee.com": "recruitee",
}

# Form service patterns → tier T5
FORM_SERVICE_PATTERNS: list[str] = [
    "docs.google.com/forms",
    "forms.gle",
    "typeform.com",
    "jotform.com",
    "airtable.com/shr",
    "surveymonkey.com",
]

# Dead link indicators → tier T0
DEAD_INDICATORS: list[str] = [
    "this position has been filled",
    "job is no longer available",
    "this listing has expired",
    "page not found",
    "404",
]

# Login indicators → tier T2
LOGIN_INDICATORS: list[str] = [
    "sign in",
    "log in",
    "create account",
    "register to apply",
]

# CAPTCHA indicators → tier T3
CAPTCHA_INDICATORS: list[str] = [
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "verify you are human",
]

# Max redirect hops
MAX_HOPS = 5
HOP_TIMEOUT_S = 30
