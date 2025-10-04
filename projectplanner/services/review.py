"""Quality evaluation helpers for prompt plans."""
from __future__ import annotations

from typing import List, Tuple

from projectplanner.logging_utils import get_logger
from projectplanner.models import PromptPlan, PromptStep

LOGGER = get_logger(__name__)


def evaluate_step(step: PromptStep) -> Tuple[float, List[str]]:
    """Return a rubric score and list of suggested improvements for a step."""

    deductions: List[str] = []
    if not step.inputs:
        deductions.append("Declare explicit inputs for the agent.")
    if not step.outputs:
        deductions.append("List explicit outputs/artifacts.")
    if not step.cited_artifacts:
        deductions.append("Reference at least one prior artifact.")
    if step.token_budget > 1200:
        deductions.append("Reduce token budget below 1200 to control cost.")
    if not any("criteria" in crit.lower() or "define" in crit.lower() for crit in step.acceptance_criteria):
        deductions.append("Tighten acceptance criteria with measurable statements.")
    if len(step.system_prompt.split()) > 250:
        deductions.append("Condense the system prompt to stay focused.")

    score = round(max(0.0, 1.0 - 0.15 * len(deductions)), 2)
    LOGGER.debug(
        "Evaluated step %s with score %.2f",
        step.id,
        score,
        extra={"event": "review.step", "payload": {"deduction_count": len(deductions)}},
    )
    return score, deductions


def summarize_strengths(plan: PromptPlan, steps: List[PromptStep]) -> List[str]:
    strengths = [
        f"Plan provides {len(plan.goals)} clear goals and {len(plan.milestones)} milestones",
        "Steps declare acceptance criteria for determinism",
    ]
    if any(step.cited_artifacts for step in steps):
        strengths.append("Steps cite prior artifacts to ensure continuity")
    LOGGER.debug(
        "Summarized strengths for plan with %s steps",
        len(steps),
        extra={"event": "review.strengths", "payload": {"strength_count": len(strengths)}},
    )
    return strengths


def summarize_concerns(steps: List[PromptStep]) -> List[str]:
    issues: List[str] = []
    for step in steps:
        if step.rubric_score is not None and step.rubric_score < 0.7:
            issues.append(f"{step.id} needs clarification: {step.suggested_edits}")
    if not issues:
        issues.append("No blocking issues detected; proceed to execution.")
    LOGGER.debug(
        "Summarized concerns for %s steps",
        len(steps),
        extra={"event": "review.concerns", "payload": {"concern_count": len(issues)}},
    )
    return issues