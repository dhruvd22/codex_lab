import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { downloadPromptAudit, fetchLogs, type LogEntry } from "@/lib/api";

const AUTO_REFRESH_INTERVAL_MS = 5000;
const MAX_PROMPT_ENTRIES = 100;

type PromptPayload = {
  agent: string;
  role?: string;
  stage?: string;
  preview: string;
  truncated: boolean;
  chars?: number;
  model?: string;
  metadata?: unknown;
};


type PromptTimelineEntry = {
  log: LogEntry;
  payload: PromptPayload;
};

type PromptInteraction = {
  id: string;
  agent: string;
  runId: string | null;
  startedAt: string;
  latestAt: string;
  entries: PromptTimelineEntry[];
};


function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function toRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function parsePromptPayload(entry: LogEntry): PromptPayload {
  const payload = toRecord(entry.payload) ?? {};
  const previewValue = payload["preview"];
  const preview = typeof previewValue === "string" ? previewValue : "";
  const agentValue = payload["agent"];
  const agent =
    typeof agentValue === "string" && agentValue.trim().length > 0 ? agentValue : entry.logger;
  const roleValue = payload["role"];
  const role = typeof roleValue === "string" ? roleValue : undefined;
  const stageValue = payload["stage"];
  const stage = typeof stageValue === "string" ? stageValue : undefined;
  const charValue = payload["chars"];
  const chars = typeof charValue === "number" ? charValue : undefined;
  const modelValue = payload["model"];
  const model = typeof modelValue === "string" ? modelValue : undefined;
  const truncated = Boolean(payload["truncated"]);
  const metadata = payload["metadata"];
  return {
    agent,
    role,
    stage,
    preview,
    truncated,
    chars,
    model,
    metadata,
  };
}

