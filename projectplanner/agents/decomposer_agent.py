"""Decomposer agent converts coordinator milestones into executable prompts."""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Sequence

from projectplanner.agents.schemas import DecomposerAgentInput, DecomposerAgentOutput
from projectplanner.agents._openai_helpers import (
    create_chat_completion,
    extract_choice_metadata,
    extract_message_content,
)
from projectplanner.logging_utils import get_logger, log_prompt
from projectplanner.models import MilestoneObjective, PromptStep
from projectplanner.config import MAX_COMPLETION_TOKENS

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
                except Exception as error:  # pragma: no cover - rely on fallback
                    LOGGER.warning(
                        "Decomposer GPT synthesis failed for milestone '%s'; reverting to heuristic prompts. (%s)",
                        milestone_title,
                        error,
                        extra={
                            "event": "agent.decomposer.gpt_failure",
                            "run_id": payload.run_id,
                            "payload": {"step_id": step_id, "error": str(error), "error_type": type(error).__name__},
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
        prior_summaries = list(previous_summaries)

        attempts: List[dict[str, int | None]] = [
            {
                "context_limit": MAX_CONTEXT_CHARS,
                "summary_limit": None,
                "max_tokens": MAX_COMPLETION_TOKENS,
            }
        ]
        trimmed_context_limit = max(int(MAX_CONTEXT_CHARS * 0.75), 4000)
        boosted_tokens = min(MAX_COMPLETION_TOKENS + 1024, int(MAX_COMPLETION_TOKENS * 3 // 2))
        if boosted_tokens <= MAX_COMPLETION_TOKENS:
            boosted_tokens = MAX_COMPLETION_TOKENS
        attempts.append(
            {
                "context_limit": trimmed_context_limit,
                "summary_limit": 3,
                "max_tokens": boosted_tokens,
            }
        )

        log_prompt(
            agent="DecomposerAgent",
            role="system",
            prompt=DECOMPOSER_SYSTEM_PROMPT,
            run_id=payload.run_id,
            model=self._model,
            metadata={"milestone": milestone_title, "index": index, "total": total},
        )

        for attempt_index, attempt in enumerate(attempts):
            context_limit = int(attempt["context_limit"] or MAX_CONTEXT_CHARS)
            summary_limit = attempt["summary_limit"]
            max_tokens = int(attempt["max_tokens"] or MAX_COMPLETION_TOKENS)
            attempt_summaries = prior_summaries if summary_limit is None else prior_summaries[-summary_limit:]
            project_context = self._compress_context(payload, limit=context_limit)
            prior_status = json.dumps(list(attempt_summaries)) if attempt_summaries else "[]"
            user_prompt = (
                f"Milestone {index + 1} of {total}: {milestone_title}\n"
                f"Milestone objective: {milestone_payload['objective']}\n"
                f"Success criteria: {json.dumps(milestone_payload['success_criteria'])}\n"
                f"Dependencies: {json.dumps(milestone_payload['dependencies'])}\n"
                f"Prior milestone status: {prior_status}\n"
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
                role="user",
                prompt=user_prompt,
                run_id=payload.run_id,
                model=self._model,
                metadata={
                    "milestone": milestone_title,
                    "index": index,
                    "total": total,
                    "previous_summaries": len(attempt_summaries),
                    "attempt": attempt_index + 1,
                    "context_limit": context_limit,
                    "max_tokens": max_tokens,
                },
            )

            response = create_chat_completion(
                self._client,
                model=self._model,
                messages=[
                    {"role": "system", "content": DECOMPOSER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                max_tokens=max_tokens,
            )
            response_metadata = extract_choice_metadata(response)
            response_metadata.update(
                {
                    "milestone": milestone_title,
                    "index": index,
                    "total": total,
                    "attempt": attempt_index + 1,
                    "max_tokens": max_tokens,
                    "context_limit": context_limit,
                    "previous_summaries": len(attempt_summaries),
                }
            )
            if not response.choices:
                response_metadata["reason"] = "no_choices"
                log_prompt(
                    agent="DecomposerAgent",
                    role="assistant",
                    prompt="",
                    run_id=payload.run_id,
                    stage="response",
                    model=self._model,
                    metadata=response_metadata,
                )
                raise ValueError("Decomposer model returned no choices.")
            message = response.choices[0].message
            content = extract_message_content(message)
            response_metadata["has_content"] = bool(content)
            if message is not None:
                response_metadata.setdefault("message", message)
            log_prompt(
                agent="DecomposerAgent",
                role="assistant",
                prompt=content or "",
                run_id=payload.run_id,
                stage="response",
                model=self._model,
                metadata=response_metadata,
            )
            if content:
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
            details = []
            finish_reason = response_metadata.get("finish_reason")
            if finish_reason and attempt_index < len(attempts) - 1:
                next_attempt = attempts[attempt_index + 1]
                LOGGER.warning(
                    "Decomposer response truncated; retrying with adjusted context (attempt %s).",
                    attempt_index + 2,
                    extra={
                        "event": "agent.decomposer.retry_length",
                        "run_id": payload.run_id,
                        "payload": {
                            "step_id": step_id,
                            "attempt": attempt_index + 1,
                            "context_limit": context_limit,
                            "max_tokens": max_tokens,
                            "next_context_limit": int(next_attempt["context_limit"] or context_limit),
                            "next_max_tokens": int(next_attempt["max_tokens"] or max_tokens),
                            "finish_reason": finish_reason,
                        },
                    },
                )
                continue
            if finish_reason:
                details.append(f"finish_reason={finish_reason}")
            if response_metadata.get("refusal"):
                details.append("refusal")
            response_id = response_metadata.get("response_id")
            if response_id:
                details.append(f"id={response_id}")
            suffix = f" ({', '.join(details)})" if details else ""
            raise ValueError(f"Decomposer model returned empty content{suffix}.")

        raise ValueError("Decomposer model returned empty content (all retry attempts exhausted).")


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
