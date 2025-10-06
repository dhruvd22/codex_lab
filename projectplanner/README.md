# The Coding Conductor

## What is projectplanner?
The Coding Conductor ingests architecture, design, and requirements blueprints, normalizes the source into actionable context, and emits a sequential, high-signal prompt plan tailored for autonomous AI coding agents. Feed it system briefs, RFCs, or discovery notes and it returns a run book the agent can execute without backtracking.

## Architecture summary
Multi-agent workflow with a deterministic Planner -> Decomposer -> Reviewer graph, orchestrated in code rather than loose chat loops. FastAPI serves the API, the Next.js UI wraps the review and edit surface, and SQLite handles local storage with optional Postgres when you need scale. Deterministic graph execution combined with durable orchestration keeps outputs stable and avoids flaky "agent chatter."

The module now ships with a built-in observability layer. Every Agent, API surface, and storage hop emits structured logs that are collected into an interactive UI dashboard and a `/observability` API snapshot so you can spot latency regressions or unhealthy modules before shipping changes.

## Install & Run
1. Clone the repo, create a virtual environment, and install the API module.
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e projectplanner/api
   uvicorn projectplanner.api.main:app --reload
   ```
2. Spin up the UI from a second shell.
   ```bash
   cd projectplanner/ui
   npm install
   npm run dev
   ```

### Docker Compose
Drop the snippet below into `docker-compose.yml` (or extend an existing file) to run the API and UI together.
```yaml
version: "3.9"
services:
  api:
    build: .
    command: uvicorn projectplanner.api.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    env_file:
      - projectplanner/.env
  ui:
    build:
      context: projectplanner/ui
    command: npm run dev
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: "http://api:8000"
    depends_on:
      - api
```

## Environment variables
Copy the template and edit values before running (`cp projectplanner/.env.example projectplanner/.env`).
- **OPENAI_API_KEY** — required for agent calls.
- **CODING_CONDUCTOR_MAX_COMPLETION_TOKENS** - optional upper bound for agent completion tokens (defaults to 16384).
- **DATABASE_URL** — optional Postgres connection string (e.g. `postgresql://user:pass@host:5432/db`).
- **NEXT_PUBLIC_API_URL** — the UI’s base URL for the API (e.g. `http://localhost:8000`).
- **LANGFUSE_PUBLIC_KEY**, **LANGFUSE_SECRET_KEY** — optional observability keys if you wire up Langfuse.

## API
- `POST /api/codingconductor/ingest`
  - **Request**: `{ "blueprint": string, "filename"?: string, "format_hint"?: "pdf" | "md" | "docx" | "txt" }`
  - **Response**: `{ "run_id": string, "stats": { "word_count": number, "char_count": number, "chunk_count": number } }`
- `POST /api/codingconductor/plan`
  - **Content-Type**: `application/json`, **Response Content-Type**: `text/event-stream`
  - **Request**: `{ "run_id": string, "target_stack"?: { "backend": "FastAPI", "frontend": "Next.js", "db": "Postgres" }, "style"?: "strict" | "creative" }`
  - **Stream Payload**: server-sent events for `planner_started`, `planner_completed`, `decomposer_completed`, `reviewer_completed`, followed by a `final_plan` event that mirrors the schema below.
  - **Final Payload**: `{ "plan": PromptPlan, "steps": PromptStep[], "report": AgentReport }` persisted to storage.
- `PUT /api/codingconductor/steps/{run_id}` and `GET /api/codingconductor/steps/{run_id}`
  - **Request** (`PUT`): `{ "steps": PromptStep[] }`
  - **Response**: `{ "run_id": string, "steps": PromptStep[] }`
- `GET /api/codingconductor/observability`
  - **Query**: `limit` (runtime log sample size, default 400), `calls` (recent module call limit, default 120).
  - **Response**: `{ "generated_at": string, "nodes": ObservabilityNode[], "edges": ObservabilityEdge[], "calls": ObservabilityCall[] }` representing the live workflow graph.

- `POST /api/codingconductor/export`
  - **Request**: `{ "run_id": string, "format": "yaml" | "jsonl" | "md" }`
  - **Response**: streamed attachment matching the requested format with `Content-Disposition: attachment`.

