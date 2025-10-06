import { test, expect } from "@playwright/test";
import { readFile } from "fs/promises";

const planResponse = {
  run_id: "run-123",
  plan: {
    context: "Modernize onboarding",
    goals: ["Improve retention"],
    assumptions: ["Stakeholders available"],
    non_goals: ["No billing changes"],
    risks: ["Timeline pressure"],
    milestones: [
      "Milestone 1: Confirm requirements",
      "Milestone 2: Draft architecture",
      "Milestone 3: Implement workflow",
      "Milestone 4: Validate outcomes",
      "Milestone 5: Finalize delivery",
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

const sseBody = [
  "event: planner_started",
  "data: {"run_id":"run-123"}",
  "",
  "event: planner_completed",
  `data: ${JSON.stringify({ plan: planResponse.plan })}`,
  "",
  "event: decomposer_completed",
  `data: ${JSON.stringify({ steps: planResponse.steps })}`,
  "",
  "event: reviewer_completed",
  `data: ${JSON.stringify({ report: planResponse.report, steps: planResponse.steps })}`,
  "",
  "event: final_plan",
  `data: ${JSON.stringify(planResponse)}`,
  "",
].join("
");

test("planner export reflects edited title", async ({ page }) => {
  let latestSteps = planResponse.steps.map((step) => ({ ...step }));

  await page.route("**/api/codingconductor/ingest", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run_id: planResponse.run_id, stats: { word_count: 120, char_count: 600, chunk_count: 3 } }),
    });
  });

  await page.route("**/api/codingconductor/plan", async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
      },
      body: sseBody,
    });
  });

  await page.route(`**/api/codingconductor/steps/${planResponse.run_id}`, async (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as { steps: typeof latestSteps };
      latestSteps = payload.steps.map((step) => ({ ...step }));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ run_id: planResponse.run_id, steps: latestSteps }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run_id: planResponse.run_id, steps: latestSteps }),
    });
  });

  await page.route("**/api/codingconductor/export", async (route) => {
    const yaml = [
      "plan:",
      "  context: Modernize onboarding",
      "steps:",
      `  - title: ${latestSteps[0].title}`,
      "",
    ].join("
");
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "application/yaml",
        "content-disposition": "attachment; filename=prompts.yaml",
      },
      body: yaml,
    });
  });

  const baseURL = test.info().project.use.baseURL ?? "http://localhost:3000";
  await page.goto(baseURL);

  await page.setInputFiles('input[type="file"]', {
    name: 'blueprint.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Goals
- Deliver fast'),
  });
  await page.click('button:has-text("Ingest Blueprint")');
  await expect(page.getByText(/Ingestion complete/i)).toBeVisible();

  await page.click('button:has-text("Generate Plan")');
  await expect(page.getByText(/Plan generated successfully/i)).toBeVisible();

  const titleInput = page.locator('input[value="Confirm requirements"]');
  await titleInput.fill("Clarify requirements phase");

  const downloadPromise = page.waitForEvent("download");
  await page.click('button:has-text("YAML")');
  const download = await downloadPromise;
  const downloadPath = await download.path();
  if (!downloadPath) {
    throw new Error("Download path not available");
  }
  const content = await readFile(downloadPath, "utf-8");
  expect(content).toContain("steps:");
  expect(content).toContain("Clarify requirements phase");
});
