# Project Planner

A deployable Project Planner experience driven by a FastAPI backend, SQLAlchemy persistence layer, and a statically-exported Next.js (TypeScript) UI. The service comes with end-to-end function-call tracing that records every application function invocation to the in-memory log buffer for observability.

- `GET /healthz` exposes a lightweight health probe.
- `GET /` serves the Project Planner UI when a static build is available.
- `API` endpoints live under `/api/projectplanner/...` for ingestion, planning, execution graph synthesis, and run management.

## Repository Layout
```text
.
|- app/main.py                # Boots the Project Planner API (and serves the UI when built)
|- projectplanner/api         # FastAPI routers, middleware, and persistence layer
|- projectplanner/agents      # Coordinator, planner, decomposer, reviewer GPT helpers with fallbacks
|- projectplanner/services    # Ingestion, plan storage, and review workflows
|- projectplanner/ui          # Next.js UI source that talks to the API
|- Dockerfile                 # Multi-stage build: installs deps, builds UI, runs uvicorn
|- koyeb.yaml                 # Declarative Koyeb service definition
|- requirements.txt           # Python dependencies for API + static delivery
```

## Architecture Overview
Project Planner pairs a FastAPI backend with a statically exported Next.js UI. The backend coordinates document ingestion, multi-agent planning, persistent storage, and the observability layer that powers the live dashboard and log APIs.

- `projectplanner/ui` builds the Next.js front-end and exports static assets that FastAPI serves from `app/main.py`.
- `app/main.py` bootstraps FastAPI, mounts the UI, adds CORS + rate limiting middleware, and exposes the `/api/projectplanner/*` routes.
- `projectplanner/services` hosts ingestion, planning, export, observability, and storage services that encapsulate domain logic.
- `projectplanner/agents` contains the deterministic Coordinator -> Planner -> Decomposer -> Reviewer agent chain responsible for producing project plans and quality reports.
- `projectplanner/logging_utils.py` centralizes structured runtime logging, prompt tracing, and in-memory buffers consumed by the observability features.

