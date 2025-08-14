# Contributing

Thanks for your interest in contributing!

## Development setup
1. Create a virtual environment and install dependencies
```
python -m venv .venv
. ./.venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt
```

2. Install pre-commit hooks
```
pre-commit install
```

3. Run the app locally
```
# Optional: secure local API
$env:API_TOKEN = "dev-token"
python main.py
```

4. Run tests and linters
```
pytest -q
ruff check .
black --check .
mypy .
```

## Guidelines
- Keep PRs focused; add tests where possible (FastAPI TestClient is preferred)
- Follow the code style enforced by ruff/black and mypy strict typing
- Prefer async-friendly UI patterns (don’t block the Qt main thread)
- Avoid introducing heavy dependencies; use requirements-dev.txt for dev-only tools
- For device automation code, provide mockable interfaces for testing

## Architecture notes
- API + worker + scheduler + GUI, communicating over HTTP
- Device automation via uiautomator2/adb; GUI mirrors with scrcpy
- Jobs are claimed atomically via SQLite UPDATE...RETURNING or transactional fallback
- Configuration is YAML; DB and logs under artifacts/

