# Koyeb FastAPI Demo

## Overview
- Minimal FastAPI application serving `{ "message": "Hello from Koyeb" }` on the root endpoint.
- Docker-based workflow tuned for quick deployment to Koyeb.
- Includes manifest, dependency, and ignore files to streamline CI/CD pipelines.

## Project Layout
```
.
|- .dockerignore       # Excludes caches, compiled files, and virtualenvs from Docker context
|- Dockerfile          # Builds the FastAPI app on python:3.11-slim and runs uvicorn on port 8000
|- koyeb.yaml          # Koyeb deployment manifest targeting the Dockerfile and exposing HTTP
|- requirements.txt    # FastAPI + uvicorn runtime dependencies
\- app/
   \- main.py         # Root endpoint returning the greeting JSON payload
```

## Local Run
1. `python -m venv .venv && .venv\\Scripts\\activate`
2. `pip install -r requirements.txt`
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000`
4. Visit `http://localhost:8000/` and confirm the JSON response.

## Docker Build & Test
1. `docker build -t fastapi-koyeb .`
2. `docker run --rm -p 8000:8000 fastapi-koyeb`
3. Hit `http://localhost:8000/` to verify the container output.

## Deploy to Koyeb
1. `koyeb app create koyeb-fastapi`
2. `koyeb service deploy fastapi-service --app koyeb-fastapi --dockerfile ./Dockerfile --ports 8000:http`
3. `koyeb service url fastapi-service --app koyeb-fastapi`

Use the final command to retrieve the live URL for the API.
