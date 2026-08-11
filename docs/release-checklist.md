# Release checklist

Use this checklist before publishing `ok-bd2` to GitHub.

For one-time PyAppify/GitHub Actions/CNB setup, see
`docs/release-setup-guide.zh-CN.md`.

## Required checks

- Run `python -m unittest discover tests`.
- Run `ruff check .`.
- Confirm `pyproject.toml` contains the intended package version (currently
  `0.1.1`) and `tests/test_version_consistency.py` passes. Source runtime reads
  this value directly; the `src/config.py` release marker is reserved for the
  generated PyAppify update repository.
- Confirm the release tag identifies the intended delivery revision. It is
  inlined into the update repository and is not a second package-version field.
- Confirm `git status --ignored --short` does not show private files as normal untracked files.
- Confirm `configs/`, `logs/`, `screenshots/`, `probe_outputs/`, `.venv/`, and `upstream/` are ignored.
- Confirm automatic-login template images do not contain account information or private data.
- Replace placeholder repository links:
  - `src/config.py`
  - `pyappify.yml`
  - `README.md`, if needed

## Suggested first release

```powershell
git add .
git commit -m "Initial ok-bd2 release"
git branch -M main
git remote add origin https://github.com/GodRaymond233/ok-bd2.git
git push -u origin main
git tag v0.1.0
git push origin v0.1.0
```

## Notes

This repository includes UI screenshots/templates for image matching. Keep the
license and README disclaimer visible when publishing publicly.
