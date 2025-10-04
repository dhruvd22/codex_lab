"""Coordinator agent orchestrating GPT-5 milestone synthesis."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, Iterable, List, Sequence

from projectplanner.agents.schemas import CoordinatorAgentInput, CoordinatorAgentOutput
from projectplanner.models import MilestoneObjective

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

DEFAULT_COORDINATOR_MODEL = os.getenv("PROJECTPLANNER_COORDINATOR_MODEL", "gpt-5")
MAX_CONTEXT_CHARS = 18000

COORDINATOR_SYSTEM_PROMPT = (
    "You are Agent 0, the lead project coordinator for an AI execution graph. "
    "Analyze the provided research excerpts and return an ordered list of milestones that, when completed, deliver the requested application. "
    "Milestones must be outcome-oriented, objective, and reference tangible deliverables. "
    "Respond strictly with JSON that matches the schema: {\"milestones\": [{\"id\": \"m01\", \"title\": \"...\", \"objective\": \"...\", \"success_criteria\": [\"...\"], \"dependencies\": []}]}. "
    "Use lowercase identifiers that satisfy ^[a-z0-9-]+$ and do not add commentary. "
    "Return between four and seven milestones."
)


class CoordinatorAgent:
    """Synthesizes milestone objectives by delegating to GPT-5 when available."""

    def __init__(self) -> None:
        self._model = os.getenv("PROJECTPLANNER_COORDINATOR_MODEL", DEFAULT_COORDINATOR_MODEL)
        self._client = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key and OpenAI is not None:
            try:
                self._client = OpenAI(api_key=api_key)
            except Exception:  # pragma: no cover - initialization failure fallback
                LOGGER.warning("Failed to initialize OpenAI client for CoordinatorAgent; falling back to heuristics.", exc_info=True)
                self._client = None

    def synthesize_objectives(self, payload: CoordinatorAgentInput) -> CoordinatorAgentOutput:
        """Produce ordered milestone objectives using GPT-5 with heuristic fallback."""

        objectives: List[MilestoneObjective] = []
        if self._client:
            context = self._compress_chunks(payload.chunks)
            user_prompt = self._build_user_prompt(payload, context)
            try:
                raw = self._request_objectives(user_prompt)
                objectives = self._parse_objectives(raw)
            except Exception:  # pragma: no cover - rely on fallback below
                LOGGER.warning("Coordinator GPT synthesis failed; reverting to heuristic objectives.", exc_info=True)
                objectives = []
        if not objectives:
            objectives = self._fallback_objectives(payload)
        return CoordinatorAgentOutput(objectives=objectives)

    def _request_objectives(self, user_prompt: str) -> str:
        if not self._client:
            raise RuntimeError("OpenAI client unavailable for coordinator agent.")
        response = self._client.chat.completions.create(  # type: ignore[attr-defined]
            model=self._model,
            messages=[
                {"role": "system", "content": COORDINATOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        if not response.choices:
            raise ValueError("Coordinator model returned no choices.")
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if not content:
            raise ValueError("Coordinator model returned empty content.")
        return content.strip()

    def _build_user_prompt(self, payload: CoordinatorAgentInput, context: str) -> str:
        stack = payload.target_stack
        return (
            f"Run ID: {payload.run_id}\n"
            f"Target stack: backend={stack.backend}, frontend={stack.frontend}, database={stack.db}\n"
            f"Planning style: {payload.style}\n"
            "Document excerpts (normalized and truncated to 18k characters):\n"
            '"""\n'
            f"{context}\n"
            '"""\n'
            "Deliverable requirements:\n"
            "- Return 4-7 milestones that cover the entire project lifecycle.\n"
            "- Each milestone must be objective-driven and support downstream AI-assisted development.\n"
            "- Provide success_criteria describing observable completion signals for the milestone.\n"
            "- Include dependencies as milestone ids for any prerequisite work (use [] if none).\n"
        )

    def _parse_objectives(self, raw: str) -> List[MilestoneObjective]:
        cleaned = raw.strip()
        fenced = re.match(r"```(?:json)?\s*(.*)```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        data = json.loads(cleaned)
        entries = data.get("milestones") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise ValueError("Coordinator model response missing 'milestones' array.")

        prepared: List[Dict[str, object]] = []
        id_lookup: Dict[str, str] = {}
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            raw_id = str(entry.get("id") or f"m{idx + 1:02d}")
            sanitized_id = self._sanitize_id(raw_id, idx)
            id_lookup[raw_id.lower()] = sanitized_id
            id_lookup[sanitized_id] = sanitized_id

            title = self._clean_text(entry.get("title") or entry.get("name") or f"Milestone {idx + 1}")
            objective = self._clean_text(entry.get("objective") or entry.get("summary") or title)

            success_raw = entry.get("success_criteria") or entry.get("successCriteria") or entry.get("criteria") or []
            success_items = self._normalize_list(success_raw)
            if not success_items:
                success_items = [f"Objective for {title} is achieved."]

            dependencies_raw = entry.get("dependencies") or entry.get("depends_on") or entry.get("prerequisites") or []
            dependencies_items = self._normalize_list(dependencies_raw)

            prepared.append(
                {
                    "id": sanitized_id,
                    "title": title,
                    "objective": objective,
                    "success": success_items,
                    "dependencies": dependencies_items,
                    "order": self._safe_order(entry.get("order"), idx),
                }
            )

        if not prepared:
            return []

        valid_ids = {item["id"] for item in prepared}
        for item in prepared:
            deps: List[str] = []
            for dep in item["dependencies"]:  # type: ignore[index]
                key = str(dep).lower()
                normalized = id_lookup.get(key) or self._sanitize_id(str(dep), None)
                if normalized and normalized in valid_ids and normalized != item["id"]:
                    deps.append(normalized)
            item["dependencies"] = list(dict.fromkeys(deps))

        prepared.sort(key=lambda itm: (itm["order"], itm["id"]))

        objectives: List[MilestoneObjective] = []
        for idx, item in enumerate(prepared):
            objectives.append(
                MilestoneObjective(
                    id=item["id"],
                    order=idx,
                    title=item["title"],
                    objective=item["objective"],
                    success_criteria=item["success"],
                    dependencies=item["dependencies"],
                )
            )
        return objectives

    def _sanitize_id(self, candidate: str, index: int | None) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", candidate.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        if slug:
            return slug
        if index is None:
            return ""
        return f"m{index + 1:02d}"

    def _safe_order(self, value: object, fallback: int) -> int:
        try:
            order = int(value)  # type: ignore[arg-type]
            if order >= 0:
                return order
        except (TypeError, ValueError):
            pass
        return fallback

    def _normalize_list(self, value: object) -> List[str]:
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, Iterable):
            items = [str(item) for item in value]
        else:
            items = []
        normalized = [self._clean_text(item) for item in items if self._clean_text(item)]
        return normalized

    def _clean_text(self, value: object) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return re.sub(r"\s+", " ", text)

    def _compress_chunks(self, chunks: Sequence[str], limit: int = MAX_CONTEXT_CHARS) -> str:
        joined = "\n\n".join(chunks)
        if len(joined) <= limit:
            return joined
        truncated = joined[:limit]
        cutoff = truncated.rfind("\n")
        if cutoff > limit * 0.6:
            return truncated[:cutoff]
        return truncated

    def _fallback_objectives(self, payload: CoordinatorAgentInput) -> List[MilestoneObjective]:
        stack = payload.target_stack
        fallback_sequence = [
            {
                "title": "Milestone 1: Establish project baseline",
                "objective": "Clarify problem framing, user goals, success metrics, and risks extracted from the research corpus.",
                "success": [
                    "Stakeholders confirm the documented scope, personas, and constraints.",
                    "Key risks and assumptions are surfaced with owners.",
                ],
                "dependencies": [],
            },
            {
                "title": "Milestone 2: Draft architecture and integration strategy",
                "objective": (
                    "Define application architecture across backend {stack_backend}, frontend {stack_frontend}, and database {stack_db}, "
                    "including service boundaries and integration contracts."
                ).format(
                    stack_backend=stack.backend,
                    stack_frontend=stack.frontend,
                    stack_db=stack.db,
                ),
                "success": [
                    "Architecture diagram and integration plan are reviewed and accepted.",
                    "Data flows, API surfaces, and security considerations are documented.",
                ],
                "dependencies": ["m01"],
            },
            {
                "title": "Milestone 3: Build backend foundation",
                "objective": (
                    "Stand up the {stack_backend} services, persistence models for {stack_db}, and core domain workflows.".format(
                        stack_backend=stack.backend,
                        stack_db=stack.db,
                    )
                ),
                "success": [
                    "Core service skeletons compile with health checks and observability hooks.",
                    "Primary domain endpoints and data models pass baseline automated tests.",
                ],
                "dependencies": ["m01", "m02"],
            },
            {
                "title": "Milestone 4: Build frontend experience",
                "objective": (
                    "Implement the {stack_frontend} user experience, wiring to backend APIs and delivering critical user journeys.".format(
                        stack_frontend=stack.frontend
                    )
                ),
                "success": [
                    "Priority user journeys render end-to-end against live backend contracts.",
                    "Accessibility and responsiveness checks meet defined quality bars.",
                ],
                "dependencies": ["m01", "m02", "m03"],
            },
            {
                "title": "Milestone 5: Integrate, validate, and launch",
                "objective": "Complete integration, regression coverage, operational readiness, and launch planning.",
                "success": [
                    "Regression suite covers critical paths with automated verification.",
                    "Deployment, monitoring, and rollback playbooks are signed off.",
                ],
                "dependencies": ["m01", "m02", "m03", "m04"],
            },
        ]

        objectives: List[MilestoneObjective] = []
        for index, item in enumerate(fallback_sequence):
            objectives.append(
                MilestoneObjective(
                    id=f"m{index + 1:02d}",
                    order=index,
                    title=item["title"],
                    objective=item["objective"],
                    success_criteria=item["success"],
                    dependencies=item["dependencies"],
                )
            )
        return objectives
