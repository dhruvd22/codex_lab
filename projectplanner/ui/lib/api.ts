export type IngestionRequest = {
  url?: string;
  text?: string;
  file_id?: string;
  format_hint?: "pdf" | "md" | "docx";
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
};

export type StepsResponse = {
  run_id: string;
  steps: PromptStep[];
};

export type ExportRequest = {
  run_id: string;
  format: "yaml" | "jsonl" | "md";
};

export type PlanEventHandler = (event: string, data: unknown) => void;

const envApiBase = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");

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
  return http<IngestionResponse>("/api/projectplanner/ingest", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function generatePlan(
  payload: PlanRequest,
  onEvent?: PlanEventHandler,
): Promise<PlanResponse> {
  const response = await fetch(resolveApiUrl("/api/projectplanner/plan"), {
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
    let working = buffer.replace(/

/g, "
");
    if (flush && working && !working.endsWith("

")) {
      working = `${working}

`;
    }
    let boundary = working.indexOf("

");
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
          };
        }
      }
      boundary = working.indexOf("

");
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
  const payload = finalPayload;
  if (!payload) {
    throw new Error("Planning stream ended without a final plan event.");
  }
  return payload;
}

function parseServerSentEvent(payload: string): { eventType: string; data: unknown } {
  const lines = payload.split("
");
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
  const dataString = dataLines.join("
");
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
  return http<StepsResponse>(`/api/projectplanner/steps/${runId}`);
}

export async function exportPrompts(payload: ExportRequest): Promise<Blob> {
  const response = await fetch(resolveApiUrl("/api/projectplanner/export"), {
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
  return http<StepsResponse>(`/api/projectplanner/steps/${runId}`, {
    method: "PUT",
    body: JSON.stringify({ steps }),
  });
}


