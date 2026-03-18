# Changelog

All notable changes to ApplyPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
 **Greenhouse ATS support** - New discovery source for 129 AI/ML startups and tech companies using Greenhouse (Scale AI, Stripe, Figma, Notion, etc.). Uses official Greenhouse Job Board API (`boards-api.greenhouse.io`) for reliable, structured data.
 **New module**: `src/applypilot/discovery/greenhouse.py` - API-based fetcher with full job descriptions, parallel execution, location filtering, and query matching
 **New config**: `src/applypilot/config/greenhouse.yaml` - 129 verified Greenhouse employers organized by category (Core AI, Infrastructure, Fintech, Healthcare, etc.)
 **User config override** - Users can extend/modify employers via `~/.applypilot/greenhouse.yaml`
 **New CLI commands** - `applypilot greenhouse verify|discover|validate|list-employers|add-job` for managing Greenhouse employers
 **Pipeline integration** - Greenhouse fetcher runs automatically during `discover` stage alongside JobSpy, Workday, and SmartExtract

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
