# Tri9T CT-200 Manual QA API

A focused FastAPI service that extracts the supplied CardioTrack CT-200 PDFs into a versioned section tree, pins selections to immutable node snapshots, generates structured QA test ideas, and reports whether their source text has changed.

## Features

- PyMuPDF text/layout extraction with document-specific heading and table reconstruction.
- Separate document versions and node IDs, with persistent logical IDs for exact one-to-one matches.
- SQLite storage for documents, parsed nodes, version comparisons, and pinned selections.
- Atomic JSON files for successful LLM generations and their source snapshots.
- Browse, search, change-history, selection, generation, and retrieval APIs.
- Gemini API integration with Pydantic validation, source-reference validation, and bounded retries.
- Retrieval-time stale/removed/ambiguous mapping checks. Every generated case remains marked for human QA review.

## Requirements and setup

- Python 3.11 or newer.
- Git.
- A Gemini API key only if you want the API to call a live LLM. The regular tests and default demo do not need one.
- Tesseract is not required for these supplied PDFs: all pages have selectable text. Scanned-only pages are reported as warnings; this project does not silently claim to OCR them.

From PowerShell at the project root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:GEMINI_API_KEY = "your-key"
```

`app.config` reads environment variables directly. It does not load `.env` files; set variables in the shell or use your preferred environment loader. Never commit API keys.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./ct200.db` | SQLAlchemy database URL |
| `GENERATED_STORE_PATH` | `./generated_store` | Directory for one JSON record per successful generation |
| `GEMINI_API_KEY` | unset | Gemini API key; generation returns HTTP 503 if missing |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model identifier |
| `LLM_MAX_RETRIES` | `2` | Retries after the first malformed/incomplete response; maximum total attempts is retries + 1 |

## Run the API

```powershell
python -m uvicorn app.main:app --reload
```

Open Swagger at <http://127.0.0.1:8000/docs>.

## Ingest v1, select, and generate

The initial ingestion creates version 1. Re-uploading identical PDF bytes is idempotent and returns the existing version.

```powershell
curl.exe -F "file=@data/ct200_manual.pdf" http://127.0.0.1:8000/documents/ct200/ingest
curl.exe "http://127.0.0.1:8000/documents/ct200/sections?version=1"
curl.exe "http://127.0.0.1:8000/nodes/search?q=inflation&version=1&document_id=ct200"
```

Use a `node_id` from the tree/search response in a selection. Selected nodes must all belong to the same document version.

```powershell
curl.exe -X POST http://127.0.0.1:8000/selections `
  -H "Content-Type: application/json" `
  -d "{\"name\":\"Inflation behavior\",\"document_id\":\"ct200\",\"node_ids\":[\"<v1-node-id>\"]}"

curl.exe -X POST http://127.0.0.1:8000/generations `
  -H "Content-Type: application/json" `
  -d "{\"selection_id\":\"<selection-id>\"}"
```

Successful generations are reused for a repeated request with the same selection, model, and prompt version. Send `"regenerate": true` to request a distinct generation. A malformed response is retried at most `LLM_MAX_RETRIES` times after the initial attempt; if it still fails, the API returns HTTP 502 and does not save a successful-generation record.

## Trigger the v1 → v2 flow

Upload the second supplied PDF to the same document ID. It becomes version 2; version 1 is retained.

```powershell
curl.exe -F "file=@data/ct200_manual_v2.pdf" http://127.0.0.1:8000/documents/ct200/ingest
curl.exe "http://127.0.0.1:8000/documents/ct200/sections"
curl.exe "http://127.0.0.1:8000/selections/<selection-id>"
curl.exe "http://127.0.0.1:8000/generations?selection_id=<selection-id>"
curl.exe "http://127.0.0.1:8000/generations?node_id=<v1-node-id>"
curl.exe "http://127.0.0.1:8000/nodes/<v1-node-id>/changes"
```

The selection endpoint returns its original version and exact text snapshot. Generation retrieval compares the stored source hashes against the latest version and exposes a status (`current`, `stale`, `removed_requirement`, or `ambiguous_mapping`) plus a text-change summary. “Current” means the normalized source text hash matches; it does not mean the QA idea has been approved. Human review is always required.

## End-to-end demonstration

Run the full v1 → selection → generation → v2 → stale retrieval scenario without a key using a deterministic mock model:

```powershell
python scripts\demo.py
```

To use the live Gemini API instead:

```powershell
$env:GEMINI_API_KEY = "your-key"
python scripts\demo.py --real-llm
```

The demo uses an in-memory SQLite database and a temporary generation store, so it does not alter the normal development database or generation records.

## API overview

- `POST /documents/{document_id}/ingest` — multipart PDF upload.
- `GET /documents` — known documents and latest versions.
- `GET /documents/{document_id}/versions` — retained version metadata and parser warnings.
- `GET /documents/{document_id}/sections?version=N` — nested tree; defaults to latest version.
- `GET /nodes/{node_id}` — version-specific node and descendants.
- `GET /nodes/search?q=...&version=N` — heading/body search.
- `GET /nodes/{node_id}/changes` — node comparison history.
- `POST /selections`, `GET /selections/{selection_id}` — create and retrieve immutable version-pinned selections.
- `POST /generations` — generate/reuse test ideas; `regenerate: true` bypasses reuse.
- `GET /generations?selection_id=...` or `?node_id=...` — retrieve saved output and current staleness.
- `GET /generations/{generation_id}` — retrieve a specific generation.

## Tests

```powershell
python -m pytest -v
```

Tests use the actual supplied PDFs for parser and v1→v2 assertions. The Gemini HTTP boundary is mocked; the test suite does not require network access or credentials.

## Storage and limitations

- SQLite is the source of truth for documents, versions, version-specific nodes, comparisons, and selection snapshots.
- Each successful generation is an immutable JSON file written by atomic rename. This keeps the assignment runnable without MongoDB and makes generation history inspectable. It is a local single-process store, not a distributed/concurrent production database.
- Version matching requires the same normalized heading, section number, and matched parent. Changed body text keeps the logical ID and is marked modified. Renumbered/retitled/moved sections are not fuzzily guessed; they can appear as removed plus added. Exact duplicate paths are reported ambiguous.
- Hash normalization collapses whitespace and applies Unicode NFKC, but is not semantic. A wording-only change can mark a generation stale; a changed safety threshold must also be reviewed by a human.
- OCR is intentionally not run on these text PDFs. A scanned-only/low-text page produces a warning and is not considered successfully OCR-verified.
- The parser is specialized for this document's section numbering and the observed `Parameter/Value` and `Code/Meaning/Device Behavior` tables. It is not a general-purpose PDF parser.
