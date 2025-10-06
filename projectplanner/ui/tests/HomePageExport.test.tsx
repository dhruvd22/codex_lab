import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import HomePage from "../pages/index";
import { exportPrompts, updateSteps } from "@/lib/api";

const basePlan = {
  plan: {
    context: "Modernize onboarding flows for new users.",
    goals: ["Reduce time-to-value", "Improve completion rates"],
    assumptions: ["Data warehouse is available"],
    non_goals: ["No billing changes"],
    risks: ["Timeline pressure"],
    milestones: [
      "Milestone 1: Confirm requirements",
      "Milestone 2: Draft architecture",
    ],
  },
  steps: [
    {
      id: "step-001",
      title: "Confirm requirements",
      system_prompt: "System",
      user_prompt: "Gather requirements",
      expected_artifacts: ["Create requirements"],
      tools: ["editor"],
      acceptance_criteria: ["Has sign-off"],
      inputs: ["research"],
      outputs: ["requirements"],
      token_budget: 400,
      cited_artifacts: [],
      rubric_score: 0.8,
    },
    {
      id: "step-002",
      title: "Draft architecture",
      system_prompt: "System",
      user_prompt: "Draft architecture",
      expected_artifacts: ["Create architecture"],
      tools: ["editor"],
      acceptance_criteria: ["Has diagrams"],
      inputs: ["research"],
      outputs: ["architecture"],
      token_budget: 400,
      cited_artifacts: [],
      rubric_score: 0.82,
    },
  ],
  report: {
    run_id: "run-123",
    generated_at: new Date().toISOString(),
    overall_score: 0.9,
    strengths: ["Well scoped"],
    concerns: ["Watch delivery"],
    step_feedback: [],
  },
};

let latestSteps = basePlan.steps.map((step) => ({ ...step }));

vi.mock("@/lib/api", () => {
  const ingestDocument = vi.fn(async () => ({
    run_id: "run-123",
    stats: { word_count: 100, char_count: 500, chunk_count: 5 },
  }));

  const generatePlan = vi.fn(async (_payload, onEvent?: (event: string, data: unknown) => void) => {
    latestSteps = basePlan.steps.map((step) => ({ ...step }));
    onEvent?.("planner_started", { run_id: "run-123" });
    onEvent?.("planner_completed", { plan: basePlan.plan });
    onEvent?.("decomposer_completed", { steps: latestSteps });
    onEvent?.("reviewer_completed", { report: basePlan.report });
    onEvent?.("final_plan", { run_id: "run-123", ...basePlan });
    return {
      plan: basePlan.plan,
      steps: basePlan.steps.map((step) => ({ ...step })),
      report: basePlan.report,
    };
  });

  const updateSteps = vi.fn(async (_runId: string, steps) => {
    latestSteps = steps.map((step: typeof basePlan.steps[number]) => ({ ...step }));
    return { run_id: "run-123", steps: latestSteps };
  });

  const exportPrompts = vi.fn(async () => {
    return new Blob([JSON.stringify({ steps: latestSteps })], { type: "application/json" });
  });

  return {
    ingestDocument,
    generatePlan,
    exportPrompts,
    updateSteps,
    getSteps: vi.fn(),
  };
});

describe("HomePage export flow", () => {
  let createObjectURLSpy: ReturnType<typeof vi.spyOn>;
  let revokeSpy: ReturnType<typeof vi.spyOn>;
  let clickSpy: ReturnType<typeof vi.spyOn>;
  let exportBlobPromise: Promise<string> | null = null;

  beforeEach(() => {
    exportBlobPromise = null;
    createObjectURLSpy = vi.spyOn(URL, "createObjectURL").mockImplementation((blob: Blob) => {
      exportBlobPromise = blob.text();
      return "blob:mock";
    });
    revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    createObjectURLSpy.mockRestore();
    revokeSpy.mockRestore();
    clickSpy.mockRestore();
  });

  it("updates export blob after editing a step title", async () => {
    const user = userEvent.setup();
    render(<HomePage />);

    const uploadInput = screen.getByLabelText(/Upload blueprint/i) as HTMLInputElement;
    const file = new File(["Sample research text"], "blueprint.md", { type: "text/markdown" });
    await user.upload(uploadInput, file);

    await user.click(screen.getByRole("button", { name: "Ingest Blueprint" }));
    await screen.findByText(/Ingestion complete/i);

    await user.click(screen.getByRole("button", { name: "Generate Plan" }));
    await screen.findByText(/Plan generated successfully/i);

    const titleInput = await screen.findByDisplayValue("Confirm requirements");
    await user.clear(titleInput);
    await user.type(titleInput, "Clarify requirements phase");

    await waitFor(() => {
      expect(updateSteps).toHaveBeenCalled();
    });

    await user.click(screen.getByRole("button", { name: "YAML" }));

    await waitFor(async () => {
      expect(exportBlobPromise).not.toBeNull();
      const text = await exportBlobPromise!;
      expect(text).toContain("Clarify requirements phase");
    });

    expect(exportPrompts).toHaveBeenCalledWith({ run_id: "run-123", format: "yaml" });
    expect(createObjectURLSpy).toHaveBeenCalled();
  });
});
