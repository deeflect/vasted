# Releasing `vasted`

This repository is prepared for `uv`-first distribution and PyPI publishing.

## 1. Preflight

Run checks locally:

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy app tests bot.py
uv run pytest -q
```

## 2. Build Artifacts

```bash
uv build
```

Optional validation:

```bash
uvx twine check dist/*
```

## 3. Smoke Test the Built Wheel

```bash
uv tool install --force dist/*.whl
vasted --help
```

## 4. Version Bump

Update versions in:

- `pyproject.toml` (`[project].version`)
- `app/__init__.py` (`__version__`)
- `CHANGELOG.md`

The test suite includes a guard to keep `pyproject.toml` and `app.__version__` aligned.

## 5. Publish (Manual Workflow)

Use GitHub Actions workflow: `Publish Package`.

- `repository = testpypi` for rehearsal
- `repository = pypi` for production
- `publish = true` to actually upload

The workflow always builds and validates the package first. Upload only runs when `publish=true`.

## 6. Post-Release

- Create/update release notes.
- Verify install path:

```bash
uv tool install vasted
vasted --version
```
