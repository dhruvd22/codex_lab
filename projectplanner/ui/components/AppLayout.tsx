import { ReactNode, useState } from "react";

import { LoggingPanel } from "@/components/LoggingPanel";

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const [activeTab, setActiveTab] = useState<"planner" | "logging">("planner");

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-3">
          <span className="text-lg font-semibold text-slate-100">Project Planner</span>
          <nav className="flex gap-2 text-sm">
            <button
              type="button"
              onClick={() => setActiveTab("planner")}
              className={`rounded px-3 py-1 transition ${
                activeTab === "planner"
                  ? "bg-emerald-500 text-slate-950"
                  : "bg-slate-800 text-slate-200 hover:bg-slate-700"
              }`}
            >
              Planner
            </button>
            <button
              type="button"
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
      <div className="mx-auto w-full max-w-6xl px-4 py-6">
        {activeTab === "planner" ? children : <LoggingPanel />}
      </div>
    </div>
  );
}
