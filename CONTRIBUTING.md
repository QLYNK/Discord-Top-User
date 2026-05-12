# Contributing to Discord-Top-User

Thank you for your interest in improving Discord-Top-User.

## Ground Rules

- Keep PRs scoped and focused.
- Never commit secrets, private tokens, or personal data.
- Maintain modular structure and clear naming.
- Update docs when behavior changes.

## Setup

```bash
pip install -r requirements.txt
python main.py
```

## Development Workflow

1. Fork repository and create a feature branch.
2. Make small, reviewable commits.
3. Run local validation:

```bash
python -m compileall .
```

4. Open PR with clear summary and test notes.

## Pull Request Requirements

- Explain *what changed* and *why*.
- Include impacted modules/cogs.
- Attach screenshots/logs if UI/dashboard changed.
- Confirm no secrets were introduced.

## Coding Expectations

- Favor typed, modular, production-safe Python.
- Use explicit error handling for external I/O paths.
- Avoid unnecessary refactors in unrelated files.

## Reporting Bugs & Features

Please use:
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
