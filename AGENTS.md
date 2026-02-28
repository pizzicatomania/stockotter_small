# AGENTS.md — StockOtter Small (Codex/Agent Rules)

This file defines the default working agreement for automated agents (Codex) and humans.
If there is any conflict, `docs/CODEX_RULES.md` takes precedence.

## 0) Golden Rules (Non-negotiable)

### Scope / Change Hygiene
- Work on **one GitHub issue per branch/PR**. Do not mix issues.
- Make the **smallest change** that solves the issue. Avoid drive-by refactors.
- Do not change public CLI behavior unless the issue explicitly asks for it.
- Avoid adding new dependencies. If you think you must, **stop** and explain why.

### Security / Privacy
- Do **not** read, print, or exfiltrate secrets:
  - `.env`, SSH keys, tokens, credentials, or any file that looks like secrets.
- Do not add logging that may reveal secrets.
- Do not make arbitrary external network calls (curl/wget/custom HTTP) unless explicitly required by the task.
  - (The app itself may use `requests`, but agents should not fetch random external data for “investigation.”)

### Repo Conventions
- Python >= 3.11
- Packaging: `pyproject.toml` (project name: `stockotter_small`)
- Source code under `src/stockotter_small/`
- Tests under `tests/`

## 1) Setup (Preferred)

Use a virtualenv.
- Create venv (example):
  - `python -m venv .venv`
  - `source .venv/bin/activate`

Install package + dev tools (preferred):
- `pip install -U pip`
- `pip install -e ".[dev]"`

Alternative (if using requirements files):
- `pip install -r requirements.txt`
- `pip install -r requirements-dev.txt`

## 2) Commands (Quality Gates)

Before declaring a task done, run:
1) Unit tests:
- `pytest -q`

2) Lint:
- `ruff check .`

Optional (if formatting is adopted in this repo):
- `ruff format .`

Minimum sanity check if tests are not applicable:
- `python -m compileall src`

## 3) CLI Entry Points

The project exposes a console script:
- `stockotter-small`  (maps to `stockotter_small.cli:main`)

Also available as a module:
- `python -m stockotter_small`

If you change CLI behavior, update `README.md` accordingly.

## 4) Standard Agent Workflow (Issue -> Branch -> PR)

Given an issue #N:
1) Create branch:
- `git checkout main && git pull --ff-only`
- `git checkout -b codex/issue-N`

2) Implement:
- Read only relevant files (minimize surface area).
- Keep edits focused.

3) Validate:
- Run `pytest -q`
- Run `ruff check .`
- If you modified packaging/entrypoints, run:
  - `python -m stockotter_small --help` (or equivalent)
  - `stockotter-small --help`

4) Commit:
- Message: `Fix #N: <issue title>`

5) PR description MUST include:
- Summary of change
- How to test (commands)
- Results (pass/fail output summary)
- Risk/notes (if any)
- Closing keyword: `Closes #N`

## 5) When Blocked

If you cannot proceed without clarification:
- Stop and write:
  - What you tried
  - What you observed (errors/logs)
  - The smallest set of follow-up questions OR a proposed safe default
Do NOT “guess big” and implement speculative changes.

## 6) Testing Philosophy for This Repo

- Prefer small smoke tests in `tests/test_smoke.py` style when possible.
- If a bug is fixed, add/adjust a test that would fail before and pass after.
- Keep tests deterministic; do not rely on external network calls.