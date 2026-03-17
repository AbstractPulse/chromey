# Publishing Notes

This `public_publish/` folder is the cleaned project snapshot for a GitHub repo.

## Included

- app source under `src/chromey`
- CLI entrypoint in `main.py`
- Chrome extension under `extension/chromey-extension`
- packaging files: `pyproject.toml`, `uv.lock`
- conda bootstrap file: `environment.yml`
- docs and config example

## Excluded

- `.venv/`
- `old/`
- `back up/`
- local caches and compiled files

## Before Publishing

1. Choose the final repository name and remote URL.
2. Test from a clean checkout with `uv sync` and `uv run python main.py proxy --launch-browser`.

## Notes

- User-facing branding is `Chromey`.
- Browser requests are restricted to the installed Chromey extension origin.
- Package, extension, and local config paths all use `chromey` consistently in this publish tree.
