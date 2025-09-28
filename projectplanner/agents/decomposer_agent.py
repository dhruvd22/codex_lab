"""Decomposer agent converts a plan into executable prompts."""
from __future__ import annotations

from typing import List

from projectplanner.agents.schemas import DecomposerAgentInput, DecomposerAgentOutput
from projectplanner.models import PromptStep


class DecomposerAgent:
    """Transforms a high-level plan into sequenced PromptStep entries."""

    def decompose(self, payload: DecomposerAgentInput) -> DecomposerAgentOutput:
        steps: List[PromptStep] = []
        for index, milestone in enumerate(payload.plan.milestones):
            step_id = f"step-{index+1:03d}"
            system_prompt = self._build_system_prompt(payload, milestone)
            user_prompt = self._build_user_prompt(payload, milestone, index)
            expected_artifacts = self._infer_artifacts(milestone, index)
            acceptance_criteria = self._build_acceptance_criteria(payload, milestone)
            cited = self._cited_artifacts(index)

            steps.append(
                PromptStep(
                    id=step_id,
                    title=milestone,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    expected_artifacts=expected_artifacts,
                    tools=["editor", "terminal", "git"],
                    acceptance_criteria=acceptance_criteria,
                    inputs=["ingested_research", "project_plan"],
                    outputs=[artifact.replace("Create ", "").lower() for artifact in expected_artifacts],
                    token_budget=900,
                    cited_artifacts=cited,
                )
            )
        return DecomposerAgentOutput(steps=steps)

    def _build_system_prompt(self, payload: DecomposerAgentInput, milestone: str) -> str:
        return (
            "You are a focused senior engineer. Work step-by-step, keep responses short, "
            f"and align decisions with the target stack ({payload.target_stack.backend}, "
            f"{payload.target_stack.frontend}, {payload.target_stack.db})."
        )

    def _build_user_prompt(self, payload: DecomposerAgentInput, milestone: str, index: int) -> str:
        context_lines = [
            f"Milestone objective: {milestone}",
            f"Key goals: {', '.join(payload.plan.goals[:3])}",
            f"Known risks: {', '.join(payload.plan.risks[:2])}",
        ]
        if index > 0:
            context_lines.append("Reference outputs from prior steps as needed.")
        return "\n".join(context_lines)

    def _infer_artifacts(self, milestone: str, index: int) -> List[str]:
        lower = milestone.lower()
        if "research" in lower or "requirement" in lower:
            return ["Create clarified requirements doc"]
        if "architecture" in lower or "design" in lower:
            return ["Create architecture overview", "Create API design outline"]
        if "implement" in lower or "build" in lower:
            return ["Create implementation prompts", "Create test strategy"]
        if "review" in lower or "deliver" in lower:
            return ["Create delivery checklist", "Create final summary"]
        # fallback by index
        fallbacks = [
            ["Create discovery notes"],
            ["Create architecture outline"],
            ["Create development playbook"],
            ["Create validation report"],
        ]
        return fallbacks[min(index, len(fallbacks) - 1)]

    def _build_acceptance_criteria(self, payload: DecomposerAgentInput, milestone: str) -> List[str]:
        return [
            f"Directly addresses milestone: {milestone}",
            "States required inputs and produced artifacts",
            "Uses stable, reusable artifact names",
            "Fits within assigned token budget",
        ]

    def _cited_artifacts(self, index: int) -> List[str]:
        if index == 0:
            return ["research-brief"]
        if index == 1:
            return ["research-brief", "step-001:deliverable"]
        return ["research-brief", f"step-{index:03d}:deliverable"]