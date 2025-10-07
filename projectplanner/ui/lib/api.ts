export type IngestionRequest = {
  blueprint: string;
  filename?: string;
  format_hint?: "pdf" | "md" | "docx" | "txt";
};

export type DocumentStats = {
  word_count: number;
  char_count: number;
  chunk_count: number;
};

export type IngestionResponse = {
  run_id: string;
  stats: DocumentStats;
};

export type TargetStack = {
  backend: "FastAPI";
  frontend: "Next.js";
  db: "Postgres";
};

export type PlanRequest = {
  run_id: string;
  target_stack?: TargetStack;
  style?: "strict" | "creative";
};

export type PromptPlan = {
  context: string;
  goals: string[];
  assumptions: string[];
  non_goals: string[];
  risks: string[];
  milestones: string[];
};

export type MilestoneObjective = {
  id: string;
  order: number;
  title: string;
  objective: string;
  success_criteria: string[];
  dependencies: string[];
};

export type PromptStep = {
  id: string;
  title: string;
  system_prompt: string;
  user_prompt: string;
  expected_artifacts: string[];
  tools: string[];
  acceptance_criteria: string[];
  inputs: string[];
  outputs: string[];
  token_budget: number;
  cited_artifacts: string[];
  rubric_score?: number;
  suggested_edits?: string | null;
};

export type StepFeedback = {
  step_id: string;
  rubric_score: number;
  notes: string;
};

export type AgentReport = {
  run_id: string;
  generated_at: string;
  overall_score: number;
  strengths: string[];
  concerns: string[];
  step_feedback: StepFeedback[];
};

export type PlanResponse = {
  plan: PromptPlan;
  steps: PromptStep[];
  report: AgentReport;
  objectives: MilestoneObjective[];
};

export type StepsResponse = {
  run_id: string;
  steps: PromptStep[];
};


export type LogType = "runtime" | "prompts";

export type LogEntry = {
  sequence: number;
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  type: LogType;
  run_id?: string | null;
  event?: string | null;
  payload?: Record<string, unknown> | null;
  exception?: string | null;
};

export type LogsResponse = {
  logs: LogEntry[];
  cursor: number;
};

export type ObservabilityStatus = "idle" | "healthy" | "degraded" | "error";

export type ObservabilityNode = {
  id: string;
  name: string;
  category: "endpoint" | "pipeline" | "agent" | "storage" | "service" | "orchestrator";
  description: string;
  status: ObservabilityStatus;
  event_count: number;
  run_ids: string[];
  last_event?: string | null;
  last_timestamp?: string | null;
  metrics: Record<string, unknown>;
};

export type ObservabilityEdge = {
  source: string;
  target: string;
  label?: string | null;
};

export type ObservabilityCall = {
  module_id: string;
  timestamp: string;
  level: string;
  event?: string | null;
  message: string;
  run_id?: string | null;
  log_type: LogType;
  payload?: Record<string, unknown> | null;
};

export type ObservabilitySnapshot = {
  generated_at: string;
  session_started_at: string;
  nodes: ObservabilityNode[];
  edges: ObservabilityEdge[];
  calls: ObservabilityCall[];
};

export type ExportRequest = {
  run_id: string;
  format: "yaml" | "jsonl" | "md";
};

export type PlanEventHandler = (event: string, data: unknown) => void;

