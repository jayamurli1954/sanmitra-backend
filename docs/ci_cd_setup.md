# CI/CD Setup

## Added Workflows
- `.github/workflows/ci.yml`
  - Trigger: push (`main`, `develop`) and pull request
  - Steps: install deps, `python -m compileall app scripts tests`, `python -m pytest`

- `.github/workflows/render-deploy.yml`
  - Trigger: `workflow_dispatch`
  - Input: `target` (`staging` or `production`)
  - Uses deploy-hook secrets:
    - `RENDER_STAGING_DEPLOY_HOOK`
    - `RENDER_PRODUCTION_DEPLOY_HOOK`

## Production Approval Gate
- The production deploy job uses `environment: production`.
- In GitHub repo settings, configure required reviewers for the `production` environment.
- With that enabled, production runs pause for manual approval before deploy triggers.

## Required GitHub Secrets
- `RENDER_STAGING_DEPLOY_HOOK`
- `RENDER_PRODUCTION_DEPLOY_HOOK`

## Pytest Isolation
- `pytest.ini` is configured to run only `tests/` and ignore `external-repos/`.
- This prevents cross-repo test collection conflicts in CI.
