# AGENTS.md - Sanmitra Backend Guardrails

This repository is backend-only. Treat this file as mandatory operating policy for all AI coding assistants (Codex, Claude Code, Cursor, etc.).

## Repository Scope
- This repo owns backend code and backend CI/CD only.
- `external-repos/` contains separate frontend repositories and must not be tracked in this repo.

## Hard Safety Rules
- Never run broad commit commands such as `git add .` from repo root.
- Stage only explicit files you changed, by exact path.
- Never commit secrets (`.env`, API keys, private keys, tokens, passwords).
- Never modify deployment credentials in code files.
- Never rewrite git history on shared branches.

## Change Boundaries
- Allowed backend code paths: `app/`, `scripts/`, `tests/`, `.github/workflows/`, docs.
- Do not add tracked files under `external-repos/`.

## Required Local Validation Before Commit
1. `python -m compileall app scripts tests`
2. `python scripts/check_text_integrity.py app scripts .github/workflows`
3. `python scripts/check_repository_safety.py`
4. `python -m pytest`

## PR / Commit Checklist
- Explain purpose in 2-3 lines.
- List files changed.
- Mention test commands run and result.
- Mention rollback approach.

## Versioning
- Use semantic versioning tags for releases:
  - Backend tags: `backend-vMAJOR.MINOR.PATCH`
- Never deploy from untagged unknown state.

## Owner Override
If an emergency requires bypassing any non-security check, document the reason in PR notes.
