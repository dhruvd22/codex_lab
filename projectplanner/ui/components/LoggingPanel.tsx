import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchLogs, LogEntry, LogLevelFilter } from "@/lib/api";

const LEVEL_OPTIONS: Array<LogLevelFilter | "ALL"> = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const AUTO_REFRESH_INTERVAL_MS = 5000;
const MAX_ENTRIES = 500;

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

const levelStyles: Record<string, string> = {
  DEBUG: "text-slate-300",
  INFO: "text-emerald-300",
  WARNING: "text-amber-300",
  ERROR: "text-rose-300",
  CRITICAL: "text-rose-400 font-semibold",
};

function serializeDetails(entry: LogEntry): string | null {
  const blocks: string[] = [];
  if (entry.payload && Object.keys(entry.payload).length > 0) {
    try {
      blocks.push(JSON.stringify(entry.payload, null, 2));
    } catch (error) {
      blocks.push(`Unable to display payload: ${(error as Error).message}`);
    }
  }
  if (entry.exception) {
    blocks.push(entry.exception);
  }
  return blocks.length ? blocks.join("

") : null;
}

export function LoggingPanel(): JSX.Element {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const cursorRef = useRef<number | null>(null);
  const [level, setLevel] = useState<LogLevelFilter | "ALL">("INFO");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLogs = useCallback(
    async (mode: "refresh" | "append" = "refresh") => {
      const effectiveLevel = level === "ALL" ? undefined : level;
      const shouldAppend = mode === "append" && cursorRef.current !== null;
      if (mode === "refresh") {
        setIsLoading(true);
      }
      try {
        const response = await fetchLogs({
          after: shouldAppend ? cursorRef.current ?? undefined : undefined,
          level: effectiveLevel,
        });
        cursorRef.current = response.cursor;
        setCursor(response.cursor);
        setError(null);
        setLogs((previous) => {
          if (!shouldAppend) {
            return response.logs.slice(-MAX_ENTRIES);
          }
          const existing = new Set(previous.map((entry) => entry.sequence));
          const appended = response.logs.filter((entry) => !existing.has(entry.sequence));
          if (appended.length === 0) {
            return previous;
          }
          return [...previous, ...appended].slice(-MAX_ENTRIES);
        });
      } catch (err) {
        setError((err as Error).message);
      } finally {
        if (mode === "refresh") {
          setIsLoading(false);
        }
      }
    },
    [level],
  );

  useEffect(() => {
    cursorRef.current = null;
    void loadLogs("refresh");
  }, [loadLogs]);

  useEffect(() => {
    if (!autoRefresh) {
      return () => undefined;
    }
    const interval = setInterval(() => {
      if (cursorRef.current === null) {
        void loadLogs("refresh");
        return;
      }
      void loadLogs("append");
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [autoRefresh, loadLogs]);

  const renderedRows = useMemo(() => logs.slice().reverse(), [logs]);

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm">
          <label htmlFor="log-level" className="text-slate-300">
            Level
          </label>
          <select
            id="log-level"
            value={level}
            onChange={(event) => setLevel(event.target.value as LogLevelFilter | "ALL")}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
          >
            {LEVEL_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "ALL" ? "All" : option}
              </option>
            ))}
          </select>
          <span className="text-slate-500">{renderedRows.length} records</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
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
            onClick={() => loadLogs("refresh")}
            disabled={isLoading}
            className="rounded bg-slate-800 px-3 py-1 text-xs font-medium text-slate-200 transition hover:bg-slate-700 disabled:opacity-60"
          >
            {isLoading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>
      {error && <div className="rounded border border-rose-500/40 bg-rose-900/20 px-3 py-2 text-sm text-rose-200">{error}</div>}
      <div className="overflow-x-auto rounded border border-slate-800 bg-slate-900">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-900/80 text-xs uppercase text-slate-400">
            <tr>
              <th className="px-3 py-2">Time</th>
              <th className="px-3 py-2">Level</th>
              <th className="px-3 py-2">Event</th>
              <th className="px-3 py-2">Run</th>
              <th className="px-3 py-2">Message</th>
              <th className="px-3 py-2">Details</th>
            </tr>
          </thead>
          <tbody>
            {renderedRows.map((entry) => {
              const details = serializeDetails(entry);
              return (
                <tr key={entry.sequence} className="border-t border-slate-800">
                  <td className="px-3 py-2 align-top text-slate-300">{formatTimestamp(entry.timestamp)}</td>
                  <td className={`px-3 py-2 align-top font-semibold ${levelStyles[entry.level] ?? "text-slate-200"}`}>
                    {entry.level}
                  </td>
                  <td className="px-3 py-2 align-top text-slate-300">{entry.event ?? "—"}</td>
                  <td className="px-3 py-2 align-top text-slate-300">{entry.run_id ?? "—"}</td>
                  <td className="px-3 py-2 align-top text-slate-100">{entry.message}</td>
                  <td className="px-3 py-2 align-top text-slate-300">
                    {details ? (
                      <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded bg-slate-950/60 p-2 text-xs text-slate-200">
                        {details}
                      </pre>
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {renderedRows.length === 0 && !isLoading && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-slate-400">
                  No log entries yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
