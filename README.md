<!-- logo here -->

> **⚠️ ApplyPilot** is the original open-source project, created by [Pickle-Pixel](https://github.com/Pickle-Pixel) and first published on GitHub on **February 17, 2026**. We are **not affiliated** with applypilot.app, useapplypilot.com, or any other product using the "ApplyPilot" name. These sites are **not associated with this project** and may misrepresent what they offer. If you're looking for the autonomous, open-source job application agent — you're in the right place.

# ApplyPilot

**Applied to 1,000 jobs in 2 days. Fully autonomous. Open source.**

[![PyPI version](https://img.shields.io/pypi/v/applypilot?color=blue)](https://pypi.org/project/applypilot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Pickle-Pixel/ApplyPilot?style=social)](https://github.com/Pickle-Pixel/ApplyPilot)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/S6S01UL5IO)




https://github.com/user-attachments/assets/7ee3417f-43d4-4245-9952-35df1e77f2df


---

## What It Does

ApplyPilot is a 6-stage autonomous job application pipeline. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, writes cover letters, and **submits applications for you**. It navigates forms, uploads documents, answers screening questions, all hands-free.

Three commands. That's it.

```bash
pip install applypilot
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
applypilot init          # one-time setup: resume, profile, preferences, API keys
applypilot doctor        # verify your setup — shows what's installed and what's missing
applypilot resume render --format html   # render the canonical resume.json with a theme
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot apply         # autonomous browser-driven submission
applypilot apply -w 3    # parallel apply (3 Chrome instances)
applypilot apply --dry-run  # fill forms without submitting
```

> **Why two install commands?** `python-jobspy` pins an exact numpy version in its metadata that conflicts with pip's resolver, but works fine at runtime with any modern numpy. The `--no-deps` flag bypasses the resolver; the second command installs jobspy's actual runtime dependencies. Everything except `python-jobspy` installs normally.

---

## Two Paths

### Full Pipeline (recommended)
**Requires:** Python 3.11+, Node.js (for `npx`), a built-in LLM provider (Gemini is the default), a browser agent CLI (Codex CLI by default, Claude Code CLI optional, OpenCode supported), Chrome

Runs all 6 stages, from job discovery to autonomous application submission. This is the full power of ApplyPilot.

### Discovery + Tailoring Only
**Requires:** Python 3.11+, an LLM key (Gemini/OpenAI/Claude) or `LLM_URL`

Runs stages 1-5: discovers jobs, scores them, tailors your resume, generates cover letters. You submit applications manually with the AI-prepared materials.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 129 Greenhouse ATS employers + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1-10 based on your resume and preferences. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | A browser agent CLI (Codex by default, Claude optional) navigates application forms, fills fields, uploads documents, answers questions, and submits |

Each stage is independent. Run them all or pick what you need.

---

## ApplyPilot vs The Alternatives

| Feature | ApplyPilot | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1-10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 129 Greenhouse employers, 46 Workday portals, 28 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Node.js 20+ | Auto-apply, themed resume render | Needed for `npx` to run Playwright MCP server and the local `resumed` renderer |
| LLM provider | Scoring, tailoring, cover letters | Gemini is recommended; OpenRouter, OpenAI, Anthropic, and local models are also supported |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| Codex CLI, Claude Code CLI, or OpenCode CLI | Auto-apply | Codex is preferred by default; Claude and OpenCode remain supported as additive backends |

**Gemini API key is free.** Get one at [aistudio.google.com](https://aistudio.google.com). OpenRouter, OpenAI, Anthropic, and local models (Ollama/llama.cpp) are also supported.

### Optional

| Component | What It Does |
|-----------|-------------|
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, CAPTCHA-blocked applications just fail gracefully |

### Gemini Smoke Check (optional)

```bash
GEMINI_API_KEY=your_key_here pytest -m smoke -q tests/test_gemini_smoke.py
```

> **Note:** python-jobspy is installed separately with `--no-deps` because it pins an exact numpy version in its metadata that conflicts with pip's resolver. It works fine with modern numpy at runtime.

---

## Configuration

Generated by `applypilot init`:

### `resume.json`
Canonical resume artifact. ApplyPilot stores `~/.applypilot/resume.json` as JSON Resume plus local `meta.applypilot` extensions. Personal/contact data, work history, education, skills, and projects are all sourced from this file at runtime.

### `profile.json`
Authoritative ApplyPilot settings store. `~/.applypilot/profile.json` keeps ApplyPilot-specific settings such as work authorization, compensation, availability, EEO defaults, tailoring config, and optional document paths.

If `profile.json` is missing but `resume.json` is valid, ApplyPilot auto-generates `profile.json` once for compatibility. If legacy profile data contains personal details or structured resume sections that are missing from `resume.json`, ApplyPilot migrates that missing data into `resume.json` and then rewrites `profile.json` down to settings only.

### `resume.txt` (legacy plain-text fallback)
Still supported for LLM-facing resume text when `resume.json` is absent.

### `searches.yaml`
Job search queries, target titles, locations, boards. Run multiple searches with different parameters.

### `.env`
API keys and runtime config: `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_URL`, `LLM_MODEL`, `AUTO_APPLY_AGENT`, `AUTO_APPLY_AGENT_PRIORITY`, `AUTO_APPLY_MODEL`, `CAPSOLVER_API_KEY` (optional).

## Two AI Layers

ApplyPilot intentionally separates its AI work into two different layers:

1. The built-in LLM layer handles text-heavy tasks like scoring jobs, tailoring resumes, writing cover letters, and some enrichment. Gemini is the default here.
2. The auto-apply agent layer drives the browser through MCP tools. Codex CLI is the default browser agent. Claude Code CLI and OpenCode CLI remain supported as compatibility backends.
   If you keep `AUTO_APPLY_AGENT=auto`, you can override the fallback order with `AUTO_APPLY_AGENT_PRIORITY=codex,claude,opencode`.

Canonical auto-apply settings stay on `AUTO_APPLY_*`. Compatibility aliases such as `APPLY_BACKEND`, `APPLY_CLAUDE_MODEL`, `APPLY_OPENCODE_MODEL`, and `APPLY_OPENCODE_AGENT` are accepted for merged-branch compatibility, but they are not the primary interface.

### Package configs (shipped with ApplyPilot)
- `config/employers.yaml` - Workday employer registry (48 preconfigured)
- `config/sites.yaml` - Direct career sites (30+), blocked sites, base URLs, manual ATS domains
- `config/searches.example.yaml` - Example search configuration

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable in `employers.yaml`). Queries Greenhouse employer boards from `config/greenhouse.yaml`. Hits 30 direct career sites with custom extractors. Deduplicates by URL.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data, then CSS selector patterns, then AI-powered extraction for unknown layouts.

### Score
AI scores every job 1-10 against your profile. 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Company names, project names, education, and verified metrics are derived from your canonical `resume.json`, so the AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
ApplyPilot launches a Chrome instance, then hands control to a browser agent CLI. By default that is Codex CLI; Claude Code CLI and OpenCode CLI are also supported. The agent navigates each application page, detects the form type, fills personal information and work history, uploads the tailored resume and cover letter, answers screening questions with AI, and submits. A live dashboard shows progress in real-time.

The Playwright MCP server is configured automatically at runtime per worker. No manual MCP setup needed.

```bash
# Utility modes (no Chrome/browser agent needed)
applypilot apply --mark-applied URL    # manually mark a job as applied
applypilot apply --mark-failed URL     # manually mark a job as failed
applypilot apply --reset-failed        # reset all failed jobs for retry
applypilot apply --gen --url URL       # generate prompt file for manual debugging
```

---

## CLI Reference

```
applypilot init                         # First-time setup wizard
applypilot init --resume-json PATH      # Import an existing JSON Resume during setup
applypilot resume render --format html  # Render canonical resume.json to themed HTML
applypilot resume render --format pdf   # Render canonical resume.json to themed PDF
applypilot doctor                       # Verify setup, diagnose missing requirements
applypilot run [stages...]              # Run pipeline stages (or 'all')
applypilot run --workers 4              # Parallel discovery/enrichment
applypilot run --stream                 # Concurrent stages (streaming mode)
applypilot run --min-score 8            # Override score threshold
applypilot run --dry-run                # Preview without executing
applypilot run --validation lenient     # Relax validation (recommended for Gemini free tier)
applypilot run --validation strict      # Strictest validation (retries on any banned word)
applypilot apply                        # Launch auto-apply
applypilot apply --workers 3            # Parallel browser workers
applypilot apply --dry-run              # Fill forms without submitting
applypilot apply --continuous           # Run forever, polling for new jobs
applypilot apply --headless             # Headless browser mode
applypilot apply --url URL              # Apply to a specific job
applypilot analyze --url URL            # Parse a job description and optional resume match
applypilot analyze --text-file job.txt --resume-file resume.json
applypilot greenhouse validate          # Validate configured Greenhouse employers
applypilot status                       # Pipeline statistics
applypilot dashboard                    # Open HTML results dashboard
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
