"""Planner agent orchestrating plan synthesis with GPT-5 support."""
from __future__ import annotations

import itertools
import json
import os
import re
from typing import List, Sequence

from projectplanner.agents.schemas import PlannerAgentInput, PlannerAgentOutput
from projectplanner.logging_utils import get_logger, log_prompt
from projectplanner.models import PromptPlan

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

LOGGER = get_logger(__name__)

DEFAULT_PLANNER_MODEL = os.getenv("PROJECTPLANNER_PLANNER_MODEL", "gpt-5")
MAX_CONTEXT_CHARS = 18000

PLANNER_SYSTEM_PROMPT = (
    "You are Agent 1, the project planning specialist in an AI orchestrated workflow. "
    "Using the coordinator's milestone objectives and the original research excerpts, produce a structured plan. "
    "Respond strictly with JSON containing the keys: context (string), goals (list[str]), assumptions (list[str]), "
    "non_goals (list[str]), and risks (list[str]). Do not include additional keys or prose."
)

SECTION_PATTERNS = {
    "goals": [r"^goal[s]?:", r"^objective[s]?:"],
    "assumptions": [r"assumption", r"precondition"],
    "non_goals": [r"out of scope", r"non-goal", r"exclude"],
    "risks": [r"risk", r"concern", r"challenge"],
    "milestones": [r"milestone", r"phase", r"stage"],
}


