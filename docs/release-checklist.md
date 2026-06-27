# Release checklist

Use this checklist before publishing `ok-bd2` to GitHub.

## Required checks

- Run `python -m unittest discover tests`.
- Run `ruff check .`.
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
