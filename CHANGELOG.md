# Changelog

All notable changes to ApplyPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Code-first apply engine** (`apply/code_filler.py`) — HTTP prefetch + programmatic form fill for Greenhouse/Lever/Ashby. 35s/job vs 180s with LLM loop. LLM called only for unknown screening questions (single batch call)
- **Fair job scheduler** (`apply/scheduler.py`) — CFS-inspired hierarchical min-heap scheduler. Round-robin across boards and companies using virtual runtimes. O(log n) per pick
- **Board-specific handlers** — Greenhouse/Lever/Ashby → code-first, Workday/LinkedIn → LLM agent, Unknown → code-first with LLM fallback
- **Relevance gate** (`discovery/relevance_gate.py`) — LLM-generated role keywords + anti-keywords at init time. Filters irrelevant jobs at discovery insert before they enter the DB
- **Location resolver** (`discovery/location_resolver.py`) — City/state → country resolution via `local-geocode` library. Enables location-based filtering (e.g., exclude US jobs)
- **Job timeline** (`analytics/timeline.py`, `applypilot timeline <URL>`) — Full lifecycle view per job: discover → enrich → score → tailor → cover → apply with timestamps, errors, artifacts, stats
- **Dashboard/orchestrator separation** — All Rich/UI code moved from `orchestrator.py` to `dashboard.py`. Orchestrator is now pure business logic with zero UI imports
- **Dashboard pause/resume** — `dashboard.pause_for_input()` stops Live refresh during interactive prompts, preventing flickering
- **Interactive needs_human prompt** — Enter=skip, r=retry agent, m=finish manually. Chrome stays open
- **Pause file mechanism** — `touch ~/.applypilot/.pause` to intervene mid-agent-run
- **Dry-run Chrome pause** — Chrome stays open for verification on both APPLIED and NEEDS_HUMAN results
- **HTTP pre-fetch expired detection** — Detects expired jobs in 1-6s via HTTP without launching Chrome
- **Shared Playwright browser pool** (`pdf_renderer.py`) — One Chromium instance reused across all PDFs in a batch. Eliminates ~2-4s startup per PDF
- **76 new tests** (941 → 1015) across 4 new test files: code_filler, scheduler, location_resolver, relevance_gate

### Fixed

