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
