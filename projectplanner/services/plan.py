"""Planning workflow orchestration."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterator, List, Tuple

from fastapi import HTTPException

from projectplanner.agents.coordinator_agent import CoordinatorAgent
from projectplanner.agents.decomposer_agent import DecomposerAgent
from projectplanner.agents.planner_agent import PlannerAgent
from projectplanner.agents.reviewer_agent import ReviewerAgent
from projectplanner.agents.schemas import (
    CoordinatorAgentInput,
    DecomposerAgentInput,
    PlannerAgentInput,
    ReviewerAgentInput,
)
from projectplanner.logging_utils import get_logger
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


LOGGER = get_logger(__name__)


def _serialize_report(report: AgentReport) -> Dict[str, Any]:
    """Convert the reviewer report into a JSON safe payload."""

    data = report.dict()
    data["generated_at"] = report.generated_at.isoformat()
    return data


def _planning_generator(
    payload: PlanRequest, *, store: ProjectPlannerStore
) -> Iterator[PlanningEvent]:
    """Yield planning lifecycle events and return the final plan."""

    LOGGER.info(
        "Planning workflow initiated for run %s",
        payload.run_id,
        extra={
            "event": "planning.start",
            "run_id": payload.run_id,
            "payload": {"style": payload.style},
        },
    )
    if not store.run_exists(payload.run_id):
        LOGGER.warning(
            "Planning requested for unknown run %s",
            payload.run_id,
            extra={"event": "planning.missing_run", "run_id": payload.run_id},
        )
        raise HTTPException(status_code=404, detail="Run not found. Ingest a document first.")

    chunks = store.get_chunks(payload.run_id)
    if not chunks:
        LOGGER.warning(
            "Run %s has no chunks available for planning",
            payload.run_id,
            extra={"event": "planning.missing_chunks", "run_id": payload.run_id},
        )
        raise HTTPException(status_code=400, detail="Run has no chunks to plan against.")

    text_chunks = [chunk.text for chunk in chunks]
    coordinator_started = {"run_id": payload.run_id, "chunk_count": len(text_chunks)}
    LOGGER.info(
        "Coordinator stage starting (%s chunks)",
        len(text_chunks),
        extra={
            "event": "planning.coordinator.start",
            "run_id": payload.run_id,
            "payload": coordinator_started,
        },
    )
    yield ("coordinator_started", coordinator_started)

    coordinator_input = CoordinatorAgentInput(
        run_id=payload.run_id,
        chunks=text_chunks,
        target_stack=payload.target_stack,
        style=payload.style,
    )
    coordinator = CoordinatorAgent()
    coordinator_output = coordinator.synthesize_objectives(coordinator_input)
    objectives = sorted(
        coordinator_output.objectives,
        key=lambda objective: objective.order,
    )
    store.upsert_objectives(payload.run_id, objectives)
    coordinator_completed = {
        "run_id": payload.run_id,
        "objective_count": len(objectives),
        "objectives": [objective.dict() for objective in objectives],
    }
    LOGGER.info(
        "Coordinator produced %s objectives",
        len(objectives),
        extra={
            "event": "planning.coordinator.complete",
            "run_id": payload.run_id,
            "payload": coordinator_completed,
        },
    )
    yield ("coordinator_completed", coordinator_completed)

    planner_started = {
        "run_id": payload.run_id,
        "chunk_count": len(text_chunks),
        "objective_count": len(objectives),
    }
    LOGGER.info(
        "Planner stage starting",
        extra={
            "event": "planning.planner.start",
            "run_id": payload.run_id,
            "payload": planner_started,
        },
    )
    yield ("planner_started", planner_started)

    planner_input = PlannerAgentInput(
        run_id=payload.run_id,
        chunks=text_chunks,
        target_stack=payload.target_stack,
        style=payload.style,
        objectives=objectives,
    )
    planner = PlannerAgent()
    plan_output = planner.generate_plan(planner_input)
    plan = plan_output.plan
    planner_completed = {"plan": plan.dict()}
    LOGGER.info(
        "Planner completed",
        extra={
            "event": "planning.planner.complete",
            "run_id": payload.run_id,
            "payload": {"goal_count": len(plan.goals), "milestone_count": len(plan.milestones)},
        },
    )
    yield ("planner_completed", planner_completed)

    decomposer_input = DecomposerAgentInput(
        run_id=payload.run_id,
        plan=plan,
        target_stack=payload.target_stack,
        objectives=objectives,
    )
    decomposer = DecomposerAgent()
    steps_output = decomposer.decompose(decomposer_input)
    steps = steps_output.steps
    decomposer_completed = {"steps": [step.dict() for step in steps]}
    LOGGER.info(
        "Decomposer produced %s steps",
        len(steps),
        extra={
            "event": "planning.decomposer.complete",
            "run_id": payload.run_id,
            "payload": {"step_count": len(steps)},
        },
    )
    yield ("decomposer_completed", decomposer_completed)

    reviewer_input = ReviewerAgentInput(run_id=payload.run_id, plan=plan, steps=steps)
    reviewer = ReviewerAgent()
    review_output = reviewer.review(reviewer_input)
    reviewed_steps = review_output.steps
    report = review_output.report
    reviewer_completed = {
        "report": _serialize_report(report),
        "steps": [step.dict() for step in reviewed_steps],
    }
    LOGGER.info(
        "Reviewer completed with overall score %.2f",
        report.overall_score,
        extra={
            "event": "planning.reviewer.complete",
            "run_id": payload.run_id,
            "payload": {"overall_score": report.overall_score},
        },
    )
    yield ("reviewer_completed", reviewer_completed)

    store.attach_plan_context(
        payload.run_id,
        target_stack=payload.target_stack.dict(),
        style=payload.style,
    )
    store.upsert_plan(payload.run_id, plan)
    store.upsert_steps(payload.run_id, reviewed_steps)
    store.upsert_report(payload.run_id, report)
    LOGGER.debug(
        "Persisted plan artifacts for run %s",
        payload.run_id,
        extra={"event": "planning.persisted", "run_id": payload.run_id},
    )

    final_payload: Dict[str, Any] = {
        "run_id": payload.run_id,
        "plan": plan.dict(),
        "steps": [step.dict() for step in reviewed_steps],
        "report": _serialize_report(report),
        "objectives": [objective.dict() for objective in objectives],
    }
    LOGGER.info(
        "Planning workflow complete for run %s",
        payload.run_id,
        extra={
            "event": "planning.complete",
            "run_id": payload.run_id,
            "payload": {"step_count": len(reviewed_steps)},
        },
    )
    yield ("final_plan", final_payload)
    return PlanResponse(plan=plan, steps=reviewed_steps, report=report, objectives=objectives)


async def run_planning_workflow(payload: PlanRequest, *, store: ProjectPlannerStore) -> PlanResponse:
    """Execute the planning workflow and return the final response."""

    LOGGER.debug(
        "Running planning workflow coroutine for run %s",
        payload.run_id,
        extra={"event": "planning.run.execute", "run_id": payload.run_id, "payload": {"mode": "coroutine"}},
    )
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
    LOGGER.debug(
        "Planning coroutine completed for run %s",
        payload.run_id,
        extra={"event": "planning.run.complete", "run_id": payload.run_id, "payload": {"step_count": len(final_response.steps) if final_response else 0}},
    )
    return final_response


def planning_event_stream(
    payload: PlanRequest, *, store: ProjectPlannerStore
) -> Iterator[PlanningEvent]:
    """Expose the planning lifecycle as an iterator for streaming."""

    LOGGER.debug(
        "Creating planning event stream for run %s",
        payload.run_id,
        extra={"event": "planning.stream.create", "run_id": payload.run_id},
    )
    return _planning_generator(payload, store=store)


async def export_prompts(payload: ExportRequest, *, store: ProjectPlannerStore) -> ExportResponse:
    LOGGER.info(
        "Exporting prompts for run %s",
        payload.run_id,
        extra={
            "event": "planning.export.start",
            "run_id": payload.run_id,
            "payload": {"format": payload.format},
        },
    )
    plan = store.get_plan(payload.run_id)
    steps = store.get_steps(payload.run_id)
    report = store.get_report(payload.run_id)

    if not plan or not steps:
        LOGGER.warning(
            "Export requested but plan or steps missing for run %s",
            payload.run_id,
            extra={"event": "planning.export.missing", "run_id": payload.run_id},
        )
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
    LOGGER.info(
        "Prepared %s export for run %s",
        payload.format,
        payload.run_id,
        extra={
            "event": "planning.export.complete",
            "run_id": payload.run_id,
            "payload": {"filename": filename},
        },
    )
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


