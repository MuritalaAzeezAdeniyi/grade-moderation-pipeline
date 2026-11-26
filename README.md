# Grade Moderation Pipeline

A restartable, auditable grade moderation pipeline built with FastAPI and Streamlit.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## How to run

Start the API in one terminal:

```powershell
uvicorn app.main:app --reload
```

Start the dashboard in a second terminal:

```powershell
streamlit run dashboard.py
```

The dashboard uses `http://127.0.0.1:8000` by default. Override it with:

```powershell
$env:STREAMLIT_BACKEND_URL="http://127.0.0.1:8000"
streamlit run dashboard.py
```

The dashboard communicates only through the API and does not access SQLite directly.

## Tests

```powershell
pytest -q
```

See `SPEC.md` for the source-of-truth requirements.
