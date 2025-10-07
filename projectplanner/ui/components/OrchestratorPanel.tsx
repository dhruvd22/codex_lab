import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createOrchestratorRun,
  deleteOrchestratorRun,
  finalizeOrchestratorRun,
  generateOrchestratorMilestones,
  generateOrchestratorPrompts,
  getOrchestratorMilestones,
  getOrchestratorPrompts,
  getOrchestratorResult,
  getOrchestratorRun,
  getOrchestratorSummary,
  listOrchestratorRuns,
  OrchestratorMilestonesEnvelope,
  OrchestratorPromptsEnvelope,
  OrchestratorResult,
  OrchestratorSessionStatus,
  OrchestratorSummaryEnvelope,
  regenerateOrchestratorSummary,
  submitOrchestratorMilestonesDecision,
  submitOrchestratorSummaryDecision,
  HttpError,
} from "@/lib/api";

type Banner = { type: "info" | "error"; message: string } | null;

type FormatHint = "pdf" | "md" | "docx" | "txt" | undefined;

const ACCEPTED_BLUEPRINT_TYPES = [".pdf", ".md", ".docx", ".txt"];

export function OrchestratorPanel(): JSX.Element {
  const [file, setFile] = useState<File | null>(null);
  const [runs, setRuns] = useState<OrchestratorSessionStatus[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<OrchestratorSessionStatus | null>(null);
  const [summary, setSummary] = useState<OrchestratorSummaryEnvelope | null>(null);
  const [milestones, setMilestones] = useState<OrchestratorMilestonesEnvelope | null>(null);
  const [prompts, setPrompts] = useState<OrchestratorPromptsEnvelope | null>(null);
  const [result, setResult] = useState<OrchestratorResult | null>(null);
  const [banner, setBanner] = useState<Banner>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const loadRequestIdRef = useRef(0);

  const mergeRunSnapshot = useCallback((runId: string, patch: Partial<OrchestratorSessionStatus>) => {
    const cleaned = omitUndefined(patch);
    if (Object.keys(cleaned).length === 0) {
      return;
    }
    const now = new Date().toISOString();
    setRuns((previous) => {
      let found = false;
      const next = previous.map((run) => {
        if (run.run_id !== runId) {
          return run;
        }
        found = true;
        const merged: OrchestratorSessionStatus = {
          ...run,
          ...cleaned,
          created_at: typeof cleaned.created_at === "string" ? cleaned.created_at : run.created_at,
          updated_at: typeof cleaned.updated_at === "string" ? cleaned.updated_at : now,
          source:
            ("source" in cleaned ? (cleaned.source as string | null | undefined) : undefined) ?? run.source ?? null,
        };
        return merged;
      });
      if (!found) {
        next.push({
          run_id: runId,
          source: ("source" in cleaned ? (cleaned.source as string | null | undefined) : null) ?? null,
          summary_ready: cleaned.summary_ready ?? false,
          summary_approved: cleaned.summary_approved ?? false,
          milestones_ready: cleaned.milestones_ready ?? false,
          milestones_approved: cleaned.milestones_approved ?? false,
          prompts_ready: cleaned.prompts_ready ?? false,
          created_at: cleaned.created_at ?? now,
          updated_at: cleaned.updated_at ?? now,
        });
      }
      return next;
    });
    setStatus((current) => {
      if (!current || current.run_id !== runId) {
        return current;
      }
      return {
        ...current,
        ...cleaned,
        created_at: typeof cleaned.created_at === "string" ? cleaned.created_at : current.created_at,
        updated_at: typeof cleaned.updated_at === "string" ? cleaned.updated_at : current.updated_at,
        source:
          ("source" in cleaned ? (cleaned.source as string | null | undefined) : undefined) ?? current.source ?? null,
      };
    });
  }, []);

  const orderedRuns = useMemo(() => {
    return runs.slice().sort((a, b) => new Date(b.updated_at).valueOf() - new Date(a.updated_at).valueOf());
  }, [runs]);

  const isBusy = Boolean(busyAction);

  const refreshRuns = useCallback(async () => {
    try {
      const data = await listOrchestratorRuns();
      setRuns(data);
      setStatus((current) => {
        if (!current) {
          return current;
        }
        const latest = data.find((run) => run.run_id === current.run_id);
        return latest ? { ...current, ...latest } : current;
      });
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to refresh orchestrator runs", error) });
    }
  }, []);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  const handleFileChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const next = event.target.files?.[0] ?? null;
    setFile(next);
  }, []);

  const pickFormatHint = useCallback((target: File | null): FormatHint => {
    if (!target) {
      return undefined;
    }
    const lower = target.name.toLowerCase();
    if (lower.endsWith(".pdf")) return "pdf";
    if (lower.endsWith(".md")) return "md";
    if (lower.endsWith(".docx")) return "docx";
    if (lower.endsWith(".txt")) return "txt";
    return undefined;
  }, []);

  const loadSession = useCallback(
    async (runId: string, actionLabel: string = "loading") => {
      const requestId = ++loadRequestIdRef.current;
      setBusyAction(actionLabel);
      setBanner(null);
      try {
        const nextStatus = await getOrchestratorRun(runId);
        if (requestId !== loadRequestIdRef.current) {
          return;
        }
        setSelectedRunId(runId);
        setStatus(nextStatus);
        mergeRunSnapshot(runId, nextStatus);

        if (!nextStatus.prompts_ready) {
          setResult(null);
        }

        let summaryEnvelope: OrchestratorSummaryEnvelope | null = null;
        if (nextStatus.summary_ready) {
          try {
            summaryEnvelope = await getOrchestratorSummary(runId);
          } catch (error) {
            if (requestId === loadRequestIdRef.current) {
              setBanner({ type: "error", message: buildErrorMessage("Unable to load summary", error) });
            }
          }
        }
        if (requestId !== loadRequestIdRef.current) {
          return;
        }
        if (summaryEnvelope) {
          setSummary(summaryEnvelope);
        } else if (!nextStatus.summary_ready) {
          setSummary(null);
        }

        let milestonesEnvelope: OrchestratorMilestonesEnvelope | null = null;
        if (nextStatus.milestones_ready) {
          try {
            milestonesEnvelope = await getOrchestratorMilestones(runId);
          } catch (error) {
            if (requestId === loadRequestIdRef.current) {
              setBanner({ type: "error", message: buildErrorMessage("Unable to load milestones", error) });
            }
          }
        }
        if (requestId !== loadRequestIdRef.current) {
          return;
        }
        if (milestonesEnvelope) {
          setMilestones(milestonesEnvelope);
        } else if (!nextStatus.milestones_ready) {
          setMilestones(null);
        }

        let promptsEnvelope: OrchestratorPromptsEnvelope | null = null;
        if (nextStatus.prompts_ready) {
          try {
            promptsEnvelope = await getOrchestratorPrompts(runId);
          } catch (error) {
            if (requestId === loadRequestIdRef.current) {
              setBanner({ type: "error", message: buildErrorMessage("Unable to load prompts", error) });
            }
          }
        }
        if (requestId !== loadRequestIdRef.current) {
          return;
        }
        if (promptsEnvelope) {
          setPrompts(promptsEnvelope);
        } else if (!nextStatus.prompts_ready) {
          setPrompts(null);
        }
      } catch (error) {
        if (requestId === loadRequestIdRef.current) {
          setBanner({ type: "error", message: buildErrorMessage("Unable to load orchestrator run", error) });
        }
        throw error;
      } finally {
        if (requestId === loadRequestIdRef.current) {
          setBusyAction(null);
        }
      }
    },
    [mergeRunSnapshot],
  );
  useEffect(() => {
    if (selectedRunId || isBusy || orderedRuns.length === 0) {
      return;
    }
    const next = orderedRuns[0];
    if (next) {
      loadSession(next.run_id, "auto-load").catch(() => undefined);
    }
  }, [orderedRuns, selectedRunId, isBusy, loadSession]);

  useEffect(() => {
    if (!selectedRunId) {
      return;
    }
    if (runs.some((run) => run.run_id === selectedRunId)) {
      return;
    }
    setSelectedRunId(null);
    setStatus(null);
    setSummary(null);
    setMilestones(null);
    setPrompts(null);
    setResult(null);
  }, [runs, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId || isBusy) {
      return;
    }
    const latest = runs.find((run) => run.run_id === selectedRunId);
    if (!latest) {
      return;
    }

    setStatus((current) => {
      if (
        !current ||
        current.updated_at !== latest.updated_at ||
        current.summary_ready !== latest.summary_ready ||
        current.summary_approved !== latest.summary_approved ||
        current.milestones_ready !== latest.milestones_ready ||
        current.milestones_approved !== latest.milestones_approved ||
        current.prompts_ready !== latest.prompts_ready ||
        current.source !== latest.source
      ) {
        return { ...current, ...latest };
      }
      return current;
    });

    const needsSummary = latest.summary_ready && !summary;
    const needsMilestones = latest.milestones_ready && !milestones;
    const needsPrompts = latest.prompts_ready && !prompts;

    if (needsSummary || needsMilestones || needsPrompts) {
      loadSession(latest.run_id, "sync").catch(() => undefined);
    }
  }, [selectedRunId, runs, summary, milestones, prompts, isBusy, loadSession]);


  const handleSelectRun = useCallback(
    async (runId: string) => {
      if (!runId) {
        setSelectedRunId(null);
        setStatus(null);
        setSummary(null);
        setMilestones(null);
        setPrompts(null);
        setResult(null);
        return;
      }
      try {
        await loadSession(runId);
      } catch {
        /* errors surfaced via respective handlers */
      }
    },
    [loadSession],
  );

  const handleCreateRun = useCallback(async () => {
    if (!file) {
      setBanner({ type: "error", message: "Upload a blueprint before starting the orchestrator." });
      return;
    }
    setBusyAction("create");
    setBanner(null);
    try {
      const blueprint = await fileToBlueprint(file);
      if (!blueprint) {
        throw new Error("Blueprint could not be read. Try a different file.");
      }
      const response = await createOrchestratorRun({
        blueprint,
        filename: file.name,
        format_hint: pickFormatHint(file),
      });
      setFile(null);
      const now = new Date().toISOString();
      const snapshot: OrchestratorSessionStatus = {
        run_id: response.run_id,
        source: response.source ?? null,
        summary_ready: true,
        summary_approved: false,
        milestones_ready: false,
        milestones_approved: false,
        prompts_ready: false,
        created_at: now,
        updated_at: now,
      };
      setSelectedRunId(response.run_id);
      setStatus(snapshot);
      setSummary(response);
      setMilestones(null);
      setPrompts(null);
      setResult(null);
      mergeRunSnapshot(response.run_id, snapshot);
      setBanner({ type: "info", message: `Orchestrator run ${response.run_id} created.` });
      await refreshRuns();
      await loadSession(response.run_id);
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to create orchestrator run", error) });
    } finally {
      setBusyAction(null);
    }
  }, [file, pickFormatHint, refreshRuns, loadSession, mergeRunSnapshot]);

  const handleRegenerateSummary = useCallback(async () => {
    if (!selectedRunId) {
      return;
    }
    setBusyAction("regenerate");
    setBanner(null);
    try {
      const envelope = await regenerateOrchestratorSummary(selectedRunId);
      setSummary(envelope);
      setMilestones(null);
      setPrompts(null);
      setResult(null);
      const now = new Date().toISOString();
      mergeRunSnapshot(selectedRunId, {
        summary_ready: true,
        summary_approved: false,
        milestones_ready: false,
        milestones_approved: false,
        prompts_ready: false,
        updated_at: now,
      });
      setBanner({ type: "info", message: "Summary regenerated." });
      await refreshRuns();
      await loadSession(selectedRunId);
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to regenerate summary", error) });
    } finally {
      setBusyAction(null);
    }
  }, [selectedRunId, refreshRuns, loadSession, mergeRunSnapshot]);

  const handleSummaryDecision = useCallback(
    async (approved: boolean) => {
      if (!selectedRunId) {
        return;
      }
      setBusyAction("summary-decision");
      setBanner(null);
      try {
        await submitOrchestratorSummaryDecision(selectedRunId, approved);
        const now = new Date().toISOString();
        mergeRunSnapshot(selectedRunId, { summary_approved: approved, updated_at: now });
        setBanner({
          type: "info",
          message: approved ? "Summary approved." : "Summary marked for revision.",
        });
        await refreshRuns();
        await loadSession(selectedRunId);
      } catch (error) {
        setBanner({ type: "error", message: buildErrorMessage("Unable to record summary decision", error) });
      } finally {
        setBusyAction(null);
      }
    },
    [selectedRunId, refreshRuns, loadSession, mergeRunSnapshot],
  );

  const handleGenerateMilestones = useCallback(async () => {
    if (!selectedRunId) {
      return;
    }
    setBusyAction("milestones");
    setBanner(null);
    try {
      const envelope = await generateOrchestratorMilestones(selectedRunId);
      setMilestones(envelope);
      const now = new Date().toISOString();
      mergeRunSnapshot(selectedRunId, {
        milestones_ready: true,
        milestones_approved: false,
        updated_at: now,
      });
      setBanner({ type: "info", message: "Milestones generated." });
      await refreshRuns();
      await loadSession(selectedRunId);
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to generate milestones", error) });
    } finally {
      setBusyAction(null);
    }
  }, [selectedRunId, refreshRuns, loadSession, mergeRunSnapshot]);

  const handleMilestoneDecision = useCallback(
    async (approved: boolean) => {
      if (!selectedRunId) {
        return;
      }
      setBusyAction("milestones-decision");
      setBanner(null);
      try {
        await submitOrchestratorMilestonesDecision(selectedRunId, approved);
        const now = new Date().toISOString();
        mergeRunSnapshot(selectedRunId, { milestones_approved: approved, updated_at: now });
        setBanner({
          type: "info",
          message: approved ? "Milestones approved." : "Milestones marked for revision.",
        });
        await refreshRuns();
        await loadSession(selectedRunId);
      } catch (error) {
        setBanner({ type: "error", message: buildErrorMessage("Unable to record milestone decision", error) });
      } finally {
        setBusyAction(null);
      }
    },
    [selectedRunId, refreshRuns, loadSession, mergeRunSnapshot],
  );

  const handleGeneratePrompts = useCallback(async () => {
    if (!selectedRunId) {
      return;
    }
    setBusyAction("prompts");
    setBanner(null);
    try {
      const envelope = await generateOrchestratorPrompts(selectedRunId);
      setPrompts(envelope);
      mergeRunSnapshot(selectedRunId, { prompts_ready: true, updated_at: new Date().toISOString() });
      setBanner({ type: "info", message: "Prompt bundle generated." });
      await refreshRuns();
      await loadSession(selectedRunId);
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to generate prompts", error) });
    } finally {
      setBusyAction(null);
    }
  }, [selectedRunId, refreshRuns, loadSession, mergeRunSnapshot]);

  const handleFinalize = useCallback(async () => {
    if (!selectedRunId) {
      return;
    }
    setBusyAction("finalize");
    setBanner(null);
    try {
      const payload = await finalizeOrchestratorRun(selectedRunId);
      setResult(payload);
      mergeRunSnapshot(selectedRunId, { updated_at: new Date().toISOString() });
      setBanner({ type: "info", message: "Orchestrator result assembled." });
      await refreshRuns();
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to finalize orchestrator run", error) });
    } finally {
      setBusyAction(null);
    }
  }, [selectedRunId, refreshRuns, mergeRunSnapshot]);

  const handleRefresh = useCallback(async () => {
    try {
      if (selectedRunId) {
        await Promise.all([refreshRuns(), loadSession(selectedRunId)]);
      } else {
        await refreshRuns();
      }
    } catch {
      /* errors surfaced via respective handlers */
    }
  }, [selectedRunId, refreshRuns, loadSession]);

  const handleDelete = useCallback(async () => {
    if (!selectedRunId) {
      return;
    }
    setBusyAction("delete");
    setBanner(null);
    try {
      await deleteOrchestratorRun(selectedRunId);
      setRuns((previous) => previous.filter((run) => run.run_id !== selectedRunId));
      setSelectedRunId(null);
      setStatus(null);
      setSummary(null);
      setMilestones(null);
      setPrompts(null);
      setResult(null);
      await refreshRuns();
      setBanner({ type: "info", message: "Run discarded." });
    } catch (error) {
      setBanner({ type: "error", message: buildErrorMessage("Unable to delete orchestrator run", error) });
    } finally {
      setBusyAction(null);
    }
  }, [selectedRunId, refreshRuns]);

  return (
    <div className="space-y-6">
      <div className="rounded border border-slate-800 bg-slate-900 p-4 space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Start a Coding Orchestrator run</h2>
            <p className="text-sm text-slate-400">
              Upload an application blueprint to generate summaries, milestones, and prompt bundles ready for coding
              agents.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setFile(null)}
              className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 transition hover:bg-slate-800"
              disabled={!file || isBusy}
            >
              Clear file
            </button>
            <button
              type="button"
              onClick={() => void handleCreateRun()}
              className="rounded bg-emerald-500 px-3 py-1 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-emerald-500/30 disabled:text-slate-300"
              disabled={!file || isBusy}
            >
              Launch orchestrator
            </button>
          </div>
        </div>
        <label className="block text-sm font-medium text-slate-200">
          Blueprint file
          <input
            type="file"
            accept={ACCEPTED_BLUEPRINT_TYPES.join(",")}
            onChange={handleFileChange}
            className="mt-2 w-full cursor-pointer rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 file:mr-4 file:rounded file:border-0 file:bg-slate-800 file:px-3 file:py-1 file:text-sm file:text-slate-200"
          />
        </label>
        {file && (
          <p className="text-xs text-slate-400">
            Selected: {file.name} ({Math.round(file.size / 1024)} KB)
          </p>
        )}
      </div>

      <div className="rounded border border-slate-800 bg-slate-900 p-4 space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Active runs</h2>
            <p className="text-sm text-slate-400">
              Select a run to review outputs, approve checkpoints, or continue the workflow.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void handleRefresh()}
              className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed"
              disabled={isBusy}
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={() => void handleDelete()}
              className="rounded border border-rose-500 px-3 py-1 text-xs text-rose-300 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
              disabled={!selectedRunId || isBusy}
            >
              Discard run
            </button>
          </div>
        </div>
        <select
          value={selectedRunId ?? ""}
          onChange={(event) => void handleSelectRun(event.target.value)}
          className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
          disabled={isBusy || orderedRuns.length === 0}
        >
          <option value="">Select a run.</option>
          {orderedRuns.map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {formatRunLabel(run)}
            </option>
          ))}
        </select>
        {status && (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            <SimpleStatus label="Summary ready" value={status.summary_ready} />
            <SimpleStatus label="Summary approved" value={status.summary_approved} variant="approval" />
            <SimpleStatus label="Milestones ready" value={status.milestones_ready} />
            <SimpleStatus label="Milestones approved" value={status.milestones_approved} variant="approval" />
            <SimpleStatus label="Prompts ready" value={status.prompts_ready} />
            <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-400">
              <p>Source: {status.source ?? "Uploaded file"}</p>
              <p>Updated: {new Date(status.updated_at).toLocaleString()}</p>
            </div>
          </div>
        )}
        {banner && (
          <div
            className={`rounded border px-3 py-2 text-sm ${
              banner.type === "error"
                ? "border-rose-400/40 bg-rose-500/15 text-rose-200"
                : "border-emerald-400/40 bg-emerald-500/15 text-emerald-200"
            }`}
          >
            {banner.message}
          </div>
        )}
        {busyAction && <p className="text-xs text-slate-500">Working on: {busyAction}.</p>}
      </div>

      <div className="space-y-6">
        <div className="rounded border border-slate-800 bg-slate-900 p-4">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h3 className="text-lg font-semibold text-slate-100">Blueprint summary</h3>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void handleRegenerateSummary()}
                className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed"
                disabled={!selectedRunId || isBusy}
              >
                Regenerate
              </button>
              <button
                type="button"
                onClick={() => void handleSummaryDecision(true)}
                className="rounded bg-emerald-500 px-3 py-1 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-emerald-500/30 disabled:text-slate-300"
                disabled={!selectedRunId || !status?.summary_ready || isBusy}
              >
                Approve
              </button>
              <button
                type="button"
                onClick={() => void handleSummaryDecision(false)}
                className="rounded border border-amber-400 px-3 py-1 text-xs font-semibold text-amber-300 transition hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
                disabled={!selectedRunId || !status?.summary_ready || isBusy}
              >
                Needs revision
              </button>
            </div>
          </div>
          {summary ? (
            <div className="space-y-4">
              <p className="whitespace-pre-line text-sm text-slate-200">{summary.summary.summary}</p>
              <div className="grid gap-4 md:grid-cols-2">
                <ListSection heading="Highlights" items={summary.summary.highlights} emptyLabel="No highlights recorded." />
                <ListSection heading="Risks" items={summary.summary.risks} emptyLabel="No risks captured." />
                <ListSection heading="Components" items={summary.summary.components} emptyLabel="No components extracted." />
                <ListSection heading="Metadata"
                  items={Object.entries(summary.summary.metadata || {}).map(([key, value]) => `${key}: ${String(value)}`)}
                  emptyLabel="No metadata captured."
                />
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-400">Generate a summary to review orchestrator output.</p>
          )}
        </div>

        <div className="rounded border border-slate-800 bg-slate-900 p-4">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h3 className="text-lg font-semibold text-slate-100">Milestone plan</h3>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void handleGenerateMilestones()}
                className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed"
                disabled={!selectedRunId || isBusy || !status?.summary_approved}
              >
                Generate milestones
              </button>
              <button
                type="button"
                onClick={() => void handleMilestoneDecision(true)}
                className="rounded bg-emerald-500 px-3 py-1 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-emerald-500/30 disabled:text-slate-300"
                disabled={!selectedRunId || !status?.milestones_ready || isBusy}
              >
                Approve
              </button>
              <button
                type="button"
                onClick={() => void handleMilestoneDecision(false)}
                className="rounded border border-amber-400 px-3 py-1 text-xs font-semibold text-amber-300 transition hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
                disabled={!selectedRunId || !status?.milestones_ready || isBusy}
              >
                Needs revision
              </button>
            </div>
          </div>
          {milestones ? (
            <div className="space-y-4">
              <ul className="space-y-3 text-sm text-slate-200">
                {milestones.milestones.milestones.map((item) => (
                  <li key={item.milestone_id} className="rounded border border-slate-800 bg-slate-950 p-3">
                    <p className="text-xs uppercase tracking-wide text-slate-500">Milestone {item.milestone_id}</p>
                    <p className="mt-1 font-semibold">{item.details}</p>
                    {item.context && <p className="mt-2 text-xs text-slate-400">Context: {item.context}</p>}
                  </li>
                ))}
              </ul>
              <div className="grid gap-3 md:grid-cols-2">
                <ListSection
                  heading="Covered graph nodes"
                  items={milestones.graph.covered_nodes}
                  emptyLabel="No nodes marked as covered yet."
                />
                <ListSection
                  heading="Uncovered nodes"
                  items={milestones.graph.uncovered_nodes}
                  emptyLabel="All nodes accounted for."
                />
              </div>
              {milestones.graph.notes && (
                <p className="text-xs text-slate-400">Notes: {milestones.graph.notes}</p>
              )}
              {milestones.milestones.raw_response && (
                <details className="rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400">
                  <summary className="cursor-pointer text-slate-200">View raw response</summary>
                  <pre className="mt-2 whitespace-pre-wrap break-words text-[11px] leading-relaxed">
                    {milestones.milestones.raw_response}
                  </pre>
                </details>
              )}
            </div>
          ) : (
            <p className="text-sm text-slate-400">Generate milestones after approving the summary.</p>
          )}
        </div>

        <div className="rounded border border-slate-800 bg-slate-900 p-4">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h3 className="text-lg font-semibold text-slate-100">Prompt bundle</h3>
            <button
              type="button"
              onClick={() => void handleGeneratePrompts()}
              className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed"
              disabled={!selectedRunId || isBusy || !status?.milestones_approved}
            >
              Generate prompts
            </button>
          </div>
          {prompts ? (
            <div className="space-y-3">
              {prompts.prompts.prompts.map((prompt) => (
                <div key={`${prompt.milestone_id}-${prompt.title}`} className="rounded border border-slate-800 bg-slate-950 p-3">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Milestone {prompt.milestone_id}</p>
                  <h4 className="mt-1 font-semibold text-slate-100">{prompt.title}</h4>
                  <section className="mt-2 space-y-1 text-xs text-slate-300">
                    <div>
                      <p className="font-semibold text-slate-200">System prompt</p>
                      <p className="whitespace-pre-line text-slate-300">{prompt.system_prompt}</p>
                    </div>
                    <div>
                      <p className="font-semibold text-slate-200">User prompt</p>
                      <p className="whitespace-pre-line text-slate-300">{prompt.user_prompt}</p>
                    </div>
                    <ListSection
                      heading="Acceptance criteria"
                      items={prompt.acceptance_criteria}
                      emptyLabel="No acceptance criteria provided."
                    />
                    <ListSection
                      heading="Expected artifacts"
                      items={prompt.expected_artifacts}
                      emptyLabel="No artifacts listed."
                    />
                    <ListSection
                      heading="References"
                      items={prompt.references}
                      emptyLabel="No references supplied."
                    />
                  </section>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-400">Generate prompts after approving the milestone plan.</p>
          )}
        </div>

        <div className="rounded border border-slate-800 bg-slate-900 p-4">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h3 className="text-lg font-semibold text-slate-100">Finalize orchestrator run</h3>
            <button
              type="button"
              onClick={() => void handleFinalize()}
              className="rounded bg-emerald-500 px-3 py-1 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-emerald-500/30 disabled:text-slate-300"
              disabled={!selectedRunId || !status?.prompts_ready || isBusy}
            >
              Assemble result
            </button>
          </div>
          {result ? (
            <div className="space-y-3 text-sm text-slate-200">
              <p>Run {result.run_id} finalized at {new Date(result.generated_at).toLocaleString()}.</p>
              <p className="text-xs text-slate-400">
                Summary, milestones, and prompts are included below for convenient copy-paste into downstream tools.
              </p>
              <details className="rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200">
                <summary className="cursor-pointer font-semibold">Summary</summary>
                <p className="mt-2 whitespace-pre-line text-slate-300">{result.summary.summary}</p>
              </details>
              <details className="rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200">
                <summary className="cursor-pointer font-semibold">Milestones</summary>
                <ul className="mt-2 space-y-2">
                  {result.milestones.milestones.map((milestone) => (
                    <li key={milestone.milestone_id}>
                      <span className="font-semibold text-slate-100">Milestone {milestone.milestone_id}:</span> {milestone.details}
                    </li>
                  ))}
                </ul>
              </details>
              <details className="rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200">
                <summary className="cursor-pointer font-semibold">Prompts</summary>
                <ul className="mt-2 space-y-2">
                  {result.prompts.prompts.map((prompt) => (
                    <li key={`${prompt.milestone_id}-${prompt.title}`}>
                      <span className="font-semibold text-slate-100">{prompt.title}:</span> {prompt.system_prompt}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          ) : (
            <p className="text-sm text-slate-400">Assemble the orchestrator result after prompts are generated.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ListSection({
  heading,
  items,
  emptyLabel,
}: {
  heading: string;
  items: Array<string | number>;
  emptyLabel: string;
}): JSX.Element {
  if (!items || items.length === 0) {
    return (
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{heading}</h4>
        <p className="mt-1 text-xs text-slate-500">{emptyLabel}</p>
      </div>
    );
  }
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{heading}</h4>
      <ul className="mt-1 space-y-1 text-xs text-slate-300">
        {items.map((item) => (
          <li key={`${heading}-${item}`} className="rounded bg-slate-800/40 px-2 py-1">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SimpleStatus({
  label,
  value,
  variant = "neutral",
}: {
  label: string;
  value: boolean;
  variant?: "neutral" | "approval";
}): JSX.Element {
  let style = "border border-slate-700 bg-slate-950 text-slate-200";
  if (value) {
    style = "border border-emerald-400/40 bg-emerald-500/10 text-emerald-200";
  } else if (variant === "approval") {
    style = "border border-amber-400/40 bg-amber-500/15 text-amber-200";
  }
  return (
    <span
      className={`flex items-center justify-between gap-3 rounded px-3 py-2 text-[11px] font-semibold uppercase tracking-wide ${style}`}
    >
      <span>{label}</span>
      <span>{value ? "Yes" : "No"}</span>
    </span>
  );
}

function formatRunLabel(run: OrchestratorSessionStatus): string {
  const summaryState = run.summary_ready ? (run.summary_approved ? "summary approved" : "summary ready") : "summary pending";
  const milestoneState = run.milestones_ready
    ? run.milestones_approved
      ? "milestones approved"
      : "milestones ready"
    : "milestones pending";
  const promptState = run.prompts_ready ? "prompts ready" : "prompts pending";
  return `${run.run_id} - ${summaryState}, ${milestoneState}, ${promptState}`;
}

async function fileToBlueprint(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const base64 = arrayBufferToBase64(buffer);
  return `base64:${file.type || "application/octet-stream"}:${base64}`;
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    const segment = bytes.subarray(i, i + chunk);
    binary += String.fromCharCode(...segment);
  }
  if (typeof btoa === "undefined") {
    return Buffer.from(binary, "binary").toString("base64");
  }
  return btoa(binary);
}


function buildErrorMessage(context: string, error: unknown): string {
  const detail = extractErrorDetail(error);
  return detail ? `${context}: ${detail}` : `${context}: Unexpected error.`;
}

function extractErrorDetail(error: unknown): string {
  if (error instanceof HttpError) {
    const statusLabel = formatHttpStatus(error);
    const htmlLike = isHtmlErrorDetail(error);
    if (!htmlLike && error.detail) {
      const cleanedDetail = sanitizeErrorText(error.detail);
      if (cleanedDetail) {
        return cleanedDetail;
      }
    }
    if (!htmlLike) {
      const cleanedMessage = sanitizeErrorText(error.message);
      if (cleanedMessage && cleanedMessage !== statusLabel) {
        return cleanedMessage;
      }
    }
    return statusLabel || sanitizeErrorText(error.message);
  }
  if (error instanceof Error && typeof error.message === "string") {
    return sanitizeErrorText(error.message);
  }
  if (typeof error === "string") {
    return sanitizeErrorText(error);
  }
  return "";
}

function isHtmlErrorDetail(error: HttpError): boolean {
  if (error.contentType && error.contentType.includes("text/html")) {
    return true;
  }
  const candidate = error.detail ?? error.body ?? "";
  return /<[^>]+>/.test(candidate);
}

function formatHttpStatus(error: HttpError): string {
  if (!error.status) {
    return "";
  }
  const statusText = error.statusText.trim();
  return statusText ? `HTTP ${error.status} ${statusText}` : `HTTP ${error.status}`;
}

function sanitizeErrorText(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    return "";
  }
  const plain = /<[a-z!/][^>]*>/i.test(trimmed) ? trimmed.replace(/<[^>]+>/g, " ") : trimmed;
  const normalized = plain.replace(/\s+/g, " " ).trim();
  if (!normalized) {
    return "";
  }
  return normalized.length > 300 ? `${normalized.slice(0, 300).trimEnd()}...` : normalized;
}

function omitUndefined<T extends Record<string, unknown>>(input: T): T {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined) {
      result[key] = value;
    }
  }
  return result as T;
}
