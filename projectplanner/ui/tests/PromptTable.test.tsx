import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PromptStep } from "@/lib/api";
import { PromptTable } from "../components/PromptTable";

describe("PromptTable", () => {
  const baseStep: PromptStep = {
    id: "step-001",
    title: "Discovery",
    system_prompt: "Do the thing",
    user_prompt: "List tasks",
    expected_artifacts: ["Create plan"],
    tools: ["editor"],
    acceptance_criteria: ["Has plan"],
    inputs: ["research"],
    outputs: ["plan"],
    token_budget: 500,
    cited_artifacts: ["research-brief"],
    rubric_score: 0.9,
  };

  it("calls onStepsChange when editing a title", async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<PromptTable steps={[baseStep]} onStepsChange={handler} />);
    const input = screen.getByDisplayValue("Discovery");
    await user.clear(input);
    await user.type(input, "Exploration");
    expect(handler).toHaveBeenCalled();
  });
});
