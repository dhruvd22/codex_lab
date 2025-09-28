"""Planning workflow orchestration."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterator, List, Tuple

from fastapi import HTTPException

from projectplanner.agents.decomposer_agent import DecomposerAgent
from projectplanner.agents.planner_agent import PlannerAgent
from projectplanner.agents.reviewer_agent import ReviewerAgent
from projectplanner.agents.schemas import (
    DecomposerAgentInput,
    PlannerAgentInput,
    ReviewerAgentInput,
)
from projectplanner.models import (
    AgentReport,
    ExportMetadata,
    ExportRequest,
    ExportResponse,
    PlanRequest,
    PlanResponse,
    PromptPlan,
    PromptStep,
)
from projectplanner.services.store import ProjectPlannerStore

PlanningEvent = Tuple[str, Dict[str, Any]]


def _serialize_report(report: AgentReport) -> Dict[str, Any]:
    """Convert the reviewer report into a JSON safe payload."""

    data = report.dict()
    data["generated_at"] = report.generated_at.isoformat()
    return data


def _planning_generator(
    payload: PlanRequest, *, store: ProjectPlannerStore
) -> Iterator[PlanningEvent]:
    """Yield planning lifecycle events and return the final plan."""

    if not store.run_exists(payload.run_id):
        raise HTTPException(status_code=404, detail="Run not found. Ingest a document first.")

    chunks = store.get_chunks(payload.run_id)
    if not chunks:
        raise HTTPException(status_code=400, detail="Run has no chunks to plan against.")

    text_chunks = [chunk.text for chunk in chunks]
    yield (
        "planner_started",
        {"run_id": payload.run_id, "chunk_count": len(text_chunks)},
    )

    planner_input = PlannerAgentInput(
        run_id=payload.run_id,
        chunks=text_chunks,
        target_stack=payload.target_stack,
        style=payload.style,
    )
    planner = PlannerAgent()
    plan_output = planner.generate_plan(planner_input)
    plan = plan_output.plan
    yield ("planner_completed", {"plan": plan.dict()})

    decomposer_input = DecomposerAgentInput(
        run_id=payload.run_id,
        plan=plan,
        target_stack=payload.target_stack,
    )
    decomposer = DecomposerAgent()
    steps_output = decomposer.decompose(decomposer_input)
    steps = steps_output.steps
    yield ("decomposer_completed", {"steps": [step.dict() for step in steps]})

    reviewer_input = ReviewerAgentInput(run_id=payload.run_id, plan=plan, steps=steps)
    reviewer = ReviewerAgent()
    review_output = reviewer.review(reviewer_input)
    reviewed_steps = review_output.steps
    report = review_output.report
    yield (
        "reviewer_completed",
        {
            "report": _serialize_report(report),
            "steps": [step.dict() for step in reviewed_steps],
        },
    )

    store.attach_plan_context(
        payload.run_id,
        target_stack=payload.target_stack.dict(),
        style=payload.style,
    )
    store.upsert_plan(payload.run_id, plan)
    store.upsert_steps(payload.run_id, reviewed_steps)
    store.upsert_report(payload.run_id, report)

    final_payload: Dict[str, Any] = {
        "run_id": payload.run_id,
        "plan": plan.dict(),
        "steps": [step.dict() for step in reviewed_steps],
        "report": _serialize_report(report),
    }
    yield ("final_plan", final_payload)
    return PlanResponse(plan=plan, steps=reviewed_steps, report=report)


async def run_planning_workflow(payload: PlanRequest, *, store: ProjectPlannerStore) -> PlanResponse:
    """Execute the planning workflow and return the final response."""

    generator = _planning_generator(payload, store=store)
    final_response: PlanResponse | None = None
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            final_response = stop.value
            break
    if final_response is None:
        raise RuntimeError("Planning did not produce a final response.")
    return final_response


def planning_event_stream(
    payload: PlanRequest, *, store: ProjectPlannerStore
) -> Iterator[PlanningEvent]:
    """Expose the planning lifecycle as an iterator for streaming."""

    return _planning_generator(payload, store=store)


async def export_prompts(payload: ExportRequest, *, store: ProjectPlannerStore) -> ExportResponse:
    plan = store.get_plan(payload.run_id)
    steps = store.get_steps(payload.run_id)
    report = store.get_report(payload.run_id)

    if not plan or not steps:
        raise HTTPException(status_code=404, detail="Plan or steps not found for run.")

    formatter = {
        "yaml": _to_yaml,
        "jsonl": _to_jsonl,
        "md": _to_markdown,
    }[payload.format]
    content = formatter(plan, steps, report)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename, content_type = {
        "yaml": (f"prompts-{payload.run_id}-{timestamp}.yaml", "application/yaml"),
        "jsonl": (f"prompts-{payload.run_id}-{timestamp}.jsonl", "application/json"),
        "md": (f"prompts-{payload.run_id}-{timestamp}.md", "text/markdown"),
    }[payload.format]

    metadata = ExportMetadata(
        filename=filename,
        content_type=content_type,
        generated_at=datetime.utcnow(),
    )
    export_response = ExportResponse(metadata=metadata, content=content)
    return export_response


def _to_yaml(plan: PromptPlan, steps: List[PromptStep], report: AgentReport | None) -> str:
    lines: List[str] = ["plan:", "  context: |"]
    lines.extend(f"    {line}" for line in plan.context.splitlines())
    for field in ("goals", "assumptions", "non_goals", "risks", "milestones"):
        lines.append(f"  {field}:")
        for item in getattr(plan, field):
            lines.append(f"    - {item}")
    lines.append("steps:")
    for step in steps:
        lines.append(f"  - id: {step.id}")
        lines.append(f"    title: {step.title}")
        lines.append("    system_prompt: |")
        for line in step.system_prompt.splitlines():
            lines.append(f"      {line}")
        lines.append("    user_prompt: |")
        for line in step.user_prompt.splitlines():
            lines.append(f"      {line}")
        lines.append("    expected_artifacts:")
        for artifact in step.expected_artifacts:
            lines.append(f"      - {artifact}")
        lines.append("    acceptance_criteria:")
        for criterion in step.acceptance_criteria:
            lines.append(f"      - {criterion}")
        lines.append("    inputs:")
        for item in step.inputs:
            lines.append(f"      - {item}")
        lines.append("    outputs:")
        for item in step.outputs:
            lines.append(f"      - {item}")
    if report:
        lines.append("report:")
        lines.append(f"  overall_score: {report.overall_score}")
        lines.append("  strengths:")
        for item in report.strengths:
            lines.append(f"    - {item}")
        lines.append("  concerns:")
        for item in report.concerns:
            lines.append(f"    - {item}")
    return "\n".join(lines)


def _to_jsonl(plan: PromptPlan, steps: List[PromptStep], report: AgentReport | None) -> str:
    bundle: List[dict] = [{"type": "plan", "payload": plan.dict()}]
    for step in steps:
        bundle.append({"type": "step", "payload": step.dict()})
    if report:
        bundle.append({"type": "report", "payload": report.dict()})
    return "\n".join(json.dumps(item) for item in bundle)


def _to_markdown(plan: PromptPlan, steps: List[PromptStep], report: AgentReport | None) -> str:
    lines = ["# Project Plan", "", f"**Context**: {plan.context}"]
    for heading in ("goals", "assumptions", "non_goals", "risks", "milestones"):
        section = getattr(plan, heading)
        if section:
            lines.append(f"## {heading.replace('_', ' ').title()}")
            for item in section:
                lines.append(f"- {item}")
            lines.append("")
    lines.append("## Steps")
    for index, step in enumerate(steps, start=1):
        lines.append(f"### Step {index}: {step.title}")
        lines.append("")
        lines.append("**System Prompt**")
        lines.extend(step.system_prompt.splitlines())
        lines.append("")
        lines.append("**User Prompt**")
        lines.extend(step.user_prompt.splitlines())
        lines.append("")
        if step.expected_artifacts:
            lines.append("**Expected Artifacts**")
            lines.extend(f"- {artifact}" for artifact in step.expected_artifacts)
            lines.append("")
        if step.acceptance_criteria:
            lines.append("**Acceptance Criteria**")
            lines.extend(f"- {criterion}" for criterion in step.acceptance_criteria)
            lines.append("")
    if report:
        lines.append("## Reviewer Report")
        lines.append(f"Overall score: {report.overall_score:.2f}")
        if report.strengths:
            lines.append("**Strengths**")
            lines.extend(f"- {item}" for item in report.strengths)
        if report.concerns:
            lines.append("**Concerns**")
            lines.extend(f"- {item}" for item in report.concerns)
    return "\n".join(lines)


