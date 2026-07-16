# Local Camoufox Turnstile Solver

YesCaptcha-compatible Turnstile solver using **Camoufox** (no paid YesCaptcha).

## Start

```bash
cd turnstile-solver
# Linux/macOS
bash start.sh

# Windows (PowerShell) — create venv then:
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m camoufox fetch
.\.venv\Scripts\python api_solver.py --browser_type camoufox --thread 1 --host 127.0.0.1 --port 5072
```

Default URL: `http://127.0.0.1:5072`

## grokreg protocol mode

```json
{
  "browser_engine": "protocol",
  "captcha_provider": "local",
  "local_solver_url": "http://127.0.0.1:5072"
}
```

```bash
uv run python -m grokreg protocol-run --count 1
```

API is YesCaptcha-compatible (`/createTask`, `/getTaskResult`, `/health`).
