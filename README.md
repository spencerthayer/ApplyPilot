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
applypilot init --resume-pdf resume.pdf  # import resume from PDF via LLM
applypilot doctor        # verify your setup — shows what's installed and what's missing
applypilot resume render --format html   # render the canonical resume.json with a theme
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot run --url URL # scoped pipeline for one job URL (enrich→score→tailor→cover)
applypilot run --source workday --company walmart  # targeted discovery
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
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates. A statistical deviation guard (binomial proportion test) compares token retention between original and tailored resume — if the AI fabricated content, retention drops below threshold and the result is rejected |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | Code-first form filler for Greenhouse/Lever/Ashby (35s/job, $0.02). LLM agent fallback for Workday/LinkedIn. Fair scheduler spreads applications across boards and companies |

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
| local-geocode | Offline city/state → country resolution for location filtering. Installed automatically with `pip install applypilot` |

### Gemini Smoke Check (optional)

```bash
GEMINI_API_KEY=your_key_here pytest -m smoke -q tests/test_gemini_smoke.py
```

> **Note:** python-jobspy is installed separately with `--no-deps` because it pins an exact numpy version in its metadata that conflicts with pip's resolver. It works fine with modern numpy at runtime.

---

## PDF Resume Import

Import your existing resume from PDF (or TXT) files. The LLM extracts structured data and produces a canonical `resume.json`. Multiple files are merged automatically — first document wins for basics, arrays are deduplicated by key.

```bash
applypilot init --resume-pdf resume.pdf                          # single file
applypilot init --resume-pdf page1.pdf --resume-pdf page2.pdf    # merge multiple
```

Also available as a standalone command:

```bash
applypilot resume import file1.pdf file2.pdf --output path
```

---

## PPP Salary Intelligence

ApplyPilot adjusts salary expectations per target location using Purchasing Power Parity, so you don't get exploited when switching economies.

**How it works:** During `applypilot init`, you enter your current salary and target locations. ApplyPilot fetches live PPP data from the World Bank API and FX rates from open.er-api.com (both cached locally), then calculates what salary in each target country gives you equivalent purchasing power — and applies your desired hike on top of that.

**Example:** ₹20L in India → PPP equivalent ≈$98k USD → with 40% hike → ask for $138k in the US.

Without PPP adjustment, a naive 40% hike on ₹20L = ₹28L = ~$30k USD — unlivable in the US.

**Downward PPP warning:** If you're moving to a cheaper economy (e.g. US → Vietnam), ApplyPilot warns that applying a hike on top of PPP-equivalent gives you outsized purchasing power, which local employers will reject. It suggests a realistic range instead.

