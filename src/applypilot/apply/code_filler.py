"""Code-first form filler — fills job application forms programmatically.

Two-phase approach:
  Phase 1 (HTTP, ~1s): Pre-fetch page, check if live, discover fields from HTML
  Phase 2 (Chrome, ~5s): Navigate, fill fields, upload resume, submit

LLM only called for unknown screening questions (single batch call).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ── Profile field → form field ID mapping ─────────────────────────────────
# Covers Greenhouse, Lever, Ashby, and most ATS platforms.
# Keys are form field IDs/names (lowercase). Values are profile keys.
_FIELD_MAP = {
    # Greenhouse
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "candidate-location": "city",
    # Lever
    "resumator-firstname-value": "first_name",
    "resumator-lastname-value": "last_name",
    "resumator-email-value": "email",
    "resumator-phone-value": "phone",
    "resumator-address-value": "address",
    "resumator-city-value": "city",
    "resumator-state-value": "state",
    "resumator-postal-value": "postal_code",
    "resumator-linkedin-value": "linkedin",
    "resumator-salary-value": "salary",
    "resumator-start-value": "available_start",
}

# Label-based matching (case-insensitive substring)
_LABEL_MAP = {
    "first name": "first_name",
    "last name": "last_name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin",
    "github": "github",
    "portfolio": "portfolio",
    "website": "portfolio",
    "current company": "current_company",
    "preferred name": "first_name",
    "city": "city",
    "state": "state",
    "postal": "postal_code",
    "zip": "postal_code",
    "address": "address",
    "salary": "salary",
    "start date": "available_start",
    "earliest start": "available_start",
    "how did you hear": "how_heard",
}

# Known screening question patterns → static answers
_SCREENING_ANSWERS = {
    r"18 years.*age|are you 18": "Yes",
    r"background check": "Yes",
    r"felony|convicted": "No",
    r"previously.*(worked|employed).*here": "No",
    r"how did you hear|how.*hear.*about": "Online Job Board",
    r"willing to relocate|relocat": "Yes",
    r"legally.*authorized.*work|authorized to work": "{work_authorized}",
    r"require.*sponsor|need.*sponsor|visa.*sponsor": "{sponsorship_needed}",
    r"gender|sex": "Decline to self-identify",
    r"race|ethnicity": "Decline to self-identify",
    r"veteran": "I am not a protected veteran",
    r"disability": "I do not wish to answer",
}

# JS to discover all form fields
_DISCOVER_FIELDS_JS = """() => {
    const results = [];
    for (const el of document.querySelectorAll('input, textarea, select')) {
        if (el.offsetParent === null || el.type === 'hidden') continue;
        let label = el.getAttribute('aria-label') || '';
        if (!label && el.id) {
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) { const lbl = el.closest('label'); if (lbl) label = lbl.textContent.trim(); }
        if (!label && el.placeholder) label = el.placeholder;
        if (!label && el.name) label = el.name.replace(/[_-]/g, ' ');
        results.push({
            id: el.id || '', name: el.name || '', label: label.substring(0, 120),
            type: el.type || el.tagName.toLowerCase(), tag: el.tagName.toLowerCase(),
            value: el.value || '', required: el.required,
            selector: el.id ? '#' + el.id : el.name ? el.tagName.toLowerCase() + '[name="' + el.name + '"]' : null
        });
    }
    return JSON.stringify(results);
}"""

# JS to check if page is a job posting or error
_CHECK_PAGE_JS = """() => {
    const text = document.body.innerText.toLowerCase();
    const title = document.title.toLowerCase();
    if (/no longer (accepting|available)|position.{0,20}(filled|closed|removed)|this job.{0,20}(expired|closed)|job.{0,20}not found|posting.{0,20}(removed|expired)/.test(text))
        return 'expired';
    if (document.querySelector('input[type="password"]') && /forgot password|sign in|log in/.test(text))
        return 'login_required';
    if (document.querySelector('#application-form, form[action*="application"], .application-form, [data-controller="application"]'))
        return 'form_visible';
    // Check for Apply button — use safe selectors only
    const btns = document.querySelectorAll('a, button');
    for (const b of btns) {
        const t = b.textContent.trim().toLowerCase();
        if ((t === 'apply' || t === 'apply now' || t === 'apply for this job') && b.offsetParent !== null)
            return 'has_apply_button';
    }
    return 'unknown';
}"""


# ── Phase 1: HTTP pre-fetch ───────────────────────────────────────────────

_EXPIRED_PATTERNS = re.compile(
    r"no longer (accepting|available)|position.{0,20}(filled|closed|removed)"
    r"|this job.{0,20}(expired|closed|no longer)|job.{0,20}not found"
    r"|posting.{0,20}(removed|expired)|page not found|404",
    re.IGNORECASE,
)

_FIELD_HTML_RE = re.compile(
    r'<(input|textarea|select)\b([^>]*)>',
    re.IGNORECASE,
)

_ATTR_RE = re.compile(r'(\w[\w-]*)=["\']([^"\']*)["\']')

_LABEL_FOR_RE = re.compile(
    r'<label\b[^>]*?for=["\']([^"\']*)["\'][^>]*>(.*?)</label>',
    re.IGNORECASE | re.DOTALL,
)


def prefetch_page(url: str) -> dict:
    """Phase 1: HTTP GET to check job status and discover fields.

    Returns:
        {
            "status": "live" | "expired" | "login_required" | "error",
            "fields": [{id, name, type, label, selector}, ...],
            "title": str,
            "html_length": int,
            "error": str | None,
        }
    """
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
    except Exception as e:
        return {"status": "error", "fields": [], "title": "", "html_length": 0, "error": str(e)}

    html = resp.text
    text = re.sub(r"<[^>]+>", " ", html).lower()

    if resp.status_code == 404 or _EXPIRED_PATTERNS.search(text):
        return {"status": "expired", "fields": [], "title": "", "html_length": len(html), "error": None}

    if 'type="password"' in html and ("sign in" in text or "log in" in text):
        return {"status": "login_required", "fields": [], "title": "", "html_length": len(html), "error": None}

    title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    # Extract label[for] → text mapping
    label_map = {}
    for m in _LABEL_FOR_RE.finditer(html):
        fid = m.group(1)
        label_text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if fid and label_text:
            label_map[fid] = label_text

    # Extract form fields
    fields = []
    seen = set()
    for m in _FIELD_HTML_RE.finditer(html):
        tag = m.group(1).lower()
        attrs_str = m.group(2)
        attrs = dict(_ATTR_RE.findall(attrs_str))

        fid = attrs.get("id", "")
        fname = attrs.get("name", "")
        ftype = attrs.get("type", tag)
        aria = attrs.get("aria-label", "")

        if ftype in ("hidden", "submit") or fid == "false":
            continue
        if not fid and not fname:
            continue
        key = fid or fname
        if key in seen:
            continue
        seen.add(key)

        label = aria or label_map.get(fid, "") or fname.replace("_", " ").replace("-", " ")
        selector = f"#{fid}" if fid else f'[name="{fname}"]' if fname else None
        fields.append({"id": fid, "name": fname, "type": ftype, "label": label, "selector": selector})

    return {
        "status": "live",
        "fields": [f for f in fields if f.get("selector")],
        "title": title,
        "html_length": len(html),
        "error": None,
    }


def build_profile_data(job: dict) -> dict:
    """Build flat profile dict for form filling."""
    from applypilot.config import load_profile
    profile = load_profile()
    personal = profile.get("personal", {})
    work_auth = profile.get("work_authorization", {})
    comp = profile.get("compensation", {})
    exp = profile.get("experience", {})

    return {
        "first_name": (personal.get("full_name", "") or "").split()[0] if personal.get("full_name") else "",
        "last_name": " ".join((personal.get("full_name", "") or "").split()[1:]),
        "email": personal.get("email", ""),
        "phone": personal.get("phone", ""),
        "address": personal.get("address", ""),
        "city": personal.get("city", ""),
        "state": personal.get("province_state", ""),
        "postal_code": personal.get("postal_code", ""),
        "country": personal.get("country", ""),
        "linkedin": personal.get("linkedin_url", ""),
        "github": personal.get("github_url", ""),
        "portfolio": personal.get("portfolio_url", ""),
        "current_company": exp.get("current_company", ""),
        "salary": f"{comp.get('salary_expectation', '')} {comp.get('salary_currency', '')}".strip(),
        "available_start": profile.get("availability", {}).get("earliest_start_date", "Immediately"),
        "how_heard": "Online Job Board",
        "work_authorized": str(work_auth.get("legally_authorized_to_work", "Yes")),
        "sponsorship_needed": str(work_auth.get("require_sponsorship", "No")),
    }


def _match_field_to_profile(field: dict, profile_data: dict) -> str | None:
    """Try to match a form field to a profile value. Returns value or None."""
    fid = (field.get("id") or "").lower()
    fname = (field.get("name") or "").lower()
    flabel = (field.get("label") or "").lower()

    # 1. Direct ID match
    if fid in _FIELD_MAP:
        key = _FIELD_MAP[fid]
        return profile_data.get(key, "")

    # 2. Direct name match
    if fname in _FIELD_MAP:
        key = _FIELD_MAP[fname]
        return profile_data.get(key, "")

    # 3. Label substring match
    for pattern, key in _LABEL_MAP.items():
        if pattern in flabel:
            return profile_data.get(key, "")

    # 4. Screening question pattern match
    for pattern, answer in _SCREENING_ANSWERS.items():
        if re.search(pattern, flabel, re.IGNORECASE):
            # Substitute profile values
            if "{" in answer:
                return answer.format(**profile_data)
            return answer

    return None


async def fill_form(
    session: Any,
    job: dict,
    resume_pdf: str,
    cover_letter_pdf: str | None,
    profile_data: dict,
    dry_run: bool = False,
    log_lines: list[str] | None = None,
) -> str:
    """Fill a job application form programmatically. Returns RESULT: string.

    This is the code-first replacement for the LLM agent loop.
    """
    if log_lines is None:
        log_lines = []

    async def _eval(js: str) -> str:
        r = await session.call_tool("browser_evaluate", {"function": js})
        return "".join(c.text for c in r.content if hasattr(c, "text"))

    url = job.get("application_url") or job["url"]

    # ── Phase 1: HTTP pre-fetch (no Chrome needed) ────────────────
    log.info("[code-fill] Phase 1: pre-fetching %s", url[:60])
    prefetch = prefetch_page(url)
    log_lines.append(f"[P1] Pre-fetch: status={prefetch['status']} fields={len(prefetch['fields'])} html={prefetch['html_length']} chars")

    if prefetch["status"] == "expired":
        log.info("[code-fill] Job expired (HTTP check)")
        log_lines.append("[P1] Job expired — skipping Chrome entirely")
        return "RESULT:FAILED:job_expired"

    if prefetch["status"] == "login_required":
        log.info("[code-fill] Login required (HTTP check)")
        log_lines.append("[P1] Login required — skipping Chrome")
        return "RESULT:NEEDS_HUMAN:login_required"

    if prefetch["status"] == "error":
        log_lines.append(f"[P1] HTTP error: {prefetch['error']} — falling through to Chrome")

    # Log discovered fields from HTML
    if prefetch["fields"]:
        log_lines.append(f"[P1] Fields from HTML:")
        for f in prefetch["fields"][:15]:
            log_lines.append(f"[P1]   {f.get('label','?')[:30]:<30} type={f.get('type','?'):<10} id={f.get('id','')}")

    # Use pre-fetched fields if available, otherwise discover via Chrome DOM
    prefetch_fields = prefetch["fields"]

    # ── Phase 2: Chrome fill ──────────────────────────────────────
    log.info("[code-fill] Phase 2: Chrome fill")

    async def _click(selector: str) -> bool:
        try:
            await session.call_tool("browser_evaluate", {
                "function": f"() => {{ const el = document.querySelector('{selector}'); if (el) {{ el.click(); return 'clicked'; }} return 'not_found'; }}"
            })
            return True
        except Exception:
            return False

    async def _fill(selector: str, value: str, label: str = "") -> bool:
        """Fill a form field. Uses Playwright page.fill() via evaluate for React compat."""
        try:
            result = await _eval(f"""() => {{
                const el = document.querySelector('{selector}');
                if (!el) return 'not_found';
                el.focus();
                el.value = '';
                // Use Playwright-compatible input simulation
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set || Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                if (nativeSetter) nativeSetter.call(el, {json.dumps(value)});
                else el.value = {json.dumps(value)};
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                return 'filled:' + el.value.substring(0, 20);
            }}""")
            success = "filled:" in result
            if not success:
                log_lines.append(f"[fill] {label or selector}: {result[:60]}")
            return success
        except Exception as e:
            log_lines.append(f"[fill] {label or selector} error: {e}")
            return False

    url = job.get("application_url") or job["url"]

    # Step 1: Navigate and wait for page to be ready
    log.info("[code-fill] Navigating to %s", url[:60])
    await session.call_tool("browser_navigate", {"url": url})
    log_lines.append(f"[1] Navigate: {url[:80]}")

    import asyncio

    # Wait for network idle — page fully loaded including React hydration
    try:
        await session.call_tool("browser_evaluate", {
            "function": "() => new Promise(r => { if (document.readyState === 'complete') r('ready'); else window.addEventListener('load', () => r('ready')); })"
        })
    except Exception:
        await asyncio.sleep(3)
    log_lines.append("[1] Page loaded")

    # Step 2: Check page state — log the raw result for debugging
    raw_state = await _eval(_CHECK_PAGE_JS)
    page_state = ""
    for token in ("expired", "login_required", "form_visible", "has_apply_button", "unknown"):
        if f'"{token}"' in raw_state:
            page_state = token
            break
    log_lines.append(f"[2] Page state: {page_state}")
    log_lines.append(f"[2] Raw: {raw_state[:200]}")

    if page_state == "expired":
        log.info("[code-fill] Job expired")
        return "RESULT:FAILED:job_expired"

    if page_state == "login_required":
        log.info("[code-fill] Login required")
        return "RESULT:NEEDS_HUMAN:login_required"

    # Step 3: Click Apply if needed
    if page_state == "has_apply_button":
        log_lines.append("[3] Clicking Apply button")
        clicked = await _eval("""() => {
            const selectors = [
                'a[href*="apply"]', 'button:has-text("Apply")',
                '[data-action*="apply"]', '.btn-apply', '#apply-button',
                'a.postings-btn', 'a[data-job-id]'
            ];
            for (const sel of selectors) {
                try {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) { el.click(); return 'clicked: ' + sel; }
                } catch(e) {}
            }
            // Fallback: any link/button with "apply" text
            for (const el of document.querySelectorAll('a, button')) {
                if (el.textContent.trim().toLowerCase().includes('apply') && el.offsetParent !== null) {
                    el.click(); return 'clicked: text match';
                }
            }
            return 'no_button';
        }""")
        log_lines.append(f"[3] Apply click: {clicked}")
        await asyncio.sleep(2)

        # Re-check for login after clicking Apply
        raw_state2 = await _eval(_CHECK_PAGE_JS)
        page_state2 = ""
        for token in ("expired", "login_required", "form_visible", "has_apply_button", "unknown"):
            if f'"{token}"' in raw_state2:
                page_state2 = token
                break
        if page_state2 == "login_required":
            return "RESULT:NEEDS_HUMAN:login_required"
        if page_state2 == "expired":
            return "RESULT:FAILED:job_expired"

    # Step 4: Use pre-fetched fields or discover via Chrome DOM
    fields = prefetch_fields if prefetch_fields else []

    if not fields:
        # Fallback: discover via Chrome DOM
        await _eval("() => { const f = document.querySelector('form, #application-form, .application-form'); if (f) f.scrollIntoView(); return 'ok'; }")
        await asyncio.sleep(1)

        for _attempt in range(5):
            raw = await _eval(_DISCOVER_FIELDS_JS)
            log_lines.append(f"[4] DOM discovery attempt {_attempt+1}: {len(raw)} chars")
            try:
                # Extract JSON array from tool output (may have markdown wrapping)
                start = raw.index("[")
                # Find matching closing bracket
                depth = 0
                end = start
                for i in range(start, len(raw)):
                    if raw[i] == "[":
                        depth += 1
                    elif raw[i] == "]":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                fields = json.loads(raw[start:end])
            except (ValueError, json.JSONDecodeError) as e:
                log_lines.append(f"[4] Parse error: {e}")
                fields = []
            fields = [f for f in fields if f.get("selector")]
            if fields:
                break
            await asyncio.sleep(1)

    log_lines.append(f"[4] Total fields: {len(fields)} ({'pre-fetched' if prefetch_fields else 'DOM'})")
    for f in fields[:10]:
        log_lines.append(f"[4]   {f.get('label','?')[:30]:<30} type={f.get('type','?'):<10} id={f.get('id','')}")

    if not fields:
        return "RESULT:FAILED:no_form_found"

    # Step 5: Fill known fields
    filled = 0
    unknown = []
    for field in fields:
        ftype = field.get("type", "")
        selector = field["selector"]
        label = field.get("label", "")

        if ftype == "file" or field.get("value"):
            continue

        value = _match_field_to_profile(field, profile_data)
        if value:
            if field.get("id") == "country" or ("country" in label.lower() and "located" not in label.lower()):
                # Country dropdown (Greenhouse React Select)
                await _click(selector)
                await asyncio.sleep(0.3)
                await _fill(selector, value, label)
                await asyncio.sleep(0.5)
                await _eval(f"""() => {{
                    const opts = document.querySelectorAll('[class*="option"], [role="option"], li');
                    for (const o of opts) {{
                        if (o.textContent.toLowerCase().includes({json.dumps(value.lower())})) {{
                            o.click(); return 'selected';
                        }}
                    }}
                    return 'no_match';
                }}""")
                filled += 1
                log_lines.append(f"[5] ✅ {label[:30]} = {value[:20]} (dropdown)")
            elif ftype in ("text", "email", "tel", "url", "textarea", "search"):
                ok = await _fill(selector, value, label)
                if ok:
                    filled += 1
                    log_lines.append(f"[5] ✅ {label[:30]} = {value[:20]}")
                else:
                    log_lines.append(f"[5] ❌ {label[:30]} — fill failed")
                    unknown.append(field)
            elif ftype == "select-one":
                await _eval(f"""() => {{
                    const el = document.querySelector('{selector}');
                    if (!el) return 'not_found';
                    for (const opt of el.options) {{
                        if (opt.text.toLowerCase().includes({json.dumps(value.lower())})) {{
                            el.value = opt.value;
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return 'selected';
                        }}
                    }}
                    return 'no_match';
                }}""")
                filled += 1
                log_lines.append(f"[5] ✅ {label[:30]} = {value[:20]} (select)")
        else:
            if ftype not in ("file",):
                unknown.append(field)
                log_lines.append(f"[5] ❓ {label[:30]} — no profile match")

    log_lines.append(f"[5] Summary: {filled} filled, {len(unknown)} unknown")

    # Step 6: Upload resume
    resume_uploaded = False
    for field in fields:
        if field.get("type") == "file":
            fid = field.get("id", "")
            flabel = (field.get("label") or "").lower()
            if "resume" in fid or "resume" in flabel or "cv" in flabel:
                await _click(field["selector"])
                await asyncio.sleep(0.5)
                await session.call_tool("browser_file_upload", {"paths": [resume_pdf]})
                resume_uploaded = True
                log_lines.append("[6] Resume uploaded")
            elif ("cover" in fid or "cover" in flabel) and cover_letter_pdf:
                await _click(field["selector"])
                await asyncio.sleep(0.5)
                await session.call_tool("browser_file_upload", {"paths": [cover_letter_pdf]})
                log_lines.append("[6] Cover letter uploaded")

    if not resume_uploaded:
        # Try clicking any file upload area
        for field in fields:
            if field.get("type") == "file":
                await _click(field["selector"])
                await asyncio.sleep(0.5)
                await session.call_tool("browser_file_upload", {"paths": [resume_pdf]})
                resume_uploaded = True
                log_lines.append("[6] Resume uploaded (generic file input)")
                break

    # Step 7: Handle unknown required fields with LLM (single call)
    if unknown:
        log_lines.append(f"[7] Asking LLM about {len(unknown)} unknown fields")
        try:
            from applypilot.llm import get_client
            client = get_client(tier="premium")

            questions = "\n".join(
                f"- Field: \"{f.get('label', f.get('id', '?'))}\" (type={f.get('type','text')}, required={f.get('required',False)})"
                for f in unknown
            )
            prompt = (
                f"I'm applying for {job.get('title','')} at {job.get('site','')}.\n"
                f"My profile: {json.dumps({k: v for k, v in profile_data.items() if v}, indent=0)}\n\n"
                f"Answer these form fields. Return JSON object mapping field label to answer value:\n{questions}\n\n"
                f"Rules: Never lie about work authorization. Use 'Decline to self-identify' for demographic questions. "
                f"For open-ended questions, write 2-3 sentences. Return ONLY the JSON object."
            )
            raw_answer = client.chat([{"role": "user", "content": prompt}], max_output_tokens=2000)
            raw_answer = re.sub(r"<think>.*?</think>", "", raw_answer, flags=re.DOTALL).strip()
            start = raw_answer.index("{")
            end = raw_answer.rindex("}") + 1
            answers = json.loads(raw_answer[start:end])

            for field in unknown:
                label = field.get("label", field.get("id", ""))
                value = answers.get(label)
                if not value:
                    # Try fuzzy match
                    for k, v in answers.items():
                        if k.lower() in label.lower() or label.lower() in k.lower():
                            value = v
                            break
                if value and field.get("selector"):
                    await _fill(field["selector"], str(value))
                    filled += 1

            log_lines.append(f"[7] LLM answered {len(answers)} fields")
        except Exception as e:
            log_lines.append(f"[7] LLM fallback failed: {e}")

    # Step 8: Submit (or pause for dry-run)
    if dry_run:
        log_lines.append(f"[8] DRY RUN — filled {filled} fields total, not submitting")
        return "RESULT:APPLIED (dry_run)"

    # Find and click submit
    submit_result = await _eval("""() => {
        const selectors = [
            'input[type="submit"]', 'button[type="submit"]',
            'button:has-text("Submit")', 'button:has-text("Apply")',
            '#submit-app', '.submit-button', '[data-action="submit"]'
        ];
        for (const sel of selectors) {
            try {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) { el.click(); return 'submitted: ' + sel; }
            } catch(e) {}
        }
        for (const el of document.querySelectorAll('button, input[type="submit"]')) {
            const t = el.textContent.trim().toLowerCase();
            if ((t.includes('submit') || t === 'apply') && el.offsetParent !== null) {
                el.click(); return 'submitted: text match';
            }
        }
        return 'no_submit_button';
    }""")
    log_lines.append(f"[8] Submit: {submit_result}")

    if "submitted" in submit_result:
        await asyncio.sleep(3)
        # Check for success
        confirmation = await _eval("""() => {
            const text = document.body.innerText.toLowerCase();
            if (/thank you|application.*received|successfully.*submitted|application.*submitted/.test(text))
                return 'confirmed';
            if (/error|required|please fill|invalid/.test(text))
                return 'validation_error';
            return 'unknown';
        }""")
        log_lines.append(f"[8] Confirmation: {confirmation}")
        if confirmation == "confirmed":
            return "RESULT:APPLIED"
        elif confirmation == "validation_error":
            return "RESULT:NEEDS_HUMAN:validation_errors"
        return "RESULT:APPLIED"  # Optimistic — submit clicked, no error detected

    return "RESULT:NEEDS_HUMAN:no_submit_button"
