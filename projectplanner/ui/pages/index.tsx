import Head from "next/head";
import { ChangeEvent, useCallback, useMemo, useState } from "react";
import {
  AgentReport,
  DocumentStats,
  PlanResponse,
  PromptPlan,
  PromptStep,
  generatePlan,
  exportPrompts as exportPromptsApi,
  updateSteps as updateStepsApi,
  ingestDocument,
} from "@/lib/api";
import { PromptTable } from "@/components/PromptTable";

const exportFormats = [
  { label: "YAML", value: "yaml" },
  { label: "JSONL", value: "jsonl" },
  { label: "Markdown", value: "md" },
];

const streamMessages: Record<string, string> = {
  planner_started: "Planner analyzing document chunks...",
  planner_completed: "Planner finished. Drafting execution steps...",
  decomposer_completed: "Decomposer drafted steps. Running reviewer...",
  reviewer_completed: "Reviewer scored the plan. Finalizing...",
};

type StyleOption = "strict" | "creative";

export default function HomePage() {
  const [textInput, setTextInput] = useState("");
  const [urlInput, setUrlInput] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [plan, setPlan] = useState<PromptPlan | null>(null);
  const [steps, setSteps] = useState<PromptStep[]>([]);
  const [report, setReport] = useState<AgentReport | null>(null);
  const [style, setStyle] = useState<StyleOption>("strict");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleFileChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
  }, []);

  const pickFormatHint = useCallback((): "pdf" | "md" | "docx" | undefined => {
    if (!file) return undefined;
    if (file.name.endsWith(".pdf")) return "pdf";
    if (file.name.endsWith(".md")) return "md";
    if (file.name.endsWith(".docx")) return "docx";
    return undefined;
  }, [file]);

  const serializeFile = useCallback(async (): Promise<string> => {
    if (!file) return "";
    const buffer = await file.arrayBuffer();
    const base64 = arrayBufferToBase64(buffer);
    return `base64:${file.type || "application/octet-stream"}:${base64}`;
  }, [file]);
  const handleIngest = useCallback(async () => {
    if (loading) return;
    setLoading(true);
    setMessage(null);
    try {
      const ingestionPayload: Record<string, unknown> = {};
      if (urlInput) {
        ingestionPayload.url = urlInput;
      }
      if (textInput) {
        ingestionPayload.text = textInput;
      }
      if (file) {
        ingestionPayload.text = await serializeFile();
        ingestionPayload.format_hint = pickFormatHint();
      }
      if (!ingestionPayload.url && !ingestionPayload.text) {
        throw new Error("Provide text, URL, or upload a file to ingest.");
      }
      const response = await ingestDocument(ingestionPayload);
      setRunId(response.run_id);
      setStats(response.stats);
      setPlan(null);
      setSteps([]);
      setReport(null);
      setMessage(`Ingestion complete. Words: ${response.stats.word_count}, chunks: ${response.stats.chunk_count}`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [file, loading, pickFormatHint, serializeFile, textInput, urlInput]);
  const handlePlan = useCallback(async () => {
    if (!runId) {
      setMessage("Ingest a document before planning.");
      return;
    }
    setLoading(true);
    setMessage(null);
    try {
      const response: PlanResponse = await generatePlan(
        { run_id: runId, style },
        (event) => {
          const next = streamMessages[event];
          if (next) {
            setMessage(next);
          }
        },
      );
      setPlan(response.plan);
      setSteps(response.steps);
      setReport(response.report);
      setMessage("Plan generated successfully.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [runId, style]);

  const handleExport = useCallback(
    async (format: "yaml" | "jsonl" | "md") => {
      if (!runId) {
        setMessage("Run a plan before exporting.");
        return;
      }
      try {
        const blob = await exportPromptsApi({ run_id: runId, format });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `prompts-${runId}.${format}`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
      } catch (error) {
        setMessage((error as Error).message);
      }
    },
    [runId],
  );
  const handleStepsChange = useCallback(
    (updatedSteps: PromptStep[]) => {
      setSteps(updatedSteps);
      if (runId) {
        updateStepsApi(runId, updatedSteps).catch((error: Error) => {
          setMessage(error.message);
        });
      }
    },
    [runId],
  );
  const planSummary = useMemo(() => {
    if (!plan) return null;
    return (
      <div className="space-y-4">
        <Section heading="Context" items={[plan.context]} />
        <Section heading="Goals" items={plan.goals} />
        <Section heading="Assumptions" items={plan.assumptions} />
        <Section heading="Non-goals" items={plan.non_goals} />
        <Section heading="Risks" items={plan.risks} />
        <Section heading="Milestones" items={plan.milestones} />
      </div>
    );
  }, [plan]);

  return (
    <>
      <Head>
        <title>Project Planner</title>
      </Head>
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-8">
        <h1 className="text-3xl font-semibold text-white">Project Planner</h1>
        <p className="text-slate-400">
          Upload research, pick a strategy, and generate executable prompts for your AI coding agent.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <textarea
            value={textInput}
            onChange={(event) => setTextInput(event.target.value)}
            placeholder="Paste research or solution doc markdown here..."
            className="h-48 w-full rounded border border-slate-700 bg-slate-900 p-3 text-sm text-slate-100"
          />
          <div className="space-y-4 rounded border border-slate-800 bg-slate-900 p-4">
            <label className="block text-sm font-medium text-slate-200">Or supply a URL</label>
            <input
              type="url"
              value={urlInput}
              onChange={(event) => setUrlInput(event.target.value)}
              placeholder="https://example.com/deep-dive.pdf"
              className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            />
            <label className="block text-sm font-medium text-slate-200">Upload document</label>
            <input
              type="file"
              accept=".pdf,.md,.docx,.txt"
              onChange={handleFileChange}
              className="block w-full text-sm text-slate-300"
            />
            <div className="space-y-2">
              <label className="block text-sm font-medium text-slate-200">Strategy</label>
              <select
                value={style}
                onChange={(event) => setStyle(event.target.value as StyleOption)}
                className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              >
                <option value="strict">Strict & deterministic</option>
                <option value="creative">Creative exploration</option>
              </select>
            </div>
            <button
              type="button"
              onClick={handleIngest}
              disabled={loading}
              className="w-full rounded bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950"
            >
              {loading ? "Processing..." : "Ingest Document"}
            </button>
            <button
              type="button"
              onClick={handlePlan}
              disabled={!runId || loading}
              className="w-full rounded bg-slate-200 px-4 py-2 text-sm font-semibold text-slate-950"
            >
              Generate Plan
            </button>
            {stats && (
              <div className="rounded bg-slate-800 p-3 text-xs text-slate-300">
                <p>Run: {runId}</p>
                <p>Words: {stats.word_count}</p>
                <p>Chunks: {stats.chunk_count}</p>
              </div>
            )}
          </div>
        </div>

        {message && <div className="rounded bg-slate-800 p-3 text-sm text-slate-200">{message}</div>}
        {plan && (
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="rounded border border-slate-800 bg-slate-900 p-4">
              <h2 className="mb-3 text-lg font-semibold text-slate-100">Plan</h2>
              {planSummary}
            </div>
            <div className="lg:col-span-2 space-y-4">
              <div className="rounded border border-slate-800 bg-slate-900 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-slate-100">Steps</h2>
                  <ExportButtons onExport={handleExport} />
                </div>
                <PromptTable steps={steps} onStepsChange={handleStepsChange} />
              </div>
              {report && <ReportCard report={report} />}
            </div>
          </div>
        )}
      </main>
    </>
  );
}
type SectionProps = { heading: string; items: string[] };

function Section({ heading, items }: SectionProps) {
  return (
    <section>
      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{heading}</h3>
      <ul className="mt-2 space-y-1 text-sm text-slate-200">
        {items.map((item) => (
          <li key={item} className="rounded bg-slate-800/50 px-2 py-1">
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}
type ExportButtonsProps = {
  onExport: (format: "yaml" | "jsonl" | "md") => void;
};

function ExportButtons({ onExport }: ExportButtonsProps) {
  return (
    <div className="flex gap-2">
      {exportFormats.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onExport(option.value as "yaml" | "jsonl" | "md")}
          className="rounded bg-slate-800 px-3 py-1 text-xs text-slate-200 transition hover:bg-slate-700"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

type ReportCardProps = {
  report: AgentReport;
};

function ReportCard({ report }: ReportCardProps) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-2 text-lg font-semibold text-slate-100">Reviewer Report</h2>
      <p className="text-sm text-slate-300">Overall score: {report.overall_score.toFixed(2)}</p>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-emerald-300">Strengths</h3>
          <ul className="mt-2 space-y-1 text-sm text-slate-200">
            {report.strengths.map((item) => (
              <li key={item} className="rounded bg-slate-800/40 px-2 py-1">
                {item}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-rose-300">Concerns</h3>
          <ul className="mt-2 space-y-1 text-sm text-slate-200">
            {report.concerns.map((item) => (
              <li key={item} className="rounded bg-slate-800/40 px-2 py-1">
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <h3 className="mt-4 text-xs font-semibold uppercase text-slate-400">Per-step feedback</h3>
      <ul className="mt-2 space-y-1 text-xs text-slate-300">
        {report.step_feedback.map((feedback) => (
          <li key={feedback.step_id}>
            <span className="font-semibold text-slate-200">{feedback.step_id}</span>: {feedback.notes}
          </li>
        ))}
      </ul>
    </div>
  );
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
