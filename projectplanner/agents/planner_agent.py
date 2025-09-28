"""Planner agent heuristics."""
from __future__ import annotations

import itertools
import re
from typing import List

from projectplanner.agents.schemas import PlannerAgentInput, PlannerAgentOutput
from projectplanner.models import PromptPlan

SECTION_PATTERNS = {
    "goals": [r"^goal[s]?:", r"^objective[s]?:"],
    "assumptions": [r"assumption", r"precondition"],
    "non_goals": [r"out of scope", r"non-goal", r"exclude"],
    "risks": [r"risk", r"concern", r"challenge"],
    "milestones": [r"milestone", r"phase", r"stage"],
}


class PlannerAgent:
    """Extracts a structured plan from normalized research chunks."""

    def generate_plan(self, payload: PlannerAgentInput) -> PlannerAgentOutput:
        text = " \n".join(payload.chunks)

        plan = PromptPlan(
            context=self._build_context(text, payload.target_stack),
            goals=self._extract_items(text, "goals", fallback=["Deliver a working prototype aligned with the research brief."]),
            assumptions=self._extract_items(text, "assumptions", fallback=["Stakeholders provide timely reviews."]),
            non_goals=self._extract_items(text, "non_goals", fallback=["Do not re-architect unrelated systems."]),
            risks=self._extract_items(text, "risks", fallback=["Timeline pressure may limit exploration."]),
            milestones=self._extract_items(text, "milestones", fallback=self._default_milestones(payload.style)),
        )
        return PlannerAgentOutput(plan=plan)

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
                    cleaned = re.sub(r"^[^:]*:", "", normalized).strip(" -•\t")
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
        tokens = [line.strip(" -•\t") for line in text.split("\n") if line.strip()]
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