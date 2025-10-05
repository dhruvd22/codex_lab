import { useCallback, useEffect, useMemo, useState } from "react";

import {
  downloadObservabilitySnapshot,
  fetchObservabilitySnapshot,
  ObservabilityCall,
  ObservabilityNode,
  ObservabilitySnapshot,
  ObservabilityStatus,
} from "@/lib/api";

type ModuleGroup = {
  category: string;
  title: string;
  nodes: ObservabilityNode[];
  order: number;
};

type FlowItem = {
  id: string;
  sourceId: string;
  targetId: string;
  sourceName: string;
  targetName: string;
  label: string | null;
  isActive: boolean;
};

const CATEGORY_META: Record<string, { title: string; order: number }> = {
  endpoint: { title: "Endpoints", order: 0 },
  pipeline: { title: "Pipeline", order: 1 },
  storage: { title: "Storage", order: 2 },
  agent: { title: "Agents", order: 3 },
  service: { title: "Services", order: 4 },
};

const STATUS_STYLES: Record<ObservabilityStatus, string> = {
  healthy: "border border-emerald-400/40 bg-emerald-500/15 text-emerald-300",
  idle: "border border-slate-700 bg-slate-800 text-slate-200",
  degraded: "border border-amber-400/40 bg-amber-500/15 text-amber-300",
  error: "border border-rose-400/50 bg-rose-500/15 text-rose-300",
};

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "text-slate-400",
  INFO: "text-emerald-300",
  WARNING: "text-amber-300",
  ERROR: "text-rose-300",
  CRITICAL: "text-rose-400",
};

type TimeRangePreset = "all" | "15m" | "1h" | "24h" | "7d" | "custom";

const TIME_RANGE_OPTIONS: Array<{ value: TimeRangePreset; label: string }> = [
  { value: "all", label: "All time" },
  { value: "15m", label: "Last 15 min" },
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "custom", label: "Custom range" },
];

const DEFAULT_CALL_LIMIT = 50;
const CALL_LIMIT_OPTIONS = [10, 25, 50];
const REFRESH_INTERVAL_MS = 10000;

function resolveTimeWindow(
  range: TimeRangePreset,
  options: { sessionStart?: string | null; customWindow?: { start?: string; end?: string } } = {},
): { start?: string; end?: string } {
  if (range === "all") {
    const start = options.sessionStart ?? undefined;
    return start ? { start } : {};
  }
  if (range === "custom") {
    const start = options.customWindow?.start ?? options.sessionStart ?? undefined;
    const end = options.customWindow?.end ?? undefined;
    return { start, end };
  }
  const now = new Date();
  const end = now.toISOString();
  const offsets: Record<Exclude<TimeRangePreset, "all" | "custom">, number> = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
  };
  const startMs = now.getTime() - offsets[range as Exclude<TimeRangePreset, "all" | "custom">];
  return { start: new Date(startMs).toISOString(), end };
}

