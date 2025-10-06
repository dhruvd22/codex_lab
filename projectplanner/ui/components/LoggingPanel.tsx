import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { downloadLogs, fetchLogs, LogEntry, LogLevelFilter, type LogType } from "@/lib/api";

type TimeRangePreset = "all" | "15m" | "1h" | "24h" | "7d";

const LEVEL_OPTIONS: Array<LogLevelFilter | "ALL"> = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const SOURCE_OPTIONS: Array<{ value: LogType; label: string }> = [
  { value: "runtime", label: "Runtime" },
  { value: "prompts", label: "Prompts" },
];
const TIME_RANGE_OPTIONS: Array<{ value: TimeRangePreset; label: string }> = [
  { value: "all", label: "All time" },
  { value: "15m", label: "Last 15 min" },
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
];
const AUTO_REFRESH_INTERVAL_MS = 5000;
const DEFAULT_PAGE_SIZE = 50;
const PAGE_SIZE_OPTIONS = [10, 25, 50];

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
  return blocks.length ? blocks.join("\n\n") : null;
}

function resolveTimeWindow(range: TimeRangePreset): { start?: string; end?: string } {
  if (range === "all") {
    return {};
  }
  const now = new Date();
  const end = now.toISOString();
  const offsets: Record<Exclude<TimeRangePreset, "all">, number> = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
  };
  const startMs = now.getTime() - offsets[range as Exclude<TimeRangePreset, "all">];
  return { start: new Date(startMs).toISOString(), end };
}

function filterLogsByWindow(entries: LogEntry[], windowStart: number | null, windowEnd: number | null): LogEntry[] {
  if (windowStart === null && windowEnd === null) {
    return entries;
  }
  return entries.filter((entry) => {
    const timestamp = new Date(entry.timestamp).getTime();
    if (Number.isNaN(timestamp)) {
      return true;
    }
    if (windowStart !== null && timestamp < windowStart) {
      return false;
    }
    if (windowEnd !== null && timestamp > windowEnd) {
      return false;
    }
    return true;
  });
}

export function LoggingPanel(): JSX.Element {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const cursorRef = useRef<number | null>(null);
  const [source, setSource] = useState<LogType>("runtime");
  const [level, setLevel] = useState<LogLevelFilter | "ALL">("INFO");
  const [timeRange, setTimeRange] = useState<TimeRangePreset>("all");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);

  const loadLogs = useCallback(
    async (mode: "refresh" | "append" = "refresh") => {
      const effectiveLevel = level === "ALL" ? undefined : level;
      const shouldAppend = mode === "append" && cursorRef.current !== null;
      const window = resolveTimeWindow(timeRange);
      const windowStart = window.start ? Date.parse(window.start) : null;
      const windowEnd = window.end ? Date.parse(window.end) : null;
      const limit = Math.max(pageSize, 1);
      if (mode === "refresh") {
        setIsLoading(true);
      }
      try {
        const response = await fetchLogs({
          after: shouldAppend ? cursorRef.current ?? undefined : undefined,
          level: effectiveLevel,
          type: source,
          start: window.start,
          end: window.end,
          limit,
        });
        cursorRef.current = response.cursor;
        setCursor(response.cursor);
        setError(null);
        setLogs((previous) => {
          if (!shouldAppend) {
            return filterLogsByWindow(response.logs, windowStart, windowEnd).slice(-limit);
          }
          const existing = new Set(previous.map((entry) => entry.sequence));
          const appended = response.logs.filter((entry) => !existing.has(entry.sequence));
          if (appended.length === 0) {
            return filterLogsByWindow(previous, windowStart, windowEnd).slice(-limit);
          }
          const combined = [...previous, ...appended];
          return filterLogsByWindow(combined, windowStart, windowEnd).slice(-limit);
        });
      } catch (err) {
        setError((err as Error).message);
      } finally {
        if (mode === "refresh") {
          setIsLoading(false);
        }
      }
    },
    [level, source, timeRange, pageSize],
  );

  useEffect(() => {
    cursorRef.current = null;
    setCursor(null);
    setLogs([]);
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

  const handleDownload = useCallback(async () => {
    const effectiveLevel = level === "ALL" ? undefined : level;
    const window = resolveTimeWindow(timeRange);
    setIsDownloading(true);
    try {
      const blob = await downloadLogs({
        level: effectiveLevel,
        type: source,
        start: window.start,
        end: window.end,
      });
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const parts = ["codingconductor", "logs", source];
      if (effectiveLevel) {
        parts.push(effectiveLevel.toLowerCase());
      }
      const filename = `${parts.join("-")}-${timestamp}.jsonl`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsDownloading(false);
    }
  }, [level, source, timeRange]);

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <div className="flex items-center gap-2">
            <label htmlFor="log-source" className="text-slate-300">
              Source
            </label>
            <select
              id="log-source"
              value={source}
              onChange={(event) => setSource(event.target.value as LogType)}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
            >
              {SOURCE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
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
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="log-window" className="text-slate-300">
              Window
            </label>
            <select
              id="log-window"
              value={timeRange}
              onChange={(event) => setTimeRange(event.target.value as TimeRangePreset)}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
            >
              {TIME_RANGE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="log-page-size" className="text-slate-300">
              Max rows
            </label>
            <select
              id="log-page-size"
              value={pageSize}
              onChange={(event) => setPageSize(Number(event.target.value))}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
            >
              {PAGE_SIZE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <span className="text-slate-500">
            Showing {renderedRows.length} of {pageSize} {source === "prompts" ? "prompt entries" : "records"}
          </span>
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
            onClick={() => void handleDownload()}
            disabled={isDownloading}
            className="rounded border border-slate-700 bg-slate-900 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-emerald-500 hover:text-emerald-300 disabled:opacity-60 disabled:hover:border-slate-700"
          >
            {isDownloading ? "Preparing…" : "Download"}
          </button>
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