class PlannerAgent:
    """Extracts a structured plan from normalized research chunks."""

    def __init__(self) -> None:
        self._model = os.getenv("PROJECTPLANNER_PLANNER_MODEL", DEFAULT_PLANNER_MODEL)
        self._client = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key and OpenAI is not None:
            try:
                self._client = OpenAI(api_key=api_key)
            except Exception:  # pragma: no cover - initialization failure fallback
                LOGGER.warning(
                    "Failed to initialize OpenAI client for PlannerAgent; heuristics will be used.",
                    exc_info=True,
                    extra={"event": "agent.planner.init_failure"},
                )
                self._client = None

        if self._client:
            LOGGER.info(
                "Planner agent using OpenAI model %s",
                self._model,
                extra={"event": "agent.planner.ready"},
            )
        else:
            LOGGER.info(
                "Planner agent running in heuristic mode (OpenAI unavailable).",
                extra={"event": "agent.planner.heuristic_mode"},
            )

    def generate_plan(self, payload: PlannerAgentInput) -> PlannerAgentOutput:
        """Generate a PromptPlan using GPT-5 with deterministic fallback."""

        LOGGER.info(
            "Planner agent generating plan for run %s",
            payload.run_id,
            extra={
                "event": "agent.planner.start",
                "run_id": payload.run_id,
                "payload": {"objective_count": len(payload.objectives)},
            },
        )
        heuristic_plan = self._generate_with_heuristics(payload)
        LOGGER.debug(
            "Planner agent prepared heuristic baseline",
            extra={
                "event": "agent.planner.heuristic_baseline",
                "run_id": payload.run_id,
                "payload": {
                    "goal_count": len(heuristic_plan.goals),
                    "milestone_count": len(heuristic_plan.milestones),
                },
            },
        )
        if not self._client:
            LOGGER.info(
                "Planner agent returning heuristic plan (no OpenAI client).",
                extra={"event": "agent.planner.heuristic_only", "run_id": payload.run_id},
            )
            return PlannerAgentOutput(plan=heuristic_plan)

        try:
            plan = self._generate_with_gpt(payload, heuristic_plan)
            LOGGER.info(
                "Planner agent accepted GPT-generated plan.",
                extra={
                    "event": "agent.planner.gpt_success",
                    "run_id": payload.run_id,
                    "payload": {
                        "goal_count": len(plan.goals),
                        "milestone_count": len(plan.milestones),
                    },
                },
            )
            return PlannerAgentOutput(plan=plan)
        except Exception:  # pragma: no cover - fall back to heuristic result
            LOGGER.warning(
                "Planner GPT synthesis failed; returning heuristic plan.",
                exc_info=True,
                extra={"event": "agent.planner.gpt_failure", "run_id": payload.run_id},
            )
            return PlannerAgentOutput(plan=heuristic_plan)

    def _generate_with_gpt(self, payload: PlannerAgentInput, fallback: PromptPlan) -> PromptPlan:
        context = self._compress_chunks(payload.chunks)
        objectives_json = json.dumps(
            [
                {
                    "id": obj.id,
                    "order": obj.order,
                    "title": obj.title,
                    "objective": obj.objective,
                    "success_criteria": obj.success_criteria,
                    "dependencies": obj.dependencies,
                }
                for obj in sorted(payload.objectives, key=lambda item: item.order)
            ],
            indent=2,
        )
        user_prompt = (
            f"Run ID: {payload.run_id}\n"
            f"Target stack: backend={payload.target_stack.backend}, frontend={payload.target_stack.frontend}, database={payload.target_stack.db}\n"
            f"Planning style: {payload.style}\n"
            "Ordered coordinator objectives:\n"
            f"{objectives_json if payload.objectives else '[]'}\n"
            "Document excerpts (normalized and truncated):\n"
            '"""\n'
            f"{context}\n"
            '"""\n'
            "Return JSON with fields context, goals, assumptions, non_goals, risks."
        )

        log_prompt(
            agent="PlannerAgent",
            role="system",
            prompt=PLANNER_SYSTEM_PROMPT,
            run_id=payload.run_id,
            model=self._model,
            metadata={"style": payload.style},
        )
        log_prompt(
            agent="PlannerAgent",
            role="user",
            prompt=user_prompt,
            run_id=payload.run_id,
            model=self._model,
            metadata={
                "style": payload.style,
                "objective_count": len(payload.objectives),
                "chunk_count": len(payload.chunks),
            },
        )

        response = self._client.chat.completions.create(  # type: ignore[attr-defined]
            model=self._model,
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        if not response.choices:
            raise ValueError("Planner model returned no choices.")
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if not content:
            raise ValueError("Planner model returned empty content.")

        data = self._parse_plan_json(content)
        milestones = self._milestone_titles(payload) or fallback.milestones
        return PromptPlan(
            context=data.get("context") or fallback.context,
            goals=data.get("goals") or fallback.goals,
            assumptions=data.get("assumptions") or fallback.assumptions,
            non_goals=data.get("non_goals") or fallback.non_goals,
            risks=data.get("risks") or fallback.risks,
            milestones=milestones,
        )

    def _generate_with_heuristics(self, payload: PlannerAgentInput) -> PromptPlan:
        text = " \n".join(payload.chunks)
        milestones = self._milestone_titles(payload)
        if not milestones:
            milestones = self._extract_items(text, "milestones", fallback=self._default_milestones(payload.style))

        return PromptPlan(
            context=self._build_context(text, payload.target_stack),
            goals=self._extract_items(text, "goals", fallback=["Deliver a working prototype aligned with the research brief."]),
            assumptions=self._extract_items(text, "assumptions", fallback=["Stakeholders provide timely reviews."]),
            non_goals=self._extract_items(text, "non_goals", fallback=["Do not re-architect unrelated systems."]),
            risks=self._extract_items(text, "risks", fallback=["Timeline pressure may limit exploration."]),
            milestones=milestones,
        )

    def _parse_plan_json(self, raw: str) -> dict:
        cleaned = raw.strip()
        fenced = re.match(r"```(?:json)?\s*(.*)```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("Planner model response must be a JSON object.")

        return {
            "context": self._clean_text(data.get("context")),
            "goals": self._safe_list(data.get("goals")),
            "assumptions": self._safe_list(data.get("assumptions")),
            "non_goals": self._safe_list(data.get("non_goals")),
            "risks": self._safe_list(data.get("risks")),
        }

    def _clean_text(self, value) -> str:
        if not value:
            return ""
        text = str(value).strip()
        return re.sub(r"\s+", " ", text)

    def _safe_list(self, value) -> List[str]:
        if isinstance(value, list):
            items = [self._clean_text(item) for item in value]
        elif isinstance(value, str):
            items = [self._clean_text(value)]
        else:
            items = []
        return [item for item in items if item]

    def _milestone_titles(self, payload: PlannerAgentInput) -> List[str]:
        if payload.objectives:
            ordered = sorted(payload.objectives, key=lambda item: item.order)
            return [obj.title for obj in ordered]
        return []

    def _build_context(self, text: str, stack) -> str:
        sentences = self._top_sentences(text, 2)
        stack_summary = f"Target stack: backend {stack.backend}, frontend {stack.frontend}, database {stack.db}."
        context = " ".join(sentences) if sentences else "This project builds an application based on the supplied research document."
        return f"{context} {stack_summary}".strip()

    def _extract_items(self, text: str, section: str, *, fallback: List[str]) -> List[str]:
        patterns = SECTION_PATTERNS.get(section, [])
        matches: List[str] = []
        for line in text.split("\n"):
            normalized = line.strip()
            lower = normalized.lower()
            for pattern in patterns:
                if re.search(pattern, lower):
                    cleaned = re.sub(r"^[^:]*:", "", normalized).strip(" -\u0007\t")
                    if cleaned:
                        matches.append(cleaned)
                    break
        if not matches:
            matches = self._top_phrases(text, keywords=patterns, limit=3)
        return matches or fallback

    def _top_sentences(self, text: str, limit: int) -> List[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        return sentences[:limit]

    def _top_phrases(self, text: str, *, keywords: List[str], limit: int) -> List[str]:
        phrases: List[str] = []
        tokens = [line.strip(" -\u0007\t") for line in text.split("\n") if line.strip()]
        keyword_pattern = re.compile("|".join(keywords), re.IGNORECASE) if keywords else None
        for token in tokens:
            if keyword_pattern and keyword_pattern.search(token):
                phrases.append(token)
        if not phrases:
            phrases = list(itertools.islice(tokens, limit))
        return phrases[:limit]

    def _default_milestones(self, style: str) -> List[str]:
        base = [
            "Milestone 1: Confirm requirements and domain assumptions",
            "Milestone 2: Draft architecture and integration approach",
            "Milestone 3: Implement features iteratively with validation",
            "Milestone 4: Validate outcomes against risks and acceptance criteria",
        ]
        base.append("Milestone 5: Final review, polish, and delivery")
        if style == "creative":
            base[-1] = "Milestone 5: Showcase results and gather feedback"
        return base

    def _compress_chunks(self, chunks: Sequence[str], limit: int = MAX_CONTEXT_CHARS) -> str:
        joined = "\n\n".join(chunks)
        if len(joined) <= limit:
            return joined
        truncated = joined[:limit]
        cutoff = truncated.rfind("\n")
        if cutoff > limit * 0.6:
            return truncated[:cutoff]
        return truncated