const envApiBase = ((globalThis as any)?.process?.env?.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");

function resolveApiUrl(path: string): string {
  if (!envApiBase) {
    return path;
  }
  if (path === "/") {
    return envApiBase || "/";
  }
  return `${envApiBase}${path}`;
}


async function http<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(resolveApiUrl(path), {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with status ${response.status}`);
  }
  if (response.headers.get("content-type")?.includes("application/json")) {
    return (await response.json()) as T;
  }
  const text = await response.text();
  return text as unknown as T;
}

export async function ingestDocument(payload: IngestionRequest): Promise<IngestionResponse> {
  return http<IngestionResponse>("/api/codingconductor/ingest", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function generatePlan(
  payload: PlanRequest,
  onEvent?: PlanEventHandler,
): Promise<PlanResponse> {
  const response = await fetch(resolveApiUrl("/api/codingconductor/plan"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Planning failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Streaming not supported by this environment.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload: PlanResponse | null = null;

  const processBuffer = (flush: boolean) => {
    let working = buffer.replace(/\r\n/g, "\n");
    if (flush && working && !working.endsWith("\n\n")) {
      working = `${working}\n\n`;
    }
    let boundary = working.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = working.slice(0, boundary);
      working = working.slice(boundary + 2);
      if (rawEvent.trim()) {
        const { eventType, data } = parseServerSentEvent(rawEvent);
        if (onEvent) {
          try {
            onEvent(eventType, data);
          } catch (error) {
            console.warn("Plan event handler error", error);
          }
        }
        if (eventType === "final_plan" && data && typeof data === "object") {
          const hydrated = data as Record<string, unknown>;
          finalPayload = {
            plan: hydrated.plan as PromptPlan,
            steps: hydrated.steps as PromptStep[],
            report: hydrated.report as AgentReport,
            objectives: (hydrated.objectives ?? []) as MilestoneObjective[],
          };
        }
      }
      boundary = working.indexOf("\n\n");
    }
    buffer = working;
  };

  let streamComplete = false;
  while (!streamComplete) {
    const { value, done } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: !done });
      processBuffer(false);
    }
    streamComplete = !!done;
  }
  buffer += decoder.decode(new Uint8Array(), { stream: false });
  processBuffer(true);
  if (finalPayload) {
    return finalPayload;
  }
  return Promise.reject(new Error("Planning stream ended without a final plan event."));
}

function parseServerSentEvent(payload: string): { eventType: string; data: unknown } {
  const lines = payload.split("\n");
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("event:")) {
      eventType = trimmed.slice(6).trim();
    } else if (trimmed.startsWith("data:")) {
      dataLines.push(trimmed.slice(5).trim());
    }
  }
  const dataString = dataLines.join("\n");
  if (!dataString) {
    return { eventType, data: null };
  }
  try {
    return { eventType, data: JSON.parse(dataString) };
  } catch {
    return { eventType, data: dataString };
  }
}

export async function getSteps(runId: string): Promise<StepsResponse> {
  return http<StepsResponse>(`/api/codingconductor/steps/${runId}`);
}

export async function exportPrompts(payload: ExportRequest): Promise<Blob> {
  const response = await fetch(resolveApiUrl("/api/codingconductor/export"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Export failed with status ${response.status}`);
  }
  return await response.blob();
}

export async function updateSteps(runId: string, steps: PromptStep[]): Promise<StepsResponse> {
  return http<StepsResponse>(`/api/codingconductor/steps/${runId}`, {
    method: "PUT",
    body: JSON.stringify({ steps }),
  });
}