export function ObservabilityDashboard(): JSX.Element {
  const [snapshot, setSnapshot] = useState<ObservabilitySnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedModule, setSelectedModule] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRangePreset>("all");
  const [sessionStartedAt, setSessionStartedAt] = useState<string | null>(null);
  const [callLimit, setCallLimit] = useState<number>(DEFAULT_CALL_LIMIT);
  const [customWindow, setCustomWindow] = useState<{ start?: string; end?: string }>({});
  const [customStartInput, setCustomStartInput] = useState<string>("");
  const [customEndInput, setCustomEndInput] = useState<string>("");
  const [customError, setCustomError] = useState<string | null>(null);
  const hasCustomWindow = Boolean(customWindow.start || customWindow.end);

  const loadSnapshot = useCallback(async () => {
    setLoading(true);
    const window = resolveTimeWindow(timeRange, {
      sessionStart: sessionStartedAt ?? undefined,
      customWindow,
    });
    const nextLimit = Math.max(callLimit, 1);
    try {
      const data = await fetchObservabilitySnapshot({
        calls: nextLimit,
        start: window.start,
        end: window.end,
      });
      setSnapshot(data);
      setSessionStartedAt((current) => (current === data.session_started_at ? current : data.session_started_at));
      setError(null);
      setSelectedModule((current) => {
        if (current && data.nodes.some((node) => node.id === current)) {
          return current;
        }
        return data.nodes[0]?.id ?? null;
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [timeRange, sessionStartedAt, customWindow, callLimit]);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    if (timeRange !== "custom" || !sessionStartedAt || hasCustomWindow) {
      return;
    }
    const startIso = sessionStartedAt;
    const endIso = new Date().toISOString();
    setCustomStartInput((current) => current || toLocalInputValue(startIso));
    setCustomEndInput((current) => current || toLocalInputValue(endIso));
    setCustomWindow({ start: startIso, end: endIso });
  }, [timeRange, sessionStartedAt, hasCustomWindow]);

  useEffect(() => {
    if (!autoRefresh) {
      return undefined;
    }
    const interval = window.setInterval(() => {
      void loadSnapshot();
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [autoRefresh, loadSnapshot]);

  const modulesById = useMemo<Record<string, ObservabilityNode>>(() => {
    if (!snapshot) {
      return {};
    }
    const map: Record<string, ObservabilityNode> = {};
    for (const node of snapshot.nodes) {
      map[node.id] = node;
    }
    return map;
  }, [snapshot]);

  const groups = useMemo<ModuleGroup[]>(() => {
    if (!snapshot) {
      return [];
    }
    const bucket = new Map<string, ModuleGroup>();
    for (const node of snapshot.nodes) {
      const meta = CATEGORY_META[node.category] ?? { title: node.category, order: 90 };
      if (!bucket.has(node.category)) {
        bucket.set(node.category, { category: node.category, title: meta.title, nodes: [], order: meta.order });
      }
      bucket.get(node.category)!.nodes.push(node);
    }
    return Array.from(bucket.values())
      .map((group) => ({
        ...group,
        nodes: group.nodes.slice().sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => a.order - b.order);
  }, [snapshot]);

  const selectedNode = selectedModule ? modulesById[selectedModule] : null;

  const flowItems = useMemo<FlowItem[]>(() => {
    if (!snapshot) {
      return [];
    }
    return snapshot.edges.map((edge) => ({
      id: `${edge.source}->${edge.target}`,
      sourceId: edge.source,
      targetId: edge.target,
      sourceName: modulesById[edge.source]?.name ?? edge.source,
      targetName: modulesById[edge.target]?.name ?? edge.target,
      label: edge.label ?? null,
      isActive: !!selectedModule && (edge.source === selectedModule || edge.target === selectedModule),
    }));
  }, [modulesById, selectedModule, snapshot]);

  const calls = useMemo(() => {
    if (!snapshot || !selectedModule) {
      return [];
    }
    const limit = Math.max(callLimit, 1);
    return snapshot.calls.filter((call) => call.module_id === selectedModule).slice(0, limit);
  }, [snapshot, selectedModule, callLimit]);

  const errorMessage = useMemo(() => normalizeErrorMessage(error), [error]);

  const lastGenerated = snapshot ? formatDateTime(snapshot.generated_at) : null;

  const handleDownload = useCallback(async () => {
    const window = resolveTimeWindow(timeRange, {
      sessionStart: sessionStartedAt ?? undefined,
      customWindow,
    });
    const nextLimit = Math.max(callLimit, 1);
    setIsDownloading(true);
    try {
      const blob = await downloadObservabilitySnapshot({
        calls: nextLimit,
        start: window.start,
        end: window.end,
      });
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const filename = `projectplanner-observability-${timestamp}.json`;
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
  }, [timeRange, sessionStartedAt, customWindow, callLimit]);

  const applyCustomRange = useCallback(() => {
    const startIso = fromLocalInputValue(customStartInput);
    const endIso = fromLocalInputValue(customEndInput);
    if (startIso && endIso && new Date(startIso) > new Date(endIso)) {
      setCustomError("Start must be before end.");
      return;
    }
    setCustomError(null);
    const nextWindow: { start?: string; end?: string } = {};
    if (startIso) {
      nextWindow.start = startIso;
    }
    if (endIso) {
      nextWindow.end = endIso;
    }
    setCustomWindow(nextWindow);
  }, [customStartInput, customEndInput]);

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-slate-100">Observability</h2>
          <p className="text-sm text-slate-400">Track workflow components, latency, and recent module activity.</p>
          {sessionStartedAt && (
            <p className="text-xs text-slate-500">
              Session started <span className="font-mono text-slate-300">{formatDateTime(sessionStartedAt)}</span>
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-slate-400">
          <div className="flex items-center gap-2">
            <label htmlFor="observability-window">Window</label>
            <select
              id="observability-window"
              value={timeRange}
              onChange={(event) => {
                const value = event.target.value as TimeRangePreset;
                setTimeRange(value);
                setCustomError(null);
                if (value === "custom") {
                  setAutoRefresh(false);
                }
              }}
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
            <label htmlFor="observability-call-limit">Max calls</label>
            <select
              id="observability-call-limit"
              value={callLimit}
              onChange={(event) => setCallLimit(Number(event.target.value))}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
            >
              {CALL_LIMIT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-slate-600 bg-slate-900"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            Auto refresh
          </label>
          {lastGenerated && (
            <span>
              Last updated
              <span className="ml-1 font-mono text-slate-300">{lastGenerated}</span>
            </span>
          )}
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
            onClick={() => void loadSnapshot()}
            disabled={loading}
            className="rounded border border-slate-700 bg-slate-900 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-emerald-500 hover:text-emerald-300 disabled:opacity-60 disabled:hover:border-slate-700"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
          {timeRange === "custom" && (
            <div className="flex w-full flex-wrap items-center justify-end gap-2">
              <label htmlFor="observability-custom-start">From</label>
              <input
                id="observability-custom-start"
                type="datetime-local"
                value={customStartInput}
                onChange={(event) => {
                  setCustomStartInput(event.target.value);
                  setCustomError(null);
                }}
                className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
              />
              <label htmlFor="observability-custom-end">To</label>
              <input
                id="observability-custom-end"
                type="datetime-local"
                value={customEndInput}
                onChange={(event) => {
                  setCustomEndInput(event.target.value);
                  setCustomError(null);
                }}
                className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
              />
              <button
                type="button"
                onClick={applyCustomRange}
                className="rounded border border-slate-700 bg-slate-900 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-emerald-500 hover:text-emerald-300"
              >
                Apply
              </button>
            </div>
          )}
          {timeRange === "custom" && customError && (
            <span className="w-full text-right text-rose-300">{customError}</span>
          )}
        </div>
      </header>
      {errorMessage && (
        <div className="space-y-2 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          <p className="whitespace-pre-wrap break-words">{errorMessage.summary}</p>
          {errorMessage.details && (
            <details className="text-xs">
              <summary className="cursor-pointer text-rose-300 hover:text-rose-100">View server response</summary>
              <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded border border-rose-500/20 bg-rose-500/5 p-2 text-[11px] leading-relaxed text-rose-100">{errorMessage.details}</pre>
            </details>
          )}
        </div>
      )}
      {!snapshot && loading && (
        <div className="rounded border border-slate-800 bg-slate-900 px-4 py-8 text-center text-sm text-slate-300">
          Loading observability signals...
        </div>
      )}
      {snapshot && snapshot.nodes.length === 0 && !loading && (
        <div className="rounded border border-slate-800 bg-slate-900 px-4 py-6 text-sm text-slate-300">
          Run an ingestion and planning cycle to populate the observability dashboard.
        </div>
      )}
      {snapshot && snapshot.nodes.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[2fr,1fr]">
          <div className="space-y-4">
            <ModuleMap
              groups={groups}
              selectedModule={selectedModule}
              onSelect={setSelectedModule}
            />
            <FlowList flows={flowItems} />
          </div>
          <div className="space-y-4">
            <ModuleDetails node={selectedNode} />
            <CallLog calls={calls} loading={loading} limit={callLimit} />
          </div>
        </div>
      )}
    </section>
  );
}

function ModuleMap({
  groups,
  selectedModule,
  onSelect,
}: {
  groups: ModuleGroup[];
  selectedModule: string | null;
  onSelect: (moduleId: string) => void;
}): JSX.Element {
  return (
    <section className="space-y-4 rounded border border-slate-800 bg-slate-900 p-4">
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-300">Workflow Map</h3>
        <span className="text-xs text-slate-500">Click a module to drill into calls</span>
      </header>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {groups.map((group) => (
          <div key={group.category} className="space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{group.title}</h4>
            <div className="space-y-3">
              {group.nodes.map((node) => (
                <ModuleCard
                  key={node.id}
                  node={node}
                  isActive={node.id === selectedModule}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ModuleCard({
  node,
  isActive,
  onSelect,
}: {
  node: ObservabilityNode;
  isActive: boolean;
  onSelect: (moduleId: string) => void;
}): JSX.Element {
  const statusStyle = STATUS_STYLES[node.status] ?? STATUS_STYLES.healthy;
  const totalRuns = typeof node.metrics.total_runs === "number" ? node.metrics.total_runs : node.run_ids.length;
  const avgLatency = formatLatency(node.metrics.avg_latency_ms);
  const p95Latency = formatLatency(node.metrics.p95_latency_ms);
  const lastLatency = formatLatency(node.metrics.last_latency_ms);
  const warningCount = typeof node.metrics.warning_count === "number" ? node.metrics.warning_count : 0;
  const errorCount = typeof node.metrics.error_count === "number" ? node.metrics.error_count : 0;
  const lastEventTime = formatDateTime(node.last_timestamp);
  const lastMessage = typeof node.metrics.last_message === "string" ? node.metrics.last_message : null;

  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      aria-pressed={isActive}
      className={`w-full rounded border px-3 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400 ${
        isActive ? "border-emerald-400/70 bg-slate-900/80 shadow-lg shadow-emerald-900/30" : "border-slate-800 bg-slate-950/60 hover:border-emerald-500/60 hover:bg-slate-900/80"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div>
          <h5 className="text-sm font-semibold text-slate-100">{node.name}</h5>
          <p className="text-xs text-slate-400">{node.description}</p>
        </div>
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${statusStyle}`}>{node.status}</span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs text-slate-300">
        <div>
          <dt className="text-slate-500">Events</dt>
          <dd className="font-mono text-slate-200">{node.event_count}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Runs</dt>
          <dd className="font-mono text-slate-200">{totalRuns}</dd>
        </div>
        {avgLatency && (
          <div>
            <dt className="text-slate-500">Avg latency</dt>
            <dd className="font-mono text-slate-200">{avgLatency}</dd>
          </div>
        )}
        {p95Latency && (
          <div>
            <dt className="text-slate-500">p95 latency</dt>
            <dd className="font-mono text-slate-200">{p95Latency}</dd>
          </div>
        )}
        {lastLatency && (
          <div>
            <dt className="text-slate-500">Last latency</dt>
            <dd className="font-mono text-slate-200">{lastLatency}</dd>
          </div>
        )}
        {warningCount > 0 && (
          <div>
            <dt className="text-slate-500">Warnings</dt>
            <dd className="font-mono text-amber-300">{warningCount}</dd>
          </div>
        )}
        {errorCount > 0 && (
          <div>
            <dt className="text-slate-500">Errors</dt>
            <dd className="font-mono text-rose-300">{errorCount}</dd>
          </div>
        )}
      </dl>
      <div className="mt-3 space-y-1 text-xs">
        <p className="text-slate-500">
          Last event: <span className="text-slate-300">{node.last_event ?? "--"}</span>
        </p>
        <p className="text-slate-500">
          At: <span className="text-slate-300">{lastEventTime}</span>
        </p>
        {lastMessage && <p className="text-slate-500">Msg: <span className="text-slate-300">{lastMessage}</span></p>}
      </div>
    </button>
  );
}

function FlowList({ flows }: { flows: FlowItem[] }): JSX.Element {
  if (flows.length === 0) {
    return (
      <section className="rounded border border-slate-800 bg-slate-900 p-4 text-sm text-slate-300">
        No edges discovered yet.
      </section>
    );
  }
  return (
    <section className="space-y-3 rounded border border-slate-800 bg-slate-900 p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-300">Flow Relationships</h3>
      <ul className="space-y-2 text-sm text-slate-200">
        {flows.map((flow) => (
          <li
            key={flow.id}
            className={`flex flex-wrap items-center gap-2 rounded border px-3 py-2 text-xs ${
              flow.isActive
                ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-100"
                : "border-slate-800 bg-slate-950/60 text-slate-200"
            }`}
          >
            <span className="font-semibold">{flow.sourceName}</span>
            <span className="text-slate-500">{'->'}</span>
            <span className="font-semibold">{flow.targetName}</span>
            {flow.label && <span className="text-slate-400">({flow.label})</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ModuleDetails({ node }: { node: ObservabilityNode | null }): JSX.Element {
  if (!node) {
    return (
      <section className="rounded border border-slate-800 bg-slate-900 p-4 text-sm text-slate-300">
        Select a module to inspect its latest telemetry.
      </section>
    );
  }
  const statusStyle = STATUS_STYLES[node.status] ?? STATUS_STYLES.healthy;
  const runIds = node.run_ids.slice(0, 6);
  const remainingRuns = node.run_ids.length - runIds.length;
  const lastEventTime = formatDateTime(node.last_timestamp);
  const avgLatency = formatLatency(node.metrics.avg_latency_ms);
  const p95Latency = formatLatency(node.metrics.p95_latency_ms);
  const lastLatency = formatLatency(node.metrics.last_latency_ms);
  const lastMessage = typeof node.metrics.last_message === "string" ? node.metrics.last_message : null;

  return (
    <section className="space-y-3 rounded border border-slate-800 bg-slate-900 p-4">
      <header className="space-y-1">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100">{node.name}</h3>
          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${statusStyle}`}>{node.status}</span>
        </div>
        <p className="text-xs text-slate-400">{node.description}</p>
      </header>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs text-slate-300">
        <div>
          <dt className="text-slate-500">Events</dt>
          <dd className="font-mono text-slate-200">{node.event_count}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Runs tracked</dt>
          <dd className="font-mono text-slate-200">{node.run_ids.length}</dd>
        </div>
        {avgLatency && (
          <div>
            <dt className="text-slate-500">Avg latency</dt>
            <dd className="font-mono text-slate-200">{avgLatency}</dd>
          </div>
        )}
        {p95Latency && (
          <div>
            <dt className="text-slate-500">p95 latency</dt>
            <dd className="font-mono text-slate-200">{p95Latency}</dd>
          </div>
        )}
        {lastLatency && (
          <div>
            <dt className="text-slate-500">Last latency</dt>
            <dd className="font-mono text-slate-200">{lastLatency}</dd>
          </div>
        )}
      </dl>
      <div className="space-y-1 text-xs text-slate-400">
        <p>Last event: <span className="text-slate-200">{node.last_event ?? "--"}</span></p>
        <p>At: <span className="text-slate-200">{lastEventTime}</span></p>
        {lastMessage && <p>Message: <span className="text-slate-200">{lastMessage}</span></p>}
      </div>
      <div className="space-y-2 text-xs text-slate-400">
        <span className="block text-slate-500">Recent runs</span>
        <div className="flex flex-wrap gap-1">
          {runIds.map((runId) => (
            <span key={runId} className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-slate-200">
              {runId}
            </span>
          ))}
          {remainingRuns > 0 && (
            <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">+{remainingRuns} more</span>
          )}
          {runIds.length === 0 && <span className="text-slate-500">No runs observed yet.</span>}
        </div>
      </div>
    </section>
  );
}

function normalizeErrorMessage(value: string | null): { summary: string; details?: string } | null {
  if (!value) {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return { summary: "An unknown error occurred." };
  }
  const lower = trimmed.toLowerCase();
  const looksLikeHtml = lower.startsWith("<!doctype html") || lower.startsWith("<html") || lower.includes("<body");
  if (looksLikeHtml) {
    return {
      summary: "Received an HTML error response from the observability service.",
      details: trimmed,
    };
  }
  if (trimmed.length > 300) {
    return {
      summary: `${trimmed.slice(0, 280).trimEnd()}...`,
      details: trimmed,
    };
  }
  return { summary: trimmed };
}

function determineBlockMessage(message: string): boolean {
  if (!message) {
    return false;
  }
  if (message.length > 160 || message.includes("\n")) {
    return true;
  }
  if (/<[a-z!]/i.test(message)) {
    return true;
  }
  return message.split(/\s+/).some((chunk) => chunk.length > 80);
}

function CallLog({ calls, loading, limit }: { calls: ObservabilityCall[]; loading: boolean; limit: number }): JSX.Element {
  return (
    <section className="space-y-3 rounded border border-slate-800 bg-slate-900 p-4">
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-300">Recent Calls</h3>
        <span className="text-xs text-slate-500">Showing {calls.length} of {limit}</span>
      </header>
      {calls.length === 0 && !loading && (
        <p className="text-sm text-slate-400">No calls recorded for the selected module yet.</p>
      )}
      {calls.length === 0 && loading && (
        <p className="text-sm text-slate-400">Awaiting new telemetry...</p>
      )}
      <div className="space-y-3">
        {calls.map((call) => (
          <CallEntry key={`${call.module_id}-${call.timestamp}-${call.event ?? ""}`} call={call} />
        ))}
      </div>
    </section>
  );
}

function CallEntry({ call }: { call: ObservabilityCall }): JSX.Element {
  const timestamp = formatDateTime(call.timestamp);
  const level = (call.level || "INFO").toUpperCase();
  const levelColor = LEVEL_COLORS[level] ?? LEVEL_COLORS.INFO;
  const payload = formatPayload(call.payload);

  return (
    <div className="space-y-2 rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-slate-400">{timestamp}</span>
        <div className="flex items-center gap-2">
          <span className={`font-semibold ${levelColor}`}>{level}</span>
          <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-200">{call.log_type}</span>
        </div>
      </div>
      {call.event && (
        <p className="text-slate-400">Event: <span className="text-slate-200">{call.event}</span></p>
      )}
      {call.run_id && (
        <p className="text-slate-400">Run: <span className="font-mono text-slate-200">{call.run_id}</span></p>
      )}
      {determineBlockMessage(call.message) ? (
        <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded border border-slate-800 bg-slate-950/40 p-2 text-slate-200">{call.message}</pre>
      ) : (
        <p className="text-slate-200 whitespace-pre-wrap break-words">{call.message}</p>
      )}
      {payload && (
        <details className="rounded border border-slate-800 bg-slate-950/60">
          <summary className="cursor-pointer px-2 py-1 text-slate-400 hover:text-slate-200">Payload</summary>
          <pre className="max-h-48 overflow-auto px-2 pb-2 text-[11px] leading-relaxed text-slate-200">{payload}</pre>
        </details>
      )}
    </div>
  );
}

function toLocalInputValue(iso?: string | null): string {
  if (!iso) {
    return "";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function fromLocalInputValue(value?: string): string | undefined {
  if (!value) {
    return undefined;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return undefined;
  }
  return date.toISOString();
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function formatLatency(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}s`;
  }
  if (value >= 1) {
    return `${Math.round(value)}ms`;
  }
  return `${value.toFixed(2)}ms`;
}

function formatPayload(payload: Record<string, unknown> | null | undefined): string | null {
  if (!payload) {
    return null;
  }
  try {
    return JSON.stringify(payload, null, 2);
  } catch (error) {
    return `Unable to format payload: ${(error as Error).message}`;
  }
}
