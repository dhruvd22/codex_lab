"""Decomposer agent converts coordinator milestones into executable prompts."""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Sequence

from projectplanner.agents.schemas import DecomposerAgentInput, DecomposerAgentOutput
from projectplanner.logging_utils import get_logger, log_prompt
from projectplanner.models import MilestoneObjective, PromptStep

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

LOGGER = get_logger(__name__)

DEFAULT_DECOMPOSER_MODEL = os.getenv("PROJECTPLANNER_DECOMPOSER_MODEL", "gpt-5")
MAX_CONTEXT_CHARS = 14000

DECOMPOSER_SYSTEM_PROMPT = (
    "You are Agent 2, the senior engineering lead preparing execution-ready prompts for an autonomous coding agent. "
    "Use the provided milestone objective, project context, and prior milestone progress to craft instructions. "
    "Respond strictly with JSON containing keys: system_prompt (string), user_prompt (string), expected_artifacts (list[str]), "
    "acceptance_criteria (list[str]), inputs (list[str]), outputs (list[str]), tools (list[str]), token_budget (int)."
)


class DecomposerAgent:
    """Transforms high-level milestones into sequenced PromptStep entries."""

    def __init__(self) -> None:
        self._model = os.getenv("PROJECTPLANNER_DECOMPOSER_MODEL", DEFAULT_DECOMPOSER_MODEL)
        self._client = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key and OpenAI is not None:
            try:
                self._client = OpenAI(api_key=api_key)
            except Exception:  # pragma: no cover - initialization failure fallback
                LOGGER.warning(
                    "Failed to initialize OpenAI client for DecomposerAgent; heuristics will be used.",
                    exc_info=True,
                    extra={"event": "agent.decomposer.init_failure"},
                )
                self._client = None

        if self._client:
            LOGGER.info(
                "Decomposer agent using OpenAI model %s",
                self._model,
                extra={"event": "agent.decomposer.ready"},
            )
        else:
            LOGGER.info(
                "Decomposer agent running in heuristic mode (OpenAI unavailable).",
                extra={"event": "agent.decomposer.heuristic_mode"},
            )

    def decompose(self, payload: DecomposerAgentInput) -> DecomposerAgentOutput:
        steps: List[PromptStep] = []
        previous_summaries: List[str] = []
        total = len(payload.plan.milestones)
        LOGGER.info(
            "Decomposer agent generating %s steps for run %s",
            total,
            payload.run_id,
            extra={
                "event": "agent.decomposer.start",
                "run_id": payload.run_id,
                "payload": {"milestone_count": total},
            },
        )
        using_gpt = bool(self._client)
        if not using_gpt:
            LOGGER.info(
                "Decomposer agent operating with heuristic fallbacks for all milestones.",
                extra={"event": "agent.decomposer.heuristic_mode_run", "run_id": payload.run_id},
            )
        for index, milestone_title in enumerate(payload.plan.milestones):
            step_id = f"step-{index + 1:03d}"
            objective = self._find_objective(index, milestone_title, payload.objectives)
            fallback_step = self._build_fallback_step(payload, index, milestone_title, objective, step_id)
            step = fallback_step
            LOGGER.debug(
                "Preparing step %s for milestone '%s'",
                step_id,
                milestone_title,
                extra={
                    "event": "agent.decomposer.step_start",
                    "run_id": payload.run_id,
                    "payload": {"step_id": step_id, "index": index},
                },
            )
            if self._client:
                try:
                    step = self._generate_step_with_gpt(
                        payload=payload,
                        index=index,
                        milestone_title=milestone_title,
                        objective=objective,
                        step_id=step_id,
                        previous_summaries=previous_summaries,
                        total=total,
                        fallback=fallback_step,
                    )
                    LOGGER.info(
                        "Decomposer agent accepted GPT output for %s",
                        step_id,
                        extra={
                            "event": "agent.decomposer.gpt_success",
                            "run_id": payload.run_id,
                            "payload": {"step_id": step_id},
                        },
                    )
                except Exception:  # pragma: no cover - rely on fallback
                    LOGGER.warning(
                        "Decomposer GPT synthesis failed for milestone '%s'; reverting to heuristic prompts.",
                        milestone_title,
                        exc_info=True,
                        extra={
                            "event": "agent.decomposer.gpt_failure",
                            "run_id": payload.run_id,
                            "payload": {"step_id": step_id},
                        },
                    )
                    step = fallback_step
            else:
                LOGGER.debug(
                    "Decomposer agent using heuristic prompt for %s",
                    step_id,
                    extra={
                        "event": "agent.decomposer.step_heuristic",
                        "run_id": payload.run_id,
                        "payload": {"step_id": step_id},
                    },
                )
            steps.append(step)
            previous_summaries.append(self._summarize_step(step))
        LOGGER.info(
            "Decomposer agent completed run %s with %s steps.",
            payload.run_id,
            len(steps),
            extra={
                "event": "agent.decomposer.complete",
                "run_id": payload.run_id,
                "payload": {"step_count": len(steps)},
            },
        )
        return DecomposerAgentOutput(steps=steps)

    def _generate_step_with_gpt(
        self,
        *,
        payload: DecomposerAgentInput,
        index: int,
        milestone_title: str,
        objective: Optional[MilestoneObjective],
        step_id: str,
        previous_summaries: Sequence[str],
        total: int,
        fallback: PromptStep,
    ) -> PromptStep:
        project_context = self._compress_context(payload)
        milestone_payload = {
            "order": index,
            "title": milestone_title,
            "objective": getattr(objective, "objective", milestone_title),
            "success_criteria": getattr(objective, "success_criteria", []),
            "dependencies": getattr(objective, "dependencies", []),
        }
        snapshot = {
            "plan_context": payload.plan.context,
            "goals": payload.plan.goals,
            "assumptions": payload.plan.assumptions,
            "risks": payload.plan.risks,
        }
        user_prompt = (
            f"Milestone {index + 1} of {total}: {milestone_title}\n"
            f"Milestone objective: {milestone_payload['objective']}\n"
            f"Success criteria: {json.dumps(milestone_payload['success_criteria'])}\n"
            f"Dependencies: {json.dumps(milestone_payload['dependencies'])}\n"
            f"Prior milestone status: {json.dumps(list(previous_summaries)) if previous_summaries else '[]'}\n"
            "Project snapshot:\n"
            f"{json.dumps(snapshot, indent=2)}\n"
            "Relevant context (truncated):\n"
            '"""\n'
            f"{project_context}\n"
            '"""\n'
            "Return the JSON structure described in the system instructions. Reference prior milestones when useful and respect the target stack."
        )

        log_prompt(
            agent="DecomposerAgent",
            role="system",
            prompt=DECOMPOSER_SYSTEM_PROMPT,
            run_id=payload.run_id,
            model=self._model,
            metadata={"milestone": milestone_title, "index": index, "total": total},
        )
        log_prompt(
            agent="DecomposerAgent",
            role="user",
            prompt=user_prompt,
            run_id=payload.run_id,
            model=self._model,
            metadata={
                "milestone": milestone_title,
                "index": index,
                "total": total,
                "previous_summaries": len(previous_summaries),
            },
        )

        response = self._client.chat.completions.create(  # type: ignore[attr-defined]
            model=self._model,
            messages=[
                {"role": "system", "content": DECOMPOSER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.25,
            max_completion_tokens=1100,
        )
        if not response.choices:
            raise ValueError("Decomposer model returned no choices.")
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if not content:
            raise ValueError("Decomposer model returned empty content.")

        data = self._parse_step_json(content)
        return PromptStep(
            id=step_id,
            title=milestone_title,
            system_prompt=data.get("system_prompt") or fallback.system_prompt,
            user_prompt=data.get("user_prompt") or fallback.user_prompt,
            expected_artifacts=data.get("expected_artifacts") or fallback.expected_artifacts,
            tools=data.get("tools") or fallback.tools,
            acceptance_criteria=data.get("acceptance_criteria") or fallback.acceptance_criteria,
            inputs=data.get("inputs") or fallback.inputs,
            outputs=data.get("outputs") or fallback.outputs,
            token_budget=data.get("token_budget") or fallback.token_budget,
            cited_artifacts=fallback.cited_artifacts,
        )

    def _build_fallback_step(
        self,
        payload: DecomposerAgentInput,
        index: int,
        milestone_title: str,
        objective: Optional[MilestoneObjective],
        step_id: str,
    ) -> PromptStep:
        system_prompt = self._build_system_prompt(payload, objective)
        user_prompt = self._build_user_prompt(payload, milestone_title, objective, index)
        expected_artifacts = self._infer_artifacts(milestone_title, objective, index)
        acceptance_criteria = self._build_acceptance_criteria(payload, milestone_title, objective)
        tools = ["editor", "terminal", "git"]
        inputs = self._fallback_inputs(index)
        outputs = self._fallback_outputs(expected_artifacts)
        return PromptStep(
            id=step_id,
            title=milestone_title,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expected_artifacts=expected_artifacts,
            tools=tools,
            acceptance_criteria=acceptance_criteria,
            inputs=inputs,
            outputs=outputs,
            token_budget=900,
            cited_artifacts=self._cited_artifacts(index),
        )

    def _parse_step_json(self, raw: str) -> dict:
        cleaned = raw.strip()
        fenced = re.match(r"```(?:json)?\s*(.*)```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("Decomposer model response must be a JSON object.")

        def _norm_list(value, fallback: Optional[List[str]] = None) -> List[str]:
            if isinstance(value, list):
                items = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, str):
                items = [value.strip()] if value.strip() else []
            else:
                items = []
            return items or list(fallback or [])

        token_budget = data.get("token_budget")
        try:
            token_budget_int = int(token_budget) if token_budget is not None else None
        except (TypeError, ValueError):
            token_budget_int = None

        return {
            "system_prompt": str(data.get("system_prompt") or "").strip(),
            "user_prompt": str(data.get("user_prompt") or "").strip(),
            "expected_artifacts": _norm_list(data.get("expected_artifacts")),
            "acceptance_criteria": _norm_list(data.get("acceptance_criteria")),
            "inputs": _norm_list(data.get("inputs"), ["ingested_research", "project_plan"]),
            "outputs": _norm_list(data.get("outputs")),
            "tools": _norm_list(data.get("tools"), ["editor", "terminal", "git"]),
            "token_budget": token_budget_int,
        }

    def _find_objective(
        self, index: int, milestone_title: str, objectives: Sequence[MilestoneObjective]
    ) -> Optional[MilestoneObjective]:
        if not objectives:
            return None
        for objective in objectives:
            if objective.order == index:
                return objective
        title_lower = milestone_title.lower()
        for objective in objectives:
            if objective.title.lower() == title_lower:
                return objective
        return None

    def _summarize_step(self, step: PromptStep) -> str:
        artifacts = ", ".join(step.expected_artifacts)
        return f"{step.id}: {step.title} -> {artifacts}"

    def _build_system_prompt(
        self, payload: DecomposerAgentInput, objective: Optional[MilestoneObjective]
    ) -> str:
        stack = payload.target_stack
        focus = objective.objective if objective else "Execute the milestone objective precisely."
        return (
            "You are a focused senior engineer. Work step-by-step, keep responses concise, "
            f"and align decisions with {stack.backend}, {stack.frontend}, {stack.db}. "
            f"Maintain source control hygiene and articulate assumptions. Focus on: {focus}"
        )

    def _build_user_prompt(
        self,
        payload: DecomposerAgentInput,
        milestone: str,
        objective: Optional[MilestoneObjective],
        index: int,
    ) -> str:
        context_lines = [
            f"Milestone objective: {objective.objective if objective else milestone}",
            f"Key goals: {', '.join(payload.plan.goals[:3]) if payload.plan.goals else 'Align with primary goals'}",
            f"Known risks: {', '.join(payload.plan.risks[:2]) if payload.plan.risks else 'Mitigate documented risks'}",
        ]
        if objective and objective.success_criteria:
            context_lines.append(f"Success criteria: {', '.join(objective.success_criteria)}")
        if objective and objective.dependencies:
            context_lines.append(f"Dependencies: {', '.join(objective.dependencies)} must be satisfied first.")
        if index > 0:
            completed = "; ".join(payload.plan.milestones[:index])
            context_lines.append(f"Completed milestones so far: {completed}. Reference their deliverables as inputs.")
        context_lines.append("Document blockers immediately and capture new assumptions explicitly.")
        return "\n".join(context_lines)

    def _infer_artifacts(
        self, milestone: str, objective: Optional[MilestoneObjective], index: int
    ) -> List[str]:
        if objective and objective.success_criteria:
            return [f"Create artifact covering: {criteria}" for criteria in objective.success_criteria]
        lower = milestone.lower()
        if "research" in lower or "requirement" in lower:
            return ["Create clarified requirements doc"]
        if "architecture" in lower or "design" in lower:
            return ["Create architecture overview", "Create API design outline"]
        if "implement" in lower or "build" in lower or "develop" in lower:
            return ["Create implementation prompts", "Create test strategy"]
        if "review" in lower or "deliver" in lower or "launch" in lower:
            return ["Create delivery checklist", "Create final summary"]
        fallbacks = [
            ["Create discovery notes"],
            ["Create architecture outline"],
            ["Create development playbook"],
            ["Create validation report"],
        ]
        return fallbacks[min(index, len(fallbacks) - 1)]

    def _build_acceptance_criteria(
        self, payload: DecomposerAgentInput, milestone: str, objective: Optional[MilestoneObjective]
    ) -> List[str]:
        criteria = [
            f"Directly addresses milestone: {milestone}",
            "States required inputs and produced artifacts",
            "Uses stable, reusable artifact names",
            "Fits within assigned token budget",
        ]
        if objective and objective.success_criteria:
            criteria.extend(objective.success_criteria)
        return list(dict.fromkeys(criteria))

    def _fallback_inputs(self, index: int) -> List[str]:
        inputs = ["ingested_research", "project_plan"]
        if index > 0:
            inputs.append(f"step-{index:03d}:deliverables")
        return inputs

    def _fallback_outputs(self, expected_artifacts: List[str]) -> List[str]:
        outputs: List[str] = []
        for idx, artifact in enumerate(expected_artifacts, start=1):
            slug = re.sub(r"[^a-z0-9]+", "-", artifact.lower()).strip("-")
            outputs.append(slug or f"deliverable-{idx:02d}")
        return outputs

    def _cited_artifacts(self, index: int) -> List[str]:
        if index == 0:
            return ["research-brief"]
        if index == 1:
            return ["research-brief", "step-001:deliverable"]
        return ["research-brief", f"step-{index:03d}:deliverable"]

    def _compress_context(self, payload: DecomposerAgentInput, limit: int = MAX_CONTEXT_CHARS) -> str:
        sections = [payload.plan.context]
        if payload.plan.goals:
            sections.append("Goals: " + "; ".join(payload.plan.goals[:5]))
        if payload.plan.assumptions:
            sections.append("Assumptions: " + "; ".join(payload.plan.assumptions[:5]))
        if payload.plan.risks:
            sections.append("Risks: " + "; ".join(payload.plan.risks[:5]))
        combined = "\n\n".join(section for section in sections if section)
        if len(combined) <= limit:
            return combined
        truncated = combined[:limit]
        cutoff = truncated.rfind("\n")
        if cutoff > limit * 0.6:
            return truncated[:cutoff]
        return truncated
