# TODO

## Current State

- Repository is installed at the repo root: `/Users/spencerthayer/Desktop/ApplyPilot`
- Local virtual environment exists at `.venv/`
- Python 3.11 is installed
- Local `applypilot` install was repaired in `.venv/`
- Missing build-time deps needed for the editable install were added to `.venv/`:
  `hatchling`, `editables`
- `applypilot --version` works and reports `applypilot 0.3.0`
- `applypilot doctor` runs successfully
- `python -m pip check` passes
- `applypilot doctor` currently detects:
  - `python-jobspy`: OK
  - Claude Code CLI: OK
  - Chrome/Chromium: OK
  - Node.js (`npx`): OK
  - Missing user setup: `profile.json`, `resume.txt`, `GEMINI_API_KEY`
- Current ApplyPilot tier: Tier 1 (Discovery only until user config is added)

## Completed This Session

- [x] Activate and validate the local environment
- [x] Repair the broken `applypilot` CLI/package install in `.venv/`
- [x] Confirm the CLI runs:
  `applypilot --version`
- [x] Confirm environment health:
  `applypilot doctor`
- [x] Confirm Python package consistency:
  `python -m pip check`

## Required Next Steps

- [ ] Activate the local environment before working:
  `source .venv/bin/activate`
- [ ] Run the first-time setup wizard interactively:
  `applypilot init`
- [ ] Provide your resume file path and complete the personal/profile prompts during `applypilot init`
- [ ] This will create the missing user config under:
  `~/.applypilot/`
- [ ] Add `GEMINI_API_KEY` to `~/.applypilot/.env` so scoring, tailoring, and cover letters work
- [ ] Re-run `applypilot doctor` after setup and confirm:
  - Tier 2 is unlocked if the LLM key is present
  - Tier 3 is unlocked if the LLM key is present and auto-apply prerequisites are usable

## If You Want Full Auto-Apply

- [ ] Confirm Claude Code CLI is logged in and usable from your shell
- [ ] Confirm Chrome launches normally outside the sandboxed Codex session
- [ ] Optionally add `CAPSOLVER_API_KEY` if you want CAPTCHA solving support
- [ ] Test a safe browser flow first with:
  `applypilot apply --dry-run`

## Development Cleanup

- [ ] Fix the existing Ruff issues in `src/`
  Current status: `ruff check src` reports 34 lint errors in the fork
- [ ] Add or restore a real test suite
  Current status: this checkout does not contain a `tests/` directory
- [ ] Decide how to handle local-only files in Git status
  Current untracked items include `.venv/` and `ApplyPilot.code-workspace`

## Suggested Verification Commands

- [x] `source .venv/bin/activate`
- [x] `applypilot --version`
- [x] `applypilot doctor`
- [x] `python -m pip check`
- [ ] `ruff check src`
