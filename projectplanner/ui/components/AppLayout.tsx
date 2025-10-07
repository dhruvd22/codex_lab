import { ReactNode, useState } from "react";

import { LoggingPanel } from "@/components/LoggingPanel";
import { ObservabilityDashboard } from "@/components/ObservabilityDashboard";
import { OrchestratorPanel } from "@/components/OrchestratorPanel";
import { PromptLogPanel } from "@/components/PromptLogPanel";

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const [activeTab, setActiveTab] = useState<"planner" | "orchestrator" | "prompts" | "logging" | "observability">("planner");

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-3">
          <span className="text-lg font-semibold text-slate-100">The Coding Conductor</span>
          <nav className="flex gap-2 text-sm" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "planner"}
              onClick={() => setActiveTab("planner")}
              className={`rounded px-3 py-1 transition ${
                activeTab === "planner"
                  ? "bg-emerald-500 text-slate-950"
                  : "bg-slate-800 text-slate-200 hover:bg-slate-700"
              }`}
            >
              Conductor
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "orchestrator"}
              onClick={() => setActiveTab("orchestrator")}
              className={`rounded px-3 py-1 transition ${
                activeTab === "orchestrator"
                  ? "bg-emerald-500 text-slate-950"
                  : "bg-slate-800 text-slate-200 hover:bg-slate-700"
              }`}
            >
              Orchestrator
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "prompts"}
              onClick={() => setActiveTab("prompts")}
              className={`rounded px-3 py-1 transition ${
                activeTab === "prompts"
                  ? "bg-emerald-500 text-slate-950"
                  : "bg-slate-800 text-slate-200 hover:bg-slate-700"
              }`}
            >
              Prompts
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "observability"}
              onClick={() => setActiveTab("observability")}
              className={`rounded px-3 py-1 transition ${
                activeTab === "observability"
                  ? "bg-emerald-500 text-slate-950"
                  : "bg-slate-800 text-slate-200 hover:bg-slate-700"
              }`}
            >
              Observability
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "logging"}
              onClick={() => setActiveTab("logging")}
              className={`rounded px-3 py-1 transition ${
                activeTab === "logging"
                  ? "bg-emerald-500 text-slate-950"
                  : "bg-slate-800 text-slate-200 hover:bg-slate-700"
              }`}
            >
              Logging
            </button>
          </nav>
        </div>
      </header>
      <div className="mx-auto w-full max-w-6xl px-4 py-6 space-y-6">
        <div
          role="tabpanel"
          aria-hidden={activeTab !== "planner"}
          className={activeTab === "planner" ? "" : "hidden"}
        >
          {children}
        </div>
        <div
          role="tabpanel"
          aria-hidden={activeTab !== "orchestrator"}
          className={activeTab === "orchestrator" ? "" : "hidden"}
        >
          <OrchestratorPanel />
        </div>

        <div
          role="tabpanel"
          aria-hidden={activeTab !== "prompts"}
          className={activeTab === "prompts" ? "" : "hidden"}
        >
          <PromptLogPanel />
        </div>

        <div
          role="tabpanel"
          aria-hidden={activeTab !== "observability"}
          className={activeTab === "observability" ? "" : "hidden"}
        >
          <ObservabilityDashboard />
        </div>

        <div
          role="tabpanel"
          aria-hidden={activeTab !== "logging"}
          className={activeTab === "logging" ? "" : "hidden"}
        >
          <LoggingPanel />
        </div>
      </div>
    </div>
  );
}








