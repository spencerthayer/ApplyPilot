# Changes

## Auto-Apply Agent Migration

- Split the browser-driven auto-apply layer from the built-in LLM layer.
- Kept Gemini/OpenRouter/OpenAI/local provider behavior unchanged for scoring, tailoring, cover letters, and enrichment.
- Added a pluggable auto-apply backend system with:
  - Codex CLI as the default backend
  - Claude Code CLI as a compatibility backend
- Moved backend-specific command construction and execution into [src/applypilot/apply/agent_backends.py](/Users/spencerthayer/Desktop/ApplyPilot/src/applypilot/apply/agent_backends.py).

## New Auto-Apply Config

- Added `AUTO_APPLY_AGENT` to select `auto`, `codex`, or `claude`.
- Added `AUTO_APPLY_AGENT_PRIORITY` to control the fallback order used when `AUTO_APPLY_AGENT=auto`.
  - Example: `AUTO_APPLY_AGENT_PRIORITY=claude,codex`
- Added `AUTO_APPLY_MODEL` for browser-agent model overrides only.
- Left `LLM_MODEL` unchanged for the built-in LLM layer.

## CLI And Diagnostics

- Updated `applypilot apply` to support:
  - `--agent`
  - `--agent-model`
  - `--model` as a temporary alias for `--agent-model`
- Updated Tier 3 detection to require:
  - a supported browser agent CLI
  - Chrome/Chromium
  - Node.js / `npx`
- Updated `applypilot doctor` to report:
  - built-in LLM status
  - selected auto-apply agent
  - Codex CLI + login status
  - Claude CLI availability

## Wizard And Docs

- Updated the setup wizard to explain the two-AI-layer architecture.
- Updated `.env.example` with the new auto-apply variables.
- Updated `README.md` to document:
  - the built-in LLM layer
  - the separate browser-agent layer
  - the new auto-apply environment variables

## Tests

- Added coverage for:
  - auto-apply agent resolution
  - env-driven priority overrides
  - Codex and Claude command construction
  - result parsing
  - backend failure handling
  - doctor output
- Fixed the existing brittle `check_tier` test so Rich line wrapping no longer breaks it.
