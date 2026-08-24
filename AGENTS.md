# Repository Guidelines

## Project Structure & Module Organization

Odysseus is a self-hosted FastAPI workspace. The application entry point is
`app.py`. Keep HTTP concerns in `routes/`, reusable domain logic in `services/`,
and shared application code in `src/` and `core/` (the latter includes
compatibility exports and infrastructure such as auth and database handling).
The browser UI lives in `static/`, primarily modular ES JavaScript under
`static/js/`. Operational scripts are in `scripts/`; Docker and search-service
configuration are at the repository root and under `config/`. Put tests in
`tests/`, using `test_<behavior>.py`; focused CLI and helper tests belong in
`tests/cli/` and `tests/helpers/`.

## Build, Test, and Development Commands

Use Docker for the normal local stack:

```bash
cp .env.example .env
docker compose up -d --build     # build and start at http://localhost:7000
docker compose logs --tail=120 odysseus
```

For native development (Python 3.11+), create `venv`, install
`requirements.txt`, then run `python -m uvicorn app:app --host 127.0.0.1 --port 7000`.
Validate backend changes with `./venv/bin/python -m pytest` and compile only
the changed Python files with `./venv/bin/python -m py_compile <files>`. For a
JavaScript change, run `node --check static/js/<file>.js` when Node is present.

## Coding Style & Naming Conventions

Follow the surrounding code: four-space Python indentation, `snake_case` for
functions/modules, `PascalCase` for classes, and descriptive `test_*` names.
Use existing constants and helpers rather than duplicating paths, ports, limits,
or loopback URLs; fixed persisted locations belong in `src/constants.py`.
JavaScript modules use ES exports, `camelCase`, and two-space indentation.
For UI work, reuse the existing CSS variables/components, retain the Fira Code
look, and do not add Unicode emoji. No repository-wide formatter or linter is
configured, so keep diffs narrowly formatted like neighboring code.

## Testing Guidelines

Pytest discovers `tests/` and uses `pytest-asyncio` automatically. Prefer the
smallest relevant run, for example `./venv/bin/python -m pytest tests/test_auth_regressions.py`.
Taxonomy-focused runs are available via
`./venv/bin/python tests/run_focus.py --area security`; run the full suite for
cross-cutting changes. Keep tests deterministic and isolated; do not mask
failures with `skip` or `xfail`.

## Commit & Pull Request Guidelines

Target PRs to `dev`, keep each one focused, and describe behavior, validation,
and linked issues (for example, `Fixes #123`). Include screenshots or recordings
for any UI change, including mobile when applicable. Commit messages follow
Conventional Commits, such as `fix(search): preserve fetch failures`; use a
short imperative subject and explain non-obvious rationale in the body. Never
commit secrets, API keys, private logs, or personal documents.