> **PromptPlan**: `{ "context": string, "goals": string[], "assumptions": string[], "non_goals": string[], "risks": string[], "milestones": string[] }`
>
> **PromptStep**: `{ "id": string, "title": string, "system_prompt": string, "user_prompt": string, "expected_artifacts": string[], "tools": string[], "acceptance_criteria": string[], "inputs": string[], "outputs": string[], "token_budget": number, "cited_artifacts": string[], "rubric_score"?: number, "suggested_edits"?: string }`
>
> **AgentReport**: `{ "run_id": string, "generated_at": string (ISO8601), "overall_score": number, "strengths": string[], "concerns": string[], "step_feedback": { "step_id": string, "rubric_score": number, "notes": string }[] }`

## UI
![The Coding Conductor UI placeholder](docs/screenshot-placeholder.png)
1. Upload the architecture, design, and requirements blueprint for your application.
2. Click **Generate Plan** to start the stream and watch the Planner, Decomposer, and Reviewer progress.
3. Inspect milestones and edit any step titles, prompts, or acceptance criteria inline.
4. Export your refined plan as YAML, JSONL, or Markdown for the downstream agent.
5. Hop into the **Observability** tab to monitor module health, latency trends, and the latest calls for each component.

### Observability dashboard
- Grouped cards show **Endpoints**, **Pipeline**, **Storage**, **Agents**, and **Services** with live status derived from recent log levels.
- Latency metrics (`avg_latency_ms`, `p95_latency_ms`, `last_latency_ms`) are computed from paired start/end events per module.
- The flow list highlights upstream/downstream edges touching the selected module so you can follow execution.
- The call log surfaces the most recent runtime and prompt events, including payload previews, run ids, and severity levels.
- Auto-refresh is enabled by default (10 second interval) and can be toggled while you investigate a noisy run.

## Export formats
- **YAML** — canonical hierarchy with `plan`, `steps`, and optional `report` blocks.
- **JSONL** — line-delimited records (`plan`, each `step`, optional `report`).
- **Markdown** — narrative summary for humans.

**Field guide** (shared across formats):
- `plan.context`: short problem framing.
- `plan.goals`: definitive outcomes.
- `plan.assumptions`: explicit truths we rely on.
- `plan.non_goals`: out-of-scope commitments.
- `plan.risks`: flagged concerns.
- `plan.milestones`: high-level sequencing.
- `steps[].expected_artifacts`: files or docs we expect back.
- `steps[].acceptance_criteria`: quality bar to auto-evaluate agent outputs.
- `observability.nodes[]`: per-module health, event counts, recent run ids, and derived latency stats.
- `observability.edges[]`: directional relationships between modules (source -> target).
- `observability.calls[]`: recent log-derived invocations including level, event, payload preview, and run association.

Example YAML export snippet:
```yaml
plan:
  context: |
    Modernize billing without breaking existing invoice APIs.
  goals:
    - Support usage-based pricing
  assumptions:
    - Existing auth stays intact
  non_goals:
    - No ERP migration
  risks:
    - Finance sign-off is late
  milestones:
    - Milestone 1: Ratify billing rules
steps:
  - id: milestone-1-step-1
    title: Draft billing domain glossary
    expected_artifacts:
      - docs/billing-glossary.md
    acceptance_criteria:
      - Aligns with finance terminology
```

## Quality & Limits
- Deterministic agent order with hard-coded graph edges.
- Every response validated against Pydantic schemas before persistence.
- Token budgets enforced per step; configurable via `PromptStep.token_budget`.
- Safety rails on input size (2 MiB body cap) and per-minute rate limiting.

## Prompt evaluation
We keep golden prompt regressions in Promptfoo. Run them locally after edits:
```bash
promptfoo eval -c projectplanner/evals/promptfoo.yaml
```

## Cost controls
- Planning agents run at temperature `0` with bounded `max_tokens`.
- Decomposer halts early if reviewer rubric drops below the acceptance threshold.

## Security
- No secrets checked into source control; all keys read via environment variables.
- FastAPI request path only calls OpenAI for planning.
- Postgres credentials stay in `.env`; sanitize anything before logging.

## CI hooks
Add a GitHub Actions workflow at `.github/workflows/projectplanner.yml` that runs `pip install -e projectplanner/api`, `pytest`, `npm ci`, `npm run test`, and `npx playwright test` to keep the planner green.