export function PromptLogPanel(): JSX.Element {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState<string>("all");
  const cursorRef = useRef<number | null>(null);

  const loadPrompts = useCallback(
    async (mode: "refresh" | "append" = "refresh") => {
      const shouldAppend = mode === "append" && cursorRef.current !== null;
      if (mode === "refresh") {
        setIsLoading(true);
      }
      try {
        const response = await fetchLogs({
          after: shouldAppend ? cursorRef.current ?? undefined : undefined,
          limit: MAX_PROMPT_ENTRIES,
          type: "prompts",
        });
        cursorRef.current = response.cursor;
        setError(null);
        setEntries((previous) => {
          if (!shouldAppend) {
            return response.logs.slice(-MAX_PROMPT_ENTRIES);
          }
          if (response.logs.length === 0) {
            return previous;
          }
          const existing = new Set(previous.map((entry) => entry.sequence));
          const merged = [...previous];
          for (const entry of response.logs) {
            if (!existing.has(entry.sequence)) {
              merged.push(entry);
            }
          }
          return merged.slice(-MAX_PROMPT_ENTRIES);
        });
      } catch (caught) {
        setError((caught as Error).message);
      } finally {
        if (mode === "refresh") {
          setIsLoading(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    void loadPrompts("refresh");
  }, [loadPrompts]);

  useEffect(() => {
    if (!autoRefresh) {
      return undefined;
    }
    const handle = window.setInterval(() => {
      void loadPrompts("append");
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, [autoRefresh, loadPrompts]);

  const handleDownload = useCallback(async () => {
    setIsDownloading(true);
    try {
      const blob = await downloadPromptAudit();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `codingconductor-prompt-audit-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setIsDownloading(false);
    }
  }, []);

  const interactions = useMemo(() => {
    if (entries.length === 0) {
      return [] as PromptInteraction[];
    }
    const sorted = [...entries].sort((a, b) => a.sequence - b.sequence);
    const result: PromptInteraction[] = [];
    let current: PromptInteraction | null = null;
    let currentHasResponse = false;

    const flushCurrent = () => {
      if (!current || current.entries.length === 0) {
        current = null;
        currentHasResponse = false;
        return;
      }
      const lastEntry = current.entries[current.entries.length - 1];
      current.latestAt = lastEntry.log.timestamp;
      result.push(current);
      current = null;
      currentHasResponse = false;
    };

    for (const entry of sorted) {
      const payload = parsePromptPayload(entry);
      const agent = payload.agent || entry.logger;
      const runId = entry.run_id ?? null;
      const stage = (payload.stage ?? "").toLowerCase();
      const timelineEntry: PromptTimelineEntry = {
        log: entry,
        payload,
      };
      const shouldStartNew =
        current === null ||
        current.agent !== agent ||
        current.runId !== runId ||
        (currentHasResponse && stage === "request");

      if (shouldStartNew) {
        flushCurrent();
        current = {
          id: `${entry.sequence}`,
          agent,
          runId,
          startedAt: entry.timestamp,
          latestAt: entry.timestamp,
          entries: [],
        };
        currentHasResponse = false;
      }

      const activeInteraction = current;
      if (!activeInteraction) {
        continue;
      }

      activeInteraction.entries.push(timelineEntry);
      activeInteraction.latestAt = entry.timestamp;

      if (stage === "response") {
        currentHasResponse = true;
        flushCurrent();
      }
    }

    flushCurrent();

    return result;
  }, [entries]);

  const agentOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const interaction of interactions) {
      const trimmed = interaction.agent.trim();
      if (trimmed) {
        seen.add(trimmed);
      }
    }
    return Array.from(seen).sort((a, b) => a.localeCompare(b));
  }, [interactions]);

  useEffect(() => {
    if (agentFilter !== "all" && !agentOptions.includes(agentFilter)) {
      setAgentFilter("all");
    }
  }, [agentFilter, agentOptions]);

  const filteredInteractions = useMemo(() => {
    const subset = interactions.filter((interaction) => {
      if (agentFilter !== "all" && interaction.agent !== agentFilter) {
        return false;
      }
      return true;
    });
    const sorted = [...subset];
    sorted.sort((a, b) => {
      const aSeq = a.entries.length > 0 ? a.entries[a.entries.length - 1].log.sequence : 0;
      const bSeq = b.entries.length > 0 ? b.entries[b.entries.length - 1].log.sequence : 0;
      return bSeq - aSeq;
    });
    return sorted;
  }, [interactions, agentFilter]);

  const visibleEntryCount = useMemo(
    () => filteredInteractions.reduce((total, interaction) => total + interaction.entries.length, 0),
    [filteredInteractions],
  );

  return (
    <section className="space-y-4">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Prompts</h2>
          <p className="text-sm text-slate-400">
            Truncated previews of prompt traffic exchanged with the OpenAI API. Use the download action for full text.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2 text-slate-300">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
              className="h-4 w-4 rounded border-slate-600 bg-slate-900"
            />
            Auto refresh
          </label>
          <select
            value={agentFilter}
            onChange={(event) => setAgentFilter(event.target.value)}
            className="rounded border border-slate-700 bg-slate-900 px-3 py-1 text-xs text-slate-200 transition hover:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/40"
          >
            <option value="all">All agents</option>
            {agentOptions.map((agent) => (
              <option key={agent} value={agent}>
                {agent}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void handleDownload()}
            disabled={isDownloading}
            className="rounded border border-slate-700 bg-slate-900 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-emerald-500 hover:text-emerald-300 disabled:opacity-60"
          >
            {isDownloading ? "Preparing..." : "Download full log"}
          </button>
          <button
            type="button"
            onClick={() => void loadPrompts("refresh")}
            disabled={isLoading}
            className="rounded bg-slate-800 px-3 py-1 text-xs font-medium text-slate-200 transition hover:bg-slate-700 disabled:opacity-60"
          >
            {isLoading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>
      {error && (
        <div className="rounded border border-rose-500/40 bg-rose-900/30 px-3 py-2 text-sm text-rose-200">{error}</div>
      )}
      <p className="text-sm text-slate-400">
        Showing {filteredInteractions.length} of {interactions.length} prompt exchanges ({visibleEntryCount} log events retained).
      </p>
      <div className="space-y-3">
        {filteredInteractions.map((interaction) => (
          <article
            key={interaction.id}
            className="rounded border border-slate-800 bg-slate-900/70 p-4 shadow-sm shadow-slate-950/60"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400">
              <div>
                <p className="text-sm font-semibold text-slate-100">{interaction.agent}</p>
                <p className="text-xs text-slate-500">Run: {interaction.runId ?? "-"}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-slate-800 px-2 py-0.5 text-[0.65rem] uppercase tracking-wide text-slate-200">
                  Prompt
                </span>
                <span>{interaction.entries.length} events</span>
                <span>{formatTimestamp(interaction.latestAt)}</span>
              </div>
            </div>
            <div className="mt-3 space-y-3">
              {interaction.entries.map((item) => {
                const metadataRecord = toRecord(item.payload.metadata);
                let metadataText: string | null = null;
                if (metadataRecord) {
                  try {
                    metadataText = JSON.stringify(metadataRecord, null, 2);
                  } catch (caught) {
                    metadataText = `Unable to render metadata: ${(caught as Error).message}`;
                  }
                } else if (item.payload.metadata !== undefined && item.payload.metadata !== null) {
                  metadataText = String(item.payload.metadata);
                }
                const stageLabel = (item.payload.stage ?? "").toUpperCase();
                const roleLabel = (item.payload.role ?? "").toUpperCase();
                return (
                  <div
                    key={item.log.sequence}
                    className="rounded border border-slate-800 bg-slate-950/40 p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                      <div className="flex flex-wrap items-center gap-2">
                        {stageLabel && <span className="font-semibold text-emerald-300">{stageLabel}</span>}
                        {roleLabel && <span className="text-slate-300">{roleLabel}</span>}
                        {item.payload.model && <span className="text-slate-500">{item.payload.model}</span>}
                        {item.log.event && <span className="text-slate-500">{item.log.event}</span>}
                      </div>
                      <span>{formatTimestamp(item.log.timestamp)}</span>
                    </div>
                    <div className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-100">
                      {item.payload.preview || "(no content captured)"}
                    </div>
                    {item.payload.truncated && (
                      <p className="mt-1 text-xs text-amber-300">
                        Preview truncated. Download the audit log to read the full text.
                      </p>
                    )}
                    <dl className="mt-3 grid gap-x-6 gap-y-2 text-xs text-slate-400 sm:grid-cols-2">
                      <div>
                        <dt className="font-medium text-slate-300">Characters</dt>
                        <dd>{item.payload.chars ?? item.payload.preview.length}</dd>
                      </div>
                      <div>
                        <dt className="font-medium text-slate-300">Sequence</dt>
                        <dd>#{item.log.sequence}</dd>
                      </div>
                    </dl>
                    {metadataText && (
                      <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-950/60 p-2 text-xs text-slate-200">
                        {metadataText}
                      </pre>
                    )}
                  </div>
                );
              })}
            </div>
          </article>
        ))}
        {filteredInteractions.length === 0 && !isLoading && (
          <div className="rounded border border-slate-800 bg-slate-900/60 p-6 text-center text-sm text-slate-400">
            No prompt activity captured yet. Run the planner to gather prompt telemetry.
          </div>
        )}
      </div>
    </section>
  );
}
