"""AgentPlanner produces milestone-level execution prompts."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from projectplanner.agents._openai_helpers import (
    create_chat_completion,
    extract_choice_metadata,
    extract_message_content,
)
from projectplanner.logging_utils import get_logger, log_prompt
from projectplanner.orchestrator.config import (
    get_max_completion_tokens,
    get_prompt_model,
    get_temperature,
)
from projectplanner.orchestrator.models import (
    BlueprintSummary,
    GraphCoverageSnapshot,
    Milestone,
    MilestonePrompt,
    PromptBundle,
)

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


LOGGER = get_logger(__name__)

PROMPT_SYSTEM_PROMPT = (
    "You are AgentPlanner, an elite staff engineer guiding autonomous coding agents. "
    "For each milestone you must output JSON with keys: title (string), system_prompt (string), user_prompt (string), "
    "acceptance_criteria (list of strings), expected_artifacts (list of strings), references (list of strings). "
    "System prompts must be authoritative and set guardrails. User prompts should include concrete tasks, inputs, and acceptance gates."
)


class AgentPlanner:
    """Generates milestone execution prompts."""

    def __init__(self) -> None:
        self._model = get_prompt_model()
        self._temperature = get_temperature()
        self._max_tokens = get_max_completion_tokens()
        self._client = self._init_client()

    def _init_client(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or OpenAI is None:
            LOGGER.warning(
                "AgentPlanner running without OpenAI client; fallback heuristics active.",
                extra={"event": "orchestrator.agentplanner.no_client"},
            )
            return None
        try:
            client = OpenAI(api_key=api_key)
            LOGGER.info(
                "AgentPlanner OpenAI client initialized",
                extra={"event": "orchestrator.agentplanner.client_ready", "payload": {"model": self._model}},
            )
            return client
        except Exception:
            LOGGER.exception(
                "Failed to initialize OpenAI client; using heuristic mode.",
                extra={"event": "orchestrator.agentplanner.client_error"},
            )
            return None

    def generate_prompts(
        self,
        *,
        run_id: str,
        summary: BlueprintSummary,
        milestones: List[Milestone],
        graph_snapshot: GraphCoverageSnapshot,
    ) -> PromptBundle:
        """Generate sequential prompts for each milestone."""

        prompts: List[MilestonePrompt] = []
        for milestone in sorted(milestones, key=lambda item: item.milestone_id):
            previous_titles = [prompt.title for prompt in prompts]
            prompt = self._generate_for_milestone(
                run_id=run_id,
                milestone=milestone,
                summary=summary,
                previous_titles=previous_titles,
                graph_snapshot=graph_snapshot,
            )
            prompts.append(prompt)
        return PromptBundle(run_id=run_id, prompts=prompts)

    def _generate_for_milestone(
        self,
        *,
        run_id: str,
        milestone: Milestone,
        summary: BlueprintSummary,
        previous_titles: List[str],
        graph_snapshot: GraphCoverageSnapshot,
    ) -> MilestonePrompt:
        if not self._client:
            return self._heuristic_prompt(milestone, summary, previous_titles)

        payload = self._format_payload(milestone, summary, previous_titles, graph_snapshot)
        log_prompt(
            agent="AgentPlanner",
            role="system",
            prompt=PROMPT_SYSTEM_PROMPT,
            run_id=run_id,
            stage="request",
            model=self._model,
        )
        log_prompt(
            agent="AgentPlanner",
            role="user",
            prompt=payload,
            run_id=run_id,
            stage="request",
            model=self._model,
        )
        try:
            response = create_chat_completion(
                self._client,
                model=self._model,
                messages=[
                    {"role": "system", "content": PROMPT_SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            metadata = extract_choice_metadata(response)
            content = self._extract_content(response)
            log_prompt(
                agent="AgentPlanner",
                role="assistant",
                prompt=content,
                run_id=run_id,
                stage="response",
                model=metadata.get("model", self._model),
                metadata=metadata,
            )
            data = self._parse_json(content)
            return MilestonePrompt(
                milestone_id=milestone.milestone_id,
                title=data.get("title", f"Milestone {milestone.milestone_id}"),
                system_prompt=data.get("system_prompt", ""),
                user_prompt=data.get("user_prompt", ""),
                acceptance_criteria=self._ensure_list(data.get("acceptance_criteria")),
                expected_artifacts=self._ensure_list(data.get("expected_artifacts")),
                references=self._ensure_list(data.get("references")),
            )
        except Exception:
            LOGGER.exception(
                "Prompt generation failed for milestone %s; using heuristic prompt.",
                milestone.milestone_id,
                extra={
                    "event": "orchestrator.agentplanner.prompt_error",
                    "run_id": run_id,
                    "payload": {"milestone_id": milestone.milestone_id},
                },
            )
            return self._heuristic_prompt(milestone, summary, previous_titles)

    @staticmethod
    def _format_payload(
        milestone: Milestone,
        summary: BlueprintSummary,
        previous_titles: List[str],
        graph_snapshot: GraphCoverageSnapshot,
    ) -> str:
        previous_section = "\n".join(f"- {title}" for title in previous_titles) or "- None yet"
        uncovered_section = (
            "\n".join(f"- {node}" for node in graph_snapshot.uncovered_nodes)
            if graph_snapshot.uncovered_nodes
            else "- All nodes covered so far"
        )
        covered_section = (
            "\n".join(f"- {node}" for node in graph_snapshot.covered_nodes)
            if graph_snapshot.covered_nodes
            else "- Pending coverage"
        )
        return (
            f"Application summary:\n{summary.summary}\n\n"
            f"Milestone {milestone.milestone_id} details:\n{milestone.details}\n\n"
            f"Milestone context:\n{milestone.context or '(context not provided)'}\n\n"
            f"Prior milestones delivered:\n{previous_section}\n\n"
            f"Graph coverage reference:\nCovered nodes:\n{covered_section}\nRemaining nodes of concern:\n{uncovered_section}\n\n"
            f"Generate a JSON object with the required fields."
        )

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        candidate = raw.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            if candidate.lower().startswith("json"):
                candidate = candidate[4:]
        candidate = candidate.strip()
        return json.loads(candidate) if candidate else {}

    @staticmethod
    def _ensure_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _extract_content(response: Any) -> str:
        if hasattr(response, "choices") and response.choices:
            return extract_message_content(response.choices[0].message)
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message")
                return extract_message_content(message)
        return ""

    def _heuristic_prompt(
        self,
        milestone: Milestone,
        summary: BlueprintSummary,
        previous_titles: List[str],
    ) -> MilestonePrompt:
        title = milestone.details.split(".")[0].strip() or f"Milestone {milestone.milestone_id}"
        system_prompt = (
            "You are an autonomous senior engineer executing milestone goals with discipline. "
            "Follow the user instructions precisely, produce code when necessary, and maintain audit-ready notes."
        )
        user_prompt = (
            f"Goal: {milestone.details}\n\n"\
            f"Context: {milestone.context or summary.summary}\n\n"\
            "Deliver end-to-end functionality for this milestone, validate against acceptance criteria, "
            "and note any assumptions for the next milestone."
        )
        acceptance = [
            "Implements milestone functionality as described.",
            "Adds thorough automated tests covering primary flows.",
            "Documents decisions and follow-ups for subsequent milestones.",
        ]
        artifacts = [
            "Source code diff",
            "Test report",
            "Decision log",
        ]
        LOGGER.info(
            "Heuristic prompt emitted",
            extra={
                "event": "orchestrator.agentplanner.heuristic_prompt",
                "payload": {"milestone_id": milestone.milestone_id, "previous": previous_titles},
            },
        )
        return MilestonePrompt(
            milestone_id=milestone.milestone_id,
            title=title,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            acceptance_criteria=acceptance,
            expected_artifacts=artifacts,
            references=[],
        )


__all__ = ["AgentPlanner"]