Data sources:
- PPP: [World Bank PA.NUS.PPP indicator](https://data.worldbank.org/indicator/PA.NUS.PPP) (198 countries, refreshed monthly)
- FX: [open.er-api.com](https://open.er-api.com) (166 currencies, refreshed daily)

---

## Statistical Deviation Guard

The tailoring engine rewrites your resume per job, but it must never fabricate. A binomial proportion test compares token retention between your original and tailored resume:

1. Tokenizes both resumes into lowercase word sets
2. Measures what fraction of original tokens survived in the tailored version
3. Uses binomial distribution: SE = √(p₀(1−p₀)/N), threshold = p₀ + z × SE
4. Names, companies, dates, and metrics are "anchors" that keep retention high when the AI is honest
5. If retention drops below the statistical threshold → the AI fabricated → result is rejected and retried

Baseline: 40% token overlap expected (p₀ = 0.40), 99% confidence level (α = 0.01).

---

## Code-First Apply Engine

The auto-apply stage uses a two-phase approach that's 10x faster than a pure LLM agent:

**Phase 1 — HTTP Pre-fetch (~1s, no Chrome):**
- GET the job page directly
- Check if job is live, expired, or requires login
- Extract form fields from server-rendered HTML
- Map fields to your profile data programmatically

**Phase 2 — Chrome Fill (~10-30s):**
- Navigate to the page in Chrome
- Fill all matched fields using React-compatible event dispatch
- Upload resume and cover letter PDFs
- Call LLM only for unknown screening questions (single batch call)
- Submit (or pause for dry-run verification)

| Board | Handler | Speed | LLM Calls |
|-------|---------|-------|-----------|
| Greenhouse (direct + embedded) | Code-first | ~35s | 0-1 |
| Lever | Code-first | ~35s | 0-1 |
| Ashby | Code-first | ~35s | 0-1 |
| Workday | LLM agent | ~180s | 30-60 |
| LinkedIn | LLM agent | ~180s | 30-60 |
| Unknown | Code-first → LLM fallback | ~35-180s | Varies |

Expired jobs are detected in 1-6 seconds via HTTP without launching Chrome.

---

## Fair Job Scheduler

Applications are spread across boards and companies using a CFS-inspired (Completely Fair Scheduler) algorithm:

1. Jobs are organized in a tree: Root → Boards → Companies → Jobs
2. Each node tracks a virtual runtime (how much "service" it's received)
3. The least-served company gets picked next
4. Higher-score jobs get more bandwidth (lower virtual runtime cost)

This prevents spamming one employer and ensures all companies get fair coverage. Example order:
```
Stripe(GH) → Motorola(WD) → Infystrat(HN) → Affirm(GH) → Netflix(WD) → ...
```

---

## Relevance Gate

During `applypilot init`, an LLM analyzes your profile and generates two keyword lists:
- **Role keywords**: words that should appear in relevant job titles (e.g., "engineer", "developer", "sde")
- **Anti-keywords**: words that definitely mean irrelevant (e.g., "sales", "recruiter", "nurse")

These are saved to `resume.json` and checked at discovery insert time — before jobs enter the database. Combined with the location resolver (city/state → country via [local-geocode](https://pypi.org/project/local-geocode/)), this filters out:
- Jobs in excluded countries (e.g., US)
- Non-matching role categories (e.g., sales roles for an engineer)
- Jobs requiring significantly more experience than your profile

---

## Job Timeline

View the complete lifecycle of any job with a single command:

```bash
applypilot timeline "https://example.com/jobs/123"
```

Shows every stage transition with timestamps: discovered → enriched → scored → tailored → cover letter → apply attempts → result. Includes artifact paths, LLM costs, and total lifecycle duration.

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
API keys and runtime config: `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_URL`, `LLM_MODEL`, `AUTO_APPLY_AGENT`, `AUTO_APPLY_AGENT_PRIORITY`, `AUTO_APPLY_MODEL`, `CAPSOLVER_API_KEY` (optional), `APPLYPILOT_SCORE_TRACE` (optional, set `1` for per-job scoring rationale logs).

## Two AI Layers

ApplyPilot intentionally separates its AI work into two different layers:

1. The built-in LLM layer handles text-heavy tasks like scoring jobs, tailoring resumes, writing cover letters, and some enrichment. Gemini is the default here.
2. The auto-apply agent layer drives the browser through MCP tools. Codex CLI is the default browser agent. Claude Code CLI and OpenCode CLI remain supported as compatibility backends.
   If you keep `AUTO_APPLY_AGENT=auto`, you can override the fallback order with `AUTO_APPLY_AGENT_PRIORITY=codex,claude,opencode`.

### Multi-Model Routing

ApplyPilot supports using different models for different tasks — a cheap/free model for high-volume work (scoring, enrichment) and a premium model for quality-sensitive output (tailoring, cover letters).

| Stage | Client | What it does |
|-------|--------|-------------|
| Discover, Enrich, Score | `get_client()` | Default model — high volume, cost-sensitive |
| Tailor, Cover Letter | `get_client(quality=True)` | Quality model — output directly impacts interviews |

Configure in `~/.applypilot/.env`:

```bash
# Default model (used for scoring, enrichment)
# Detected automatically from whichever API key is set:
GEMINI_API_KEY=your_key          # Free tier, recommended for scoring
# or OPENROUTER_API_KEY=...
# or OPENAI_API_KEY=...
# or BEDROCK_MODEL_ID=...

# Quality model override (used for tailoring, cover letters)
# Optional — if not set, uses the same model as default.
LLM_MODEL_QUALITY=bedrock/global.anthropic.claude-opus-4-6-v1
# or LLM_MODEL_QUALITY=gpt-4o
# or LLM_MODEL_QUALITY=anthropic/claude-sonnet-4-20250514
```

**Example cost comparison (8,000 jobs, ~400 tailored):**

| Setup | Scoring cost | Tailoring cost | Total |
|-------|-------------|---------------|-------|
| All Opus | ~$300 | ~$60 | ~$360 |
| Gemini free + Opus quality | $0 | ~$60 | ~$60 |
| Gemini free + Haiku quality | $0 | ~$5 | ~$5 |

Provider detection order: `LLM_URL` (local) → `GEMINI_API_KEY` → `OPENROUTER_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → `BEDROCK_MODEL_ID`

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
ApplyPilot uses a two-step scorer:
1. Deterministic baseline (0-10) from title similarity, skill overlap, seniority fit, and domain alignment.
2. LLM calibration with strict JSON output (`score`, `confidence`, `matched_skills`, `missing_requirements`, `reasoning`) and bounded score deltas around the baseline.

This improves consistency on near-identical titles while keeping outputs auditable. `LLM_FAILED` means the model/parsing step failed and the job remains retryable (`fit_score` stays `NULL`), which is different from a valid low-fit score.

Score bands: 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Company names, project names, education, and verified metrics are derived from your canonical `resume.json`, so the AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
ApplyPilot launches a Chrome instance, then uses a two-phase approach:

**Phase 1 (HTTP, ~1s):** Pre-fetch the job page, check if live/expired/login, extract form fields from HTML.

**Phase 2 (Chrome, ~10-30s):** Navigate, fill fields programmatically, upload resume, call LLM only for unknown screening questions, submit.

Board-specific handlers route to the right strategy:
- Greenhouse/Lever/Ashby → code-first (35s/job, $0.02)
- Workday/LinkedIn → LLM agent fallback (180s/job)
- Unknown → code-first with LLM fallback

A CFS fair scheduler spreads applications across boards and companies using virtual runtimes. Expired jobs detected in 1-6s via HTTP without Chrome.

The Playwright MCP server is configured automatically at runtime per worker. No manual MCP setup needed.

```bash
# Utility modes (no Chrome/browser agent needed)
applypilot apply --mark-applied URL    # manually mark a job as applied
applypilot apply --mark-failed URL     # manually mark a job as failed
applypilot apply --reset-failed        # reset all failed jobs for retry
applypilot apply --gen --url URL       # generate prompt file for manual debugging
```

### Single Job

Have a specific job URL? Skip the full pipeline and target it directly:

```bash
applypilot run --url "https://example.com/jobs/12345"
```

This runs a scoped pipeline (enrich → score → tailor → cover letter) for that URL only — no other jobs in your database
are touched. Once done, submit with:

```bash
applypilot apply --url "https://example.com/jobs/12345"
```

Multiple URLs work too:

```bash
applypilot run --url "https://example.com/jobs/1" "https://example.com/jobs/2"
```

> **Backward compat:** `applypilot single URL` still works as an alias for `run --url URL`.

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
applypilot run --url URL1 URL2          # Skip discover, run enrich→score→tailor→cover on URLs
applypilot run --source workday,greenhouse  # Only run specific discovery sources
applypilot run --company walmart,stripe # Filter discovery to specific companies
applypilot run --strict-title           # Require ALL query terms in job title
applypilot run --force                  # Re-tailor already-tailored jobs
applypilot company add NAME URL         # Add a company's career site to the registry
applypilot company list                 # List all companies in the registry
applypilot apply                        # Launch auto-apply (fair scheduler)
applypilot apply --workers 3            # Parallel browser workers
applypilot apply --dry-run              # Fill forms without submitting (pauses Chrome)
applypilot apply --continuous           # Run forever, polling for new jobs
applypilot apply --headless             # Headless browser mode
applypilot apply --url URL              # Apply to a specific job
applypilot apply --gen --url URL        # Generate prompt file for manual debugging
applypilot apply --mark-applied URL     # Manually mark a job as applied
applypilot apply --mark-failed URL      # Manually mark a job as failed
applypilot apply --reset-failed         # Reset all failed jobs for retry
applypilot timeline URL                 # Full lifecycle timeline for a job
applypilot timeline URL --json          # Machine-readable timeline
applypilot analyze --url URL            # Parse a job description and optional resume match
applypilot analyze --text-file job.txt --resume-file resume.json
applypilot greenhouse validate          # Validate configured Greenhouse employers
applypilot status                       # Pipeline statistics
applypilot dashboard                    # Open HTML results dashboard
applypilot recover                      # Reset stale jobs, clean partial artifacts
applypilot recover --clean              # Also remove partial resume/cover files
applypilot cv render --format html      # Render comprehensive CV (all sections)
applypilot resume refresh               # Clear stale tailored resumes for re-generation
applypilot resume refresh --force       # Force-regenerate all tailored resumes
applypilot analytics report             # Skill gaps, market intel, career health, roadmap
applypilot llm costs                    # Show LLM usage and cost summary
applypilot strengthen --paste           # Paste experience text, AI extracts achievements
applypilot strengthen --voice           # Record audio, transcribe, extract achievements
applypilot config show                  # Show runtime configuration
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
