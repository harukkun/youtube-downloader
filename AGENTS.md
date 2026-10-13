# Repository Guidelines

## Project Structure & Module Organization

This local Flask application downloads YouTube media and supports Shorts production workflows. `app.py` contains the main routes and shared integrations; `access_control.py` handles access protection. Feature packages live in `hooks/`, `cooking_audio/`, `auto_edit/`, and `article_builder/`. Keep feature-specific processing in those packages.

Jinja templates live in `templates/`; browser JavaScript, CSS, and fonts live in `static/`. Google Sheets integration lives in `sheets/Code.gs`. Tests and isolated browser fixtures live in `tests/`. Consult `docs/` for workflow setup and `deploy/` for public-serving configuration.

## Build, Test, and Development Commands

- `./run.sh --no-browser`: creates `.venv`, installs changed requirements, and starts the local server at `http://127.0.0.1:8765`. Requires Python 3.10+ and `ffmpeg`.
- `.venv/bin/pip install -r requirements.txt`: updates Python dependencies manually.
- `.venv/bin/python -m unittest discover -s tests -v`: runs the Python test suite.
- `.venv/bin/python -m unittest tests.test_upload_process -v`: runs a focused API test module.
- `node tests/test_upload_process.js` and `node tests/test_upload_script.js`: check browser-side progression and mocked Apps Script behavior.
- For upload browser tests, start `.venv/bin/python tests/upload_browser_fixture.py`, then run `node tests/test_upload_browser.js` in another terminal. Requires Playwright and Chrome.

There is no frontend build step. Restart the local server after Python changes: it runs without automatic reload.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` for Python functions and variables; use `PascalCase` for classes. Match surrounding JavaScript formatting, generally two-space indentation and `camelCase`. Preserve existing API field names and Korean user-facing text. No repository-wide formatter or linter is configured; avoid unrelated formatting changes.

## Testing Guidelines

Python tests use `unittest` and mocks; JavaScript checks use Node assertions, with Playwright for browser flows. Name tests `tests/test_<feature>.py` or `tests/test_<feature>.js`. Add regression coverage for behavioral fixes and run affected suites. No numeric coverage threshold is configured. Mock Sheets, Drive, and AI calls; use isolated fixtures instead of production writes.

## Commit & Pull Request Guidelines

Follow history's concise, imperative commit subjects, such as `Add ...`, `Fix ...`, or `Improve ...`. Keep commits focused. PRs should explain the problem, resulting behavior, and validation commands; link relevant issues and include screenshots for visible UI changes. Call out Apps Script deployment requirements when applicable.

## Security & Configuration

Local settings and history live under `~/.youtube-downloader/`. Never commit credentials, writer tokens, personal configuration, or generated media. Use `run-public.sh` and the documented HTTPS setup for external access.
