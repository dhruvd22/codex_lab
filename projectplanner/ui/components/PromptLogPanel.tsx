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

  const renderedEntries = useMemo(() => entries.slice().reverse(), [entries]);

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
        Showing {renderedEntries.length} of {MAX_PROMPT_ENTRIES} entries.
      </p>
      <div className="space-y-3">
        {renderedEntries.map((entry) => {
          const payload = parsePromptPayload(entry);
          const metadataRecord = toRecord(payload.metadata);
          let metadataText: string | null = null;
          if (metadataRecord) {
            try {
              metadataText = JSON.stringify(metadataRecord, null, 2);
            } catch (caught) {
              metadataText = `Unable to render metadata: ${(caught as Error).message}`;
            }
          } else if (payload.metadata !== undefined && payload.metadata !== null) {
            metadataText = String(payload.metadata);
          }
          return (
            <article
              key={entry.sequence}
              className="rounded border border-slate-800 bg-slate-900/70 p-4 shadow-sm shadow-slate-950/60"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                <span>{formatTimestamp(entry.timestamp)}</span>
                <div className="flex items-center gap-2 text-emerald-300">
                  {payload.stage && <span className="uppercase">{payload.stage}</span>}
                  {payload.role && <span className="uppercase text-slate-300">{payload.role}</span>}
                  {entry.event && <span className="text-slate-500">{entry.event}</span>}
                </div>
              </div>
              <div className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-100">
                {payload.preview || "(no content captured)"}
              </div>
              {payload.truncated && (
                <p className="mt-1 text-xs text-amber-300">Preview truncated. Download the audit log to read the full text.</p>
              )}
              <dl className="mt-3 grid gap-x-6 gap-y-2 text-xs text-slate-400 sm:grid-cols-2">
                <div>
                  <dt className="font-medium text-slate-300">Agent</dt>
                  <dd>{payload.agent}</dd>
                </div>
                <div>
                  <dt className="font-medium text-slate-300">Run</dt>
                  <dd>{entry.run_id ?? "-"}</dd>
                </div>
                <div>
                  <dt className="font-medium text-slate-300">Model</dt>
                  <dd>{payload.model ?? "-"}</dd>
                </div>
                <div>
                  <dt className="font-medium text-slate-300">Characters</dt>
                  <dd>{payload.chars ?? payload.preview.length}</dd>
                </div>
              </dl>
              {metadataText && (
                <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-950/60 p-2 text-xs text-slate-200">
                  {metadataText}
                </pre>
              )}
            </article>
          );
        })}
        {renderedEntries.length === 0 && !isLoading && (
          <div className="rounded border border-slate-800 bg-slate-900/60 p-6 text-center text-sm text-slate-400">
            No prompt activity captured yet. Run the planner to gather prompt telemetry.
          </div>
        )}
      </div>
    </section>
  );
}
