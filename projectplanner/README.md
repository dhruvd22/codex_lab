# Project Planner

## What it does
Project Planner ingests deep research documents, extracts a structured build plan, and emits sequenced prompts for an AI coding agent. The backend parses PDFs/Markdown/DOCX, computes embeddings, and orchestrates a deterministic Planner → Decomposer → Reviewer chain. Results are stored durably and surfaced through a Next.js + shadcn-inspired UI for editing, quality review, and exporting.

## Quickstart

### Local development
```bash
# Backend
cd projectplanner
uvicorn projectplanner.api.main:app --reload

# Frontend
cd projectplanner/ui
npm install
npm run dev
```

### Docker
```bash
docker build -t projectplanner .
docker run --env-file projectplanner/.env.example -p 8000:8000 projectplanner
```

## Environment variables
- `OPENAI_API_KEY` (required for live embeddings; deterministic fallback used when omitted)
- `DATABASE_URL` (optional Postgres connection string)
- `USE_PGVECTOR` (optional, default `false`; set `true` with Postgres + pgvector)
- `NEXT_PUBLIC_API_URL` (UI → API base URL)
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (optional tracing hooks)

## Commands
- `uvicorn projectplanner.api.main:app` – run the FastAPI service
- `npm run dev` in `projectplanner/ui` – launch the Next.js UI
- `pytest` – backend tests
- `vitest` – UI unit tests
- `npx playwright test` – end-to-end coverage hook (configure before running)

## Export formats
- `yaml` – hierarchical `plan`, `steps`, `report`
- `jsonl` – line-delimited records (`plan`, `step`, `report`)
- `md` – human-readable Markdown brief

### Example `prompts.yaml`
```yaml
plan:
  context: |
    Revamp onboarding while preserving existing auth flows.
  goals:
    - Deliver guided onboarding tasks
  assumptions:
    - API authentication remains unchanged
  non_goals:
    - No billing changes
  risks:
    - Tight regulatory review
  milestones:
    - Milestone 1: Confirm requirements and domain assumptions
steps:
  - id: step-001
    title: Milestone 1: Confirm requirements and domain assumptions
    expected_artifacts:
      - Create clarified requirements doc
```
