# Inbox Triage Agent

An autonomous email classification and drafting assistant. It reads unread
Gmail messages, classifies them by urgency and type, drafts replies for
routine categories, and surfaces genuinely ambiguous messages in a daily
digest for human review.

See [docs/BRD.docx](docs/BRD.docx) for full scope, requirements, and success
metrics.

## Status

- **FR1** Gmail OAuth2 (readonly + compose scope) — done
- **FR2** Paginated unread fetch — done
- **FR3/FR4** Batched Claude classification with low-confidence force-flagging — done
- **FR6** Ambiguous-email digest — done
- **FR5** Scheduling draft generation with sender-history tone context — done
- **FR7** Gmail draft creation (proper reply threading, opt-in, never sends) — done
- **FR8** Classification/digest/draft outcomes logged to SQLite — done
- Stage 7 UI: FastAPI backend + custom HTML/CSS/JS frontend — done
- Remaining: stage 6 (dogfooding + timed measurement)

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `GOOGLE_CLIENT_SECRETS_PATH` — path to your Gmail OAuth client secret JSON
     (see `secrets/`)
   - `ANTHROPIC_API_KEY` — your Claude API key
2. Install dependencies: `uv sync`

## Usage

Run commands from the project root, with `src` on the Python path:

```
PYTHONPATH=src uv run python -m cli fetch-unread --limit 50
PYTHONPATH=src uv run python -m cli classify --limit 50
PYTHONPATH=src uv run python -m cli digest --limit 50
PYTHONPATH=src uv run python -m cli draft --limit 50
PYTHONPATH=src uv run python -m cli draft --message-id <id> --create-gmail-draft
```

Pass `--limit 0` to fetch/classify the entire unread backlog instead of a
capped sample. `draft --message-id` targets one specific email instead of
the unread batch; add `--create-gmail-draft` to actually create the
generated reply as a real Gmail draft (opt-in, never sends — default is
print-only). The first run opens a browser window for Gmail OAuth consent;
after that, credentials are cached in `token.json`. Adding a new scope
(as stage 5 did) triggers one re-consent automatically.

### Web UI (stage 7)

Run the FastAPI backend from the project root, not from `src/` --
`token.json`, `.env`'s secrets path, the SQLite db, and `settings.json`
are all resolved relative to the current working directory, same as the
CLI. `--app-dir src` makes `api.main:app` importable without changing
that directory:

```
uv run uvicorn api.main:app --app-dir src --reload
```

Then open http://127.0.0.1:8000/ — the backend serves the frontend
directly, so there's no separate server or build step. Settings (fetch
limit, confidence threshold, dry-run toggle) are edited from the gear
icon and persisted to `settings.json`.