export type LogLevelFilter = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export async function fetchLogs(params: {
  after?: number;
  limit?: number;
  level?: LogLevelFilter;
  type?: LogType;
  start?: string;
  end?: string;
} = {}): Promise<LogsResponse> {
  const search = new URLSearchParams();
  if (typeof params.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (typeof params.after === "number") {
    search.set("after", String(params.after));
  }
  if (params.level) {
    search.set("level", params.level.toUpperCase());
  }
  if (params.start) {
    search.set("start", params.start);
  }
  if (params.end) {
    search.set("end", params.end);
  }
  const logType = params.type ?? "runtime";
  search.set("type", logType);
  const query = search.toString();
  return http<LogsResponse>(`/api/codingconductor/logs${query ? `?${query}` : ""}`);
}

export async function fetchObservabilitySnapshot(
  params: { limit?: number; calls?: number; start?: string; end?: string } = {},
): Promise<ObservabilitySnapshot> {
  const search = new URLSearchParams();
  if (typeof params.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (typeof params.calls === "number") {
    search.set("calls", String(params.calls));
  }
  if (params.start) {
    search.set("start", params.start);
  }
  if (params.end) {
    search.set("end", params.end);
  }
  const query = search.toString();
  return http<ObservabilitySnapshot>(`/api/codingconductor/observability${query ? `?${query}` : ""}`);
}

export async function downloadLogs(params: {
  level?: LogLevelFilter;
  type?: LogType;
  start?: string;
  end?: string;
} = {}): Promise<Blob> {
  const search = new URLSearchParams();
  if (params.level) {
    search.set("level", params.level.toUpperCase());
  }
  if (params.type) {
    search.set("type", params.type);
  }
  if (params.start) {
    search.set("start", params.start);
  }
  if (params.end) {
    search.set("end", params.end);
  }
  const query = search.toString();
  const response = await fetch(resolveApiUrl(`/api/codingconductor/logs/export${query ? `?${query}` : ""}`));
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Failed to export logs (status ${response.status})`);
  }
  return await response.blob();
}

export async function downloadPromptAudit(): Promise<Blob> {
  const response = await fetch(resolveApiUrl("/api/codingconductor/prompts/download"));
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Failed to download prompt audit (status ${response.status})`);
  }
  return await response.blob();
}

