"""Reviewer agent responsible for quality control."""
from __future__ import annotations

from datetime import datetime
from typing import List

from projectplanner.agents.schemas import ReviewerAgentInput, ReviewerAgentOutput
from projectplanner.logging_utils import get_logger
from projectplanner.models import AgentReport, PromptStep, StepFeedback
from projectplanner.services import review as review_service

LOGGER = get_logger(__name__)


class ReviewerAgent:
    """Applies a deterministic rubric to each prompt step."""

    def review(self, payload: ReviewerAgentInput) -> ReviewerAgentOutput:
        reviewed_steps: List[PromptStep] = []
        feedback: List[StepFeedback] = []

        LOGGER.info(
            "Reviewer agent scoring %s steps for run %s",
            len(payload.steps),
            payload.run_id,
            extra={"event": "agent.reviewer.start", "run_id": payload.run_id, "payload": {"step_count": len(payload.steps)}},
        )
        for step in payload.steps:
            score, suggestions = review_service.evaluate_step(step)
            step.rubric_score = score
            step.suggested_edits = "; ".join(suggestions) if suggestions else None
            reviewed_steps.append(step)
            feedback.append(
                StepFeedback(
                    step_id=step.id,
                    rubric_score=score,
                    notes=step.suggested_edits or "Meets rubric expectations.",
                )
            )

        overall = round(
            sum(step.rubric_score or 0.0 for step in reviewed_steps) / max(len(reviewed_steps), 1), 2
        )
        strengths = review_service.summarize_strengths(payload.plan, reviewed_steps)
        concerns = review_service.summarize_concerns(reviewed_steps)
        report = AgentReport(
            run_id=payload.run_id,
            generated_at=datetime.utcnow(),
            overall_score=overall,
            strengths=strengths,
            concerns=concerns,
            step_feedback=feedback,
        )
        LOGGER.info(
            "Reviewer agent completed for run %s with score %.2f",
            payload.run_id,
            overall,
            extra={"event": "agent.reviewer.complete", "run_id": payload.run_id, "payload": {"overall_score": overall}},
        )
        return ReviewerAgentOutput(steps=reviewed_steps, report=report)
