# Project Planner

A deployable Project Planner experience combining a FastAPI backend and a statically-exported Next.js UI. The default service exposes:

- `GET /healthz` for basic health checks.
- `GET /` serving the Project Planner web UI.
- `API` endpoints under `/api/projectplanner/...` for ingestion, planning, exporting, and run management.

## Repository Layout
```text
.
|- app/main.py                # Boots the Project Planner API (and serves the UI when built)
|- projectplanner/api         # FastAPI routers, middleware, and persistence layer
|- projectplanner/ui          # Next.js UI source that talks to the API
|- Dockerfile                 # Multi-stage build: compiles UI then serves via uvicorn
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
2. **Front-end assets** (optional for local dev, required for production parity)
   ```powershell
   npm install --prefix projectplanner/ui
   npm run build --prefix projectplanner/ui
   ```
   The build outputs to `projectplanner/ui/out`, which FastAPI automatically serves.
3. **Run the service**
   ```powershell
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
4. Open `http://localhost:8000/` to use the planner. API routes remain available at `http://localhost:8000/api/projectplanner/*`.

### Environment Variables
- `DATABASE_URL` — optional Postgres connection string. Defaults to SQLite under `projectplanner/data/`.
- `PROJECT_PLANNER_UI_DIST` — override the directory that contains the static UI build.
- `OPENAI_API_KEY` — enables real embeddings; otherwise deterministic hashes are used.

## Docker
```powershell
docker build -t projectplanner .
docker run --rm -p 8000:8000 projectplanner
```
The image installs Python dependencies, builds the Next.js UI, copies the static export, and launches uvicorn.

## Deploying to Koyeb
1. Create the app if it does not exist yet:
   ```bash
   koyeb app create projectplanner
   ```
2. Deploy (or update) the service using the manifest:
   ```bash
   koyeb service deploy planner --app projectplanner --manifest ./koyeb.yaml
   ```
3. Optionally set secrets such as `OPENAI_API_KEY` or `DATABASE_URL` through the Koyeb dashboard or CLI.

Once the service is live, navigating to the service URL renders the Project Planner UI by default, backed by the FastAPI endpoints.