```
+----------------------+        +-----------------------------+
|  Browser (Next.js)   |<------>|  Static UI build (`ui/out`) |
+----------+-----------+        +-------------+---------------+
           | HTTPS & SSE calls                |
           v                                   | served by
+-------------------------------------------------------------+
| FastAPI app (`app/main.py`)                                    |
| - CORS, rate limiting, tracing middleware                      |
| - Router `/api/projectplanner/*`                               |
| - StaticFiles mount for exported UI bundles                    |
+-----------+-------------------------+-------------------------+
            |                         |                         |
            |                         |                         |
            v                         v                         v
   +--------------------+   +---------------------+   +---------------------+
   | Ingestion service  |   | Planning service    |   | Observability svc   |
   +----------+---------+   +----------+----------+   +----------+----------+
              |                    orchestrates agents                |
              v                           |                           |
     +---------------------+              v                           |
     | ProjectPlannerStore |<---+  +---------------------------+      |
     |                     |    |  | Agents (`agents/*`)       |      |
     | SQLite / Postgres   |    |  | Coordinator -> Planner -> |      |
     +---------+-----------+    |  | Decomposer -> Reviewer    |      |
               |                |  +---------------------------+      |
               | persists runs, |                                   consumes
               | chunks, plans  |                                   structured logs
               v                v                                         v
        +-------------------------------+                    +-----------------------+
        | Export bundle builder         |                    | Logging buffers & API |
        | (YAML / JSONL / Markdown)     |                    | (`logging_utils`)     |
        +-------------------------------+                    +-----------------------+
```

The UI and agents both leverage the shared logging utilities so runtime metrics, prompt transcripts, and recent module calls surface through `GET /api/projectplanner/logs` and `/observability`.

## Execution Flow
### Document ingestion
1. The UI (or an API client) submits a document to `POST /api/projectplanner/ingest`, providing inline text, a URL, or a previously uploaded file id.
2. `projectplanner.services.ingest.ingest_document` loads and normalizes the source, collapses extraneous whitespace, and chunks the text with configured size and overlap thresholds.
3. Each chunk is deduplicated and embedded. When `OPENAI_API_KEY` is present, the service requests OpenAI embeddings; otherwise it falls back to a deterministic hash-based vector so downstream workflows still function.
4. `ProjectPlannerStore` persists the run metadata, chunk payloads, and embeddings to the configured database (SQLite by default, Postgres when `DATABASE_URL` is set).
5. The endpoint responds with a `run_id` and stats (`word_count`, `char_count`, `chunk_count`) that the UI uses to enable planning actions.

### Planning & review stream
1. The UI calls `POST /api/projectplanner/plan` with the `run_id` (and optional style/target stack). FastAPI returns a server-sent events stream.
2. `projectplanner.services.plan.planning_event_stream` validates the run, pulls stored chunks, and boots the generator that yields lifecycle events.
3. The Coordinator agent synthesizes ordered objectives, followed by the Planner agent generating milestone-aligned steps. The Decomposer enriches each step with prompts, inputs, and expected artifacts, and the Reviewer grades the plan, emitting strengths and concerns.
4. After every phase, results are written back through `ProjectPlannerStore` so the UI and exports share a consistent source of truth. The stream emits `*_started`/`*_completed` events plus a `final_plan` payload summarizing the persisted plan, steps, and reviewer report.
5. Export requests (`POST /api/projectplanner/export`) reuse the stored plan/steps/report bundle to produce YAML, JSONL, or Markdown downloads without re-running the agents.

### UI orchestration & observability
1. The statically exported Next.js UI consumes the SSE stream to drive progress indicators, then allows inline editing of stored steps via `PUT /api/projectplanner/steps/{run_id}`.
2. The observability dashboard queries `GET /api/projectplanner/logs` and `/observability` on intervals, rendering module health, recent runtime events, and prompt transcripts sourced from `logging_utils` buffers.
3. Shared logging decorators capture ingestion, planning, storage, and export events, enabling consistent debugging signals whether you interact through the UI or the API.


## Local Development
1. **Python environment**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate
   pip install -r requirements.txt
   ```
2. **Front-end assets** (optional for quick backend work, required for the UI)
   ```powershell
   npm install --prefix projectplanner/ui
   npm run build --prefix projectplanner/ui
   ```
   The build outputs to `projectplanner/ui/out`, which FastAPI automatically mounts as static content.
3. **Run the service**
   ```powershell
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
4. Visit `http://localhost:8000/` for the UI. API routes remain under `http://localhost:8000/api/projectplanner/*`.

### Environment Variables
- `DATABASE_URL` — optional Postgres connection string; defaults to a SQLite file under `projectplanner/data/`.
- `PROJECTPLANNER_UI_DIST` — override the directory that contains the static UI build.
- `OPENAI_API_KEY` — enables OpenAI-backed planning agents; without it, heuristic fallbacks run instead.
- `PROJECTPLANNER_COORDINATOR_MODEL`, `PROJECTPLANNER_PLANNER_MODEL`, `PROJECTPLANNER_DECOMPOSER_MODEL` — override the GPT model aliases per agent.
- `PROJECTPLANNER_LOG_LEVEL` / `PROJECTPLANNER_LOGGER_NAME` — adjust global logging configuration.
- `PROJECTPLANNER_LOG_CAPACITY` / `PROJECTPLANNER_LOG_PROMPT_PREVIEW` — tune in-memory log buffering.
- `PROJECTPLANNER_TRACE_CALLS` — set to `0`/`false` to disable the automatic function-call logger (enabled by default).

## Logging & Telemetry
- Importing `projectplanner` auto-enables function call logging via `projectplanner.logging_utils.enable_function_call_logging`, capturing every Python function entry within the `projectplanner` and `app` packages.
- Call records are retained in the in-memory log buffer and can be queried with `projectplanner.logging_utils.get_log_manager().get_logs()`.
- Use `PROJECTPLANNER_TRACE_CALLS=0` or invoke `disable_function_call_logging()` to turn the tracer off, and `enable_function_call_logging(packages=[...])` to customize the monitored modules.

## Docker
```powershell
docker build -t projectplanner .
docker run --rm -p 8000:8000 projectplanner
```
The image installs Python dependencies, builds the Next.js UI, copies the static export, and launches uvicorn with the FastAPI app.

## Deploying to Koyeb
1. Create the app if it does not exist yet:
   ```bash
   koyeb app create projectplanner
   ```
2. Deploy (or update) the service using the manifest:
   ```bash
   koyeb service deploy planner --app projectplanner --manifest ./koyeb.yaml
   ```
3. Configure secrets like `OPENAI_API_KEY`, `DATABASE_URL`, or `PROJECTPLANNER_TRACE_CALLS` through the Koyeb dashboard or CLI.

Once deployed, navigate to the service URL to reach the Project Planner UI backed by the FastAPI endpoints and the instrumented execution graph.