- **False expired detection** — `_CHECK_PAGE_JS` regex matched its own source code in tool output. Fixed with exact token matching
- **False auth detection** — Removed hardcoded auth page text matching that triggered on nav bar "Sign in" links. LLM handles auth detection via prompt rules
- **Auto cookie dismiss false clicks** — Removed mechanical cookie dismiss that clicked wrong elements on sites without banners. LLM handles via prompt rules
- **Infinite retry on LLM errors** — `llm_error`, `job_expired`, `login_required`, `no_form_found` added to permanent failures
- **Stale in_progress locks** — Jobs locked by interrupted runs now properly released
- **Invalid CSS selector** — `button:has-text()` (Playwright syntax) replaced with safe text content check in `querySelector`
- **JSON parse in DOM discovery** — Bracket depth matching instead of `rindex` which matched wrong bracket
- **Ambiguous SQL column** — Round-robin query aliased subquery `site` as `_rsite`
- **macOS display detection** — macOS always returns headful (doesn't use DISPLAY env var)
- **Native agent used cheap model** — Changed default from `tier="cheap"` to `tier="premium"` for form filling

### Changed

- **Apply job acquisition** — Replaced `ORDER BY fit_score DESC` with CFS round-robin: `ROW_NUMBER() OVER (PARTITION BY site)` + virtual runtime ordering
- **Max agent iterations** — 35 → 60 for forms with many screening questions
- **Snapshot truncation** — 8K → 16K for large Greenhouse pages
- **System prompt** — Added R11 (N/A for irrelevant location questions), R12 (neutral answers for unknown fields)
- **`pyproject.toml`** — Added `local-geocode>=0.0.2` dependency
- **`wizard.py`** — Generates relevance filter during init (Step 4b)

- **v2 Architecture Implementation** — Full implementation of all v2 planning doc requirements. See
  `docs/IMPLEMENTATION_ADDENDUM-2026-04-03.md` for details.
- **`applypilot recover`** — Reset stale in-progress jobs and clean partial artifacts (APPLY-22)
- **`applypilot cv render`** — Generate comprehensive CV from master profile, all sections, no page limit (INIT-20)
- **`applypilot resume refresh`** — Detect stale tailored resumes after profile edits and clear for re-tailoring (
  INIT-23)
- **FTS5 full-text search** — `jobs_fts` virtual table for fast keyword search across job titles, companies,
  descriptions (RUN-04)
- **Geographic filtering modes** — `worldwide`, `include_only`, `exclude` modes in searches.yaml (RUN-09)
- **Remote work mode filtering** — `classify_work_mode()` and `work_mode_ok()` for remote/hybrid/onsite filtering (
  RUN-10)
- **Market Intelligence** — Top skills demand, locations, seniority distribution from scored jobs (ANALYZE-03)
- **Career Health Score** — 0-10 composite metric: skill coverage + experience depth + success rate + market demand (
  ANALYZE-04)
- **Career Roadmap** — Prioritized skill acquisition milestones with demand percentages (ANALYZE-05)
- **Track/Segment Comparison** — Side-by-side metrics by job source (ANALYZE-07)
- **AI-suggested flagging** — Enrichment-generated content tracked in `meta.applypilot.ai_suggested[]` (INIT-04)
- **Multi-language input** — Non-English resume content auto-normalized to English via LLM (INIT-06)
- **Per-section resume review** — Interactive accept/edit/remove per resume section during init (INIT-10)
- **Tiered tailoring** — TL0-TL3 effort levels based on fit score (INIT-22)
- **LLM cost persistence** — Costs saved to `analytics_events` DB, survives across sessions
- **`pipeline.cost_tracking`** — Config flag to enable/disable cost persistence
- **BulletBankRepository** — ABC + SQLite implementation for bullet bank persistence
- **Per-file migration system** — `db/migrations/mNNN_*.py` auto-discovered by runner
- **ComprehensiveStorage** — Storage adapter for comprehensive tailoring engine
- **`resume/` subpackage** — Validation and extraction extracted from `resume_json.py`
- **Company-aware priority queue** — `PARTITION BY COALESCE(company, site)` spreads applications across employers (
  APPLY-19)
- **139 new tests** (565 → 704), 9 new test files covering analytics, classifiers, scoring, threading, cost tracking

### Fixed

- **SQLite threading crash in analytics observer** — Observer now creates thread-local DB connection
- **`init --resume-json` overwrites existing resume** — Now merges, preserving profiles/contact/meta
- **GitHub URL missing from HTML render** — URLs from `meta.applypilot.personal` now synced to `basics.profiles[]`
- **`applypilot llm costs` KeyError** — Factory and CLI now use consistent `CostTracker.summary()` keys
- **LLM costs lost on exit** — Persisted to DB via `analytics_events`
- **`CostTracker` never wired** — Now instantiated in `LLMClient.__init__` and recorded after each call
- **113 F821 lint warnings** — All cross-module undefined name references resolved
- **894 total lint errors** — All fixed (auto-fix + ruff format + manual splits)
- **Dead code in orchestrator** — Removed legacy `run_job()` (90 lines of raw SQL)
- **All SQL leaks** — Zero `sqlite3.connect()` calls remain in business logic

### Changed

- `tracking/stubs.py` — Rewritten to use `TrackingRepository` (5 new methods)
- `tracking/pipeline.py` — Rewritten to use repos instead of raw SQL
- `tailoring/bullet_bank/bank.py` — Now accepts `BulletBankRepository` via constructor
- `tailoring/comprehensive_engine.py` — `import sqlite3` removed, uses `ComprehensiveStorage`
- `tailoring/state_machine.py` — `_make_bullet_bank()` helper with DI fallback
- `discovery/jobspy/filters.py` — `_location_ok()` accepts mode param, `_load_location_config()` returns 3-tuple
- `analytics/observer.py` — Thread-local connection, all 7 aggregator models wired
- `analytics/aggregators/processor.py` — Routes events to market, health, roadmap, tracks aggregators
- `pyproject.toml` — Added `[tool.ruff.lint]` config suppressing structural rules

 **Greenhouse ATS support** - New discovery source for 129 AI/ML startups and tech companies using Greenhouse (Scale AI, Stripe, Figma, Notion, etc.). Uses official Greenhouse Job Board API (`boards-api.greenhouse.io`) for reliable, structured data.
 **New module**: `src/applypilot/discovery/greenhouse.py` - API-based fetcher with full job descriptions, parallel execution, location filtering, and query matching
 **New config**: `src/applypilot/config/greenhouse.yaml` - 129 verified Greenhouse employers organized by category (Core AI, Infrastructure, Fintech, Healthcare, etc.)
 **User config override** - Users can extend/modify employers via `~/.applypilot/greenhouse.yaml`
 **New CLI commands** - `applypilot greenhouse verify|discover|validate|list-employers|add-job` for managing Greenhouse employers
 **Pipeline integration** - Greenhouse fetcher runs automatically during `discover` stage alongside JobSpy, Workday, and SmartExtract
 **`pending_cover` stage** in `get_jobs_by_stage()` — cover letter generation now uses the shared query gateway instead of inline SQL

### Fixed
 **`applypilot single` processed all jobs instead of one** — score, tailor, cover, and enrich stages ignored `PipelineContext.job_url` and queried the entire database. Added `job_url` parameter to `get_jobs_by_stage()`, `run_scoring()`, `run_tailoring()`, `run_cover_letters()`, `run_enrichment()`, and `_run_detail_scraper()`. All stages in `pipeline/stages.py` now pass `ctx.job_url` through. Existing batch callers are unaffected (parameter defaults to `None`).

### Changed
- **Canonical resume contract** - Runtime personal info, work history, education, skills, projects, and verified metrics now come from `~/.applypilot/resume.json` instead of duplicated fields in `~/.applypilot/profile.json`
- **Settings-only profile.json** - `~/.applypilot/profile.json` now stores ApplyPilot settings only (`work_authorization`, `compensation`, `availability`, `eeo_voluntary`, `tailoring_config`, `files`)
- **Compatibility migration** - When canonical installs still have legacy profile data, ApplyPilot backfills missing personal/work/education/skills data into `resume.json` and rewrites `profile.json` to the settings-only format
- **Validation and tailoring sources** - fabrication checks, cover-letter prompts, metrics validation, and comprehensive tailoring now derive companies, schools, skills, projects, and key metrics from normalized `resume.json` data
- **Apply batch default** - `applypilot apply` now drains all currently eligible jobs in one batch by default; `--continuous` remains the polling mode

### Removed
- **`resume_facts` profile contract** - removed from runtime profile handling, setup flows, validation, tailoring, and documentation

## [0.3.0] - 2026-03-09

### Added
- **Multi-provider LLM fallback** - cascading Gemini → OpenAI → Anthropic with automatic
  429/quota recovery and 5-minute exhaustion cooldown per model
- **Two-tier model strategy** - `get_client(quality=True)` for writing (Pro models),
  `get_client(quality=False)` for scoring (Flash models)
- **Hacker News discovery** - `Ask HN: Who is Hiring?` thread scraper with LLM-powered
  extraction, location keyword pre-filtering, and email deobfuscation
- **Company extraction** - automatic company name extraction from application URLs
  (Workday, Greenhouse, Lever, iCIMS patterns)
- **Company-aware apply prioritization** - `acquire_job()` spreads applications across
  employers using `ROW_NUMBER() PARTITION BY company`
- **HTML dashboard** - self-contained pipeline funnel visualization with inline viewers
  for tailored resumes, cover letters, and agent logs
- **Streaming pipeline** - `applypilot run --stream` for concurrent stage execution
- **Credit exhaustion detection** - launcher detects "credit balance is too low" and
  stops all workers immediately
- **CLAUDE.md operating manual** - comprehensive pipeline documentation for Claude Code

### Fixed
- **Apply subprocess billing** - `ANTHROPIC_API_KEY` stripped from subprocess env to
  prevent API billing when using Claude Code Max plan
- **Docker MCP interference** - `--strict-mcp-config` flag prevents Docker MCP Toolkit's
  Playwright tools from shadowing local npx Playwright (Docker tools can't access host files)
- **Filename collisions** - tailor and cover letter files use `{site}_{title}_{url_hash[:8]}`
  suffix for uniqueness
- **HN URL sanitization** - only stores http(s) URLs, deobfuscates emails (`[at]`→`@`),
  generates synthetic URLs for contact-only posts

### Changed
- **Scoring prompt is profile-driven** - candidate summary built dynamically from
  `profile.json` instead of hardcoded in source
- **Location keywords configurable** - HN discovery loads accept patterns from search
  config instead of hardcoding cities
- **Salary examples generalized** - prompt salary section uses profile-driven values
  instead of hardcoded dollar amounts
- **URL normalization at insert** - resolves relative URLs via `sites.yaml` base_urls
- **Validator improvements** - fabrication detection cross-references profile's
  `skills_boundary`, banned words are warnings not errors

## [0.2.0] - 2026-02-17

### Added
- **Parallel workers for discovery/enrichment** - `applypilot run --workers N` enables
  ThreadPoolExecutor-based parallelism for Workday scraping, smart extract, and detail
  enrichment. Default is sequential (1); power users can scale up.
- **Apply utility modes** - `--gen` (generate prompt for manual debugging), `--mark-applied`,
  `--mark-failed`, `--reset-failed` flags on `applypilot apply`
- **Dry-run mode** - `applypilot apply --dry-run` fills forms without clicking Submit
- **5 new tracking columns** - `agent_id`, `last_attempted_at`, `apply_duration_ms`,
  `apply_task_id`, `verification_confidence` for better apply-stage observability
- **Manual ATS detection** - `manual_ats` list in `config/sites.yaml` skips sites with
  unsolvable CAPTCHAs (e.g. TCS iBegin)
- **Qwen3 `/no_think` optimization** - automatically saves tokens when using Qwen models
- **`config.DEFAULTS`** - centralized dict for magic numbers (`min_score`, `max_apply_attempts`,
  `poll_interval`, `apply_timeout`, `viewport`)

### Fixed
- **Config YAML not found after install** - moved `config/` into the package at
  `src/applypilot/config/` so YAML files (employers, sites, searches) ship with `pip install`
- **Search config format mismatch** - wizard wrote `searches:` key but discovery code
  expected `queries:` with tier support. Aligned wizard output and example config
- **JobSpy install isolation** - removed python-jobspy from package dependencies due to
  broken numpy==1.26.3 exact pin in jobspy metadata. Installed separately with `--no-deps`
- **Scoring batch limit** - default limit of 50 silently left jobs unscored across runs.
  Changed to no limit (scores all pending jobs in one pass)
- **Missing logging output** - added `logging.basicConfig(INFO)` so per-job progress for
  scoring, tailoring, and cover letters is visible during pipeline runs

### Changed
- **Blocked sites externalized** - moved from hardcoded sets in launcher.py to
  `config/sites.yaml` under `blocked:` key
- **Site base URLs externalized** - moved from hardcoded dict in detail.py to
  `config/sites.yaml` under `base_urls:` key
- **SSO domains externalized** - moved from hardcoded list in prompt.py to
  `config/sites.yaml` under `blocked_sso:` key
- **Prompt improvements** - screening context uses `target_role` from profile,
  salary section includes `currency_conversion_note` and dynamic hourly rate examples
- **`acquire_job()` fixed** - writes `agent_id` and `last_attempted_at` to proper columns
  instead of misusing `apply_error`
- **`profile.example.json`** - added `currency_conversion_note` and `target_role` fields

## [0.1.0] - 2026-02-17

### Added
- 6-stage pipeline: discover, enrich, score, tailor, cover letter, apply
- Multi-source job discovery: Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs
- Workday employer portal support (46 preconfigured employers)
- Direct career site scraping (28 preconfigured sites)
- 3-tier job description extraction cascade (JSON-LD, CSS selectors, AI fallback)
- AI-powered job scoring (1-10 fit scale with rationale)
- Resume tailoring with factual preservation (no fabrication)
- Cover letter generation per job
- Autonomous browser-based application submission via Playwright
- Interactive setup wizard (`applypilot init`)
- Cross-platform Chrome/Chromium detection (Windows, macOS, Linux)
- Multi-provider LLM support (Gemini, OpenAI, local models via OpenAI-compatible endpoints)
- Pipeline stats and HTML results dashboard
- YAML-based configuration for employers, career sites, and search queries
- Job deduplication across sources
- Configurable score threshold filtering
- Safety limits for maximum applications per run
- Detailed application results logging
