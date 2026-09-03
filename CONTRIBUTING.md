# Contributing to ReClip Plus

Thank you for your interest in improving ReClip Plus! This project follows a philosophy of minimalism, lightweight execution, and clean maintainable code.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Woffluon/reclip-plus.git
   cd reclip-plus
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install pytest ruff
   ```

4. **Ensure system dependencies are installed:**
   - `ffmpeg` (required for merging and audio extraction)
   - `yt-dlp`

5. **Run the development server:**
   ```bash
   python app.py
   ```
   Open `http://localhost:8899` in your browser.

## Running Tests

Run the test suite using `pytest`:
```bash
pytest
```

To run with verbose output:
```bash
pytest -v
```

## Code Style & Guidelines

- **Zero unnecessary dependencies:** Do not add heavy libraries, frameworks, or databases (no Redis, no Celery, no React/Vue/Svelte). Rely on standard Python, Flask, yt-dlp, and vanilla browser APIs.
- **Project Structure:** Application code lives cleanly inside the `reclip/` package (`reclip/app.py`, `reclip/__main__.py`, `reclip/downloader.py`, `reclip/jobs.py`, `reclip/utils.py`, `reclip/templates/`, `reclip/static/`). The root `app.py` serves as the lightweight entrypoint for dev servers and Gunicorn (`python app.py` or `python -m reclip`). Docker entrypoint scripts reside in `docker/`.
- **Security:** Always sanitize user input, maintain SSRF protections, and never use `shell=True` or format raw shell command strings.
- **Formatting:** Code should follow PEP 8 and pass lint checks with `ruff check .`.

## Pull Requests

1. Fork the repo and create a feature branch (`git checkout -b feature/my-improvement`).
2. Implement your changes with focused, surgical commits.
3. Add unit tests for any new logic or edge cases.
4. Verify all tests pass (`pytest`).
5. Open a Pull Request describing what was changed and why.