export async function downloadObservabilitySnapshot(
  params: { limit?: number; calls?: number; start?: string; end?: string } = {},
): Promise<Blob> {
  const search = new URLSearchParams();
  if (typeof params.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (typeof params.calls === "number") {
    search.set("calls", String(params.calls));
  }
  if (params.start) {
    search.set("start", params.start);
  }
  if (params.end) {
    search.set("end", params.end);
  }
  const query = search.toString();
  const response = await fetch(
    resolveApiUrl(`/api/codingconductor/observability/export${query ? `?${query}` : ""}`),
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Failed to export observability snapshot (status ${response.status})`);
  }
  return await response.blob();
}




export type OrchestratorBlueprintSummary = {
  run_id: string;
  summary: string;
  highlights: string[];
  risks: string[];
  components: string[];
  metadata: Record<string, unknown>;
};

export type OrchestratorMilestone = {
  milestone_id: number;
  details: string;
  context?: string;
};

export type OrchestratorMilestonePlan = {
  run_id: string;
  milestones: OrchestratorMilestone[];
  raw_response?: string | null;
};

export type OrchestratorMilestonePrompt = {
  milestone_id: number;
  title: string;
  system_prompt: string;
  user_prompt: string;
  acceptance_criteria: string[];
  expected_artifacts: string[];
  references: string[];
};

export type OrchestratorPromptBundle = {
  run_id: string;
  prompts: OrchestratorMilestonePrompt[];
};

export type OrchestratorGraphCoverageSnapshot = {
  run_id: string;
  covered_nodes: string[];
  uncovered_nodes: string[];
  notes?: string | null;
};

export type OrchestratorSummaryEnvelope = {
  run_id: string;
  summary: OrchestratorBlueprintSummary;
  source?: string | null;
};

export type OrchestratorMilestonesEnvelope = {
  run_id: string;
  milestones: OrchestratorMilestonePlan;
  graph: OrchestratorGraphCoverageSnapshot;
};

export type OrchestratorPromptsEnvelope = {
  run_id: string;
  prompts: OrchestratorPromptBundle;
};

export type OrchestratorApprovalStage = "summary" | "milestones";

export type OrchestratorApprovalResponse = {
  run_id: string;
  stage: OrchestratorApprovalStage;
  approved: boolean;
};

export type OrchestratorResult = {
  run_id: string;
  summary: OrchestratorBlueprintSummary;
  milestones: OrchestratorMilestonePlan;
  prompts: OrchestratorPromptBundle;
  graph_report: OrchestratorGraphCoverageSnapshot;
  generated_at: string;
};

export type OrchestratorSessionStatus = {
  run_id: string;
  source?: string | null;
  summary_ready: boolean;
  summary_approved: boolean;
  milestones_ready: boolean;
  milestones_approved: boolean;
  prompts_ready: boolean;
  created_at: string;
  updated_at: string;
};

export type OrchestratorDecisionRequest = {
  approved: boolean;
};

const ORCHESTRATOR_BASE = "/api/orchestrator";

export async function createOrchestratorRun(
  payload: IngestionRequest,
): Promise<OrchestratorSummaryEnvelope> {
  return http<OrchestratorSummaryEnvelope>(`${ORCHESTRATOR_BASE}/runs`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listOrchestratorRuns(): Promise<OrchestratorSessionStatus[]> {
  return http<OrchestratorSessionStatus[]>(`${ORCHESTRATOR_BASE}/runs`);
}

export async function getOrchestratorRun(runId: string): Promise<OrchestratorSessionStatus> {
  return http<OrchestratorSessionStatus>(`${ORCHESTRATOR_BASE}/runs/${runId}`);
}

export async function deleteOrchestratorRun(runId: string): Promise<void> {
  const response = await fetch(resolveApiUrl(`${ORCHESTRATOR_BASE}/runs/${runId}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Failed to delete orchestrator run ${runId} (status ${response.status})`);
  }
}

export async function getOrchestratorSummary(runId: string): Promise<OrchestratorSummaryEnvelope> {
  return http<OrchestratorSummaryEnvelope>(`${ORCHESTRATOR_BASE}/runs/${runId}/summary`);
}

export async function regenerateOrchestratorSummary(runId: string): Promise<OrchestratorSummaryEnvelope> {
  return http<OrchestratorSummaryEnvelope>(`${ORCHESTRATOR_BASE}/runs/${runId}/summary/regenerate`, {
    method: "POST",
  });
}

export async function submitOrchestratorSummaryDecision(
  runId: string,
  approved: boolean,
): Promise<OrchestratorApprovalResponse> {
  return http<OrchestratorApprovalResponse>(`${ORCHESTRATOR_BASE}/runs/${runId}/summary/decision`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

export async function generateOrchestratorMilestones(
  runId: string,
): Promise<OrchestratorMilestonesEnvelope> {
  return http<OrchestratorMilestonesEnvelope>(`${ORCHESTRATOR_BASE}/runs/${runId}/milestones`, {
    method: "POST",
  });
}

export async function getOrchestratorMilestones(
  runId: string,
): Promise<OrchestratorMilestonesEnvelope> {
  return http<OrchestratorMilestonesEnvelope>(`${ORCHESTRATOR_BASE}/runs/${runId}/milestones`);
}

export async function submitOrchestratorMilestonesDecision(
  runId: string,
  approved: boolean,
): Promise<OrchestratorApprovalResponse> {
  return http<OrchestratorApprovalResponse>(`${ORCHESTRATOR_BASE}/runs/${runId}/milestones/decision`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

export async function generateOrchestratorPrompts(
  runId: string,
): Promise<OrchestratorPromptsEnvelope> {
  return http<OrchestratorPromptsEnvelope>(`${ORCHESTRATOR_BASE}/runs/${runId}/prompts`, {
    method: "POST",
  });
}

export async function getOrchestratorPrompts(runId: string): Promise<OrchestratorPromptsEnvelope> {
  return http<OrchestratorPromptsEnvelope>(`${ORCHESTRATOR_BASE}/runs/${runId}/prompts`);
}

export async function finalizeOrchestratorRun(runId: string): Promise<OrchestratorResult> {
  return http<OrchestratorResult>(`${ORCHESTRATOR_BASE}/runs/${runId}/finalize`, {
    method: "POST",
  });
}

export async function getOrchestratorResult(runId: string): Promise<OrchestratorResult> {
  return http<OrchestratorResult>(`${ORCHESTRATOR_BASE}/runs/${runId}/result`);
}
