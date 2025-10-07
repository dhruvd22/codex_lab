"""Milestones agent orchestrating summary and milestone synthesis."""
from __future__ import annotations

import json
import os
import textwrap
from typing import Any, Dict, Iterable, List

from projectplanner.agents._openai_helpers import (
    create_chat_completion,
    extract_choice_metadata,
    extract_message_content,
)
from projectplanner.logging_utils import get_logger, log_prompt
from projectplanner.orchestrator.config import (
    get_max_completion_tokens,
    get_milestone_model,
    get_summary_model,
    get_temperature,
)
from projectplanner.orchestrator.models import BlueprintSummary, Milestone, MilestonePlan

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


LOGGER = get_logger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You are MilestonesAgent, an expert strategist who distills application blueprints into structured insights. "
    "Return JSON with the keys: summary (string), highlights (list of concise strings), risks (list of strings), "
    "components (list of major application components), metadata (object with any additional signals). "
    "Avoid commentary outside the JSON payload."
)

MILESTONE_SYSTEM_PROMPT = (
    "You are MilestonesAgent operating in milestone design mode. "
    "Given the approved application summary, produce exactly five milestones following the schema: "
    "{\"milestones\": [{\"milestoneID\": number, \"MilestoneDetails\": string, \"Context\": string}]}. "
    "Each milestone should be delivery-focused, mutually exclusive, and collectively cover the entire scope. "
    "Context must cite the specific blueprint signals informing the milestone."
)

MAX_CONTEXT_CHARS = 18000


class MilestonesAgent:
    """Agent responsible for blueprint synthesis and milestone planning."""

    def __init__(self) -> None:
        self._summary_model = get_summary_model()
        self._milestone_model = get_milestone_model()
        self._temperature = get_temperature()
        self._max_tokens = get_max_completion_tokens()
        self._client = self._init_client()

    def _init_client(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or OpenAI is None:
            LOGGER.warning(
                "MilestonesAgent running without OpenAI client; fallback heuristics active.",
                extra={"event": "orchestrator.milestones.no_client"},
            )
            return None
        try:
            client = OpenAI(api_key=api_key)
            LOGGER.info(
                "MilestonesAgent OpenAI client initialized",
                extra={
                    "event": "orchestrator.milestones.client_ready",
                    "payload": {
                        "summary_model": self._summary_model,
                        "milestone_model": self._milestone_model,
                    },
                },
            )
            return client
        except Exception:
            LOGGER.exception(
                "Failed to initialize OpenAI client; using heuristic mode.",
                extra={"event": "orchestrator.milestones.client_error"},
            )
            return None

    def summarize_blueprint(self, *, run_id: str, blueprint_text: str) -> BlueprintSummary:
        """Produce a structured summary for the supplied blueprint text."""

        cleaned = self._compress_text(blueprint_text)
        if not self._client:
            return self._heuristic_summary(run_id=run_id, blueprint_text=cleaned)

        user_prompt = textwrap.dedent(
            f"""
            Blueprint:
            {cleaned}
            """
        ).strip()
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        log_prompt(
            agent="MilestonesAgent",
            role="system",
            prompt=SUMMARY_SYSTEM_PROMPT,
            run_id=run_id,
            stage="request",
            model=self._summary_model,
        )
        log_prompt(
            agent="MilestonesAgent",
            role="user",
            prompt=user_prompt,
            run_id=run_id,
            stage="request",
            model=self._summary_model,
        )
        try:
            response = create_chat_completion(
                self._client,
                model=self._summary_model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            metadata = extract_choice_metadata(response)
            content = self._extract_content(response)
            log_prompt(
                agent="MilestonesAgent",
                role="assistant",
                prompt=content,
                run_id=run_id,
                stage="response",
                model=metadata.get("model", self._summary_model),
                metadata=metadata,
            )
            payload = self._parse_json(content)
            return BlueprintSummary(
                run_id=run_id,
                summary=payload.get("summary", ""),
                highlights=self._ensure_list(payload.get("highlights")),
                risks=self._ensure_list(payload.get("risks")),
                components=self._ensure_list(payload.get("components")),
                metadata=payload.get("metadata", {}) or {},
            )
        except Exception:
            LOGGER.exception(
                "Summary generation via GPT failed; using heuristics.",
                extra={"event": "orchestrator.milestones.summary_error", "run_id": run_id},
            )
            return self._heuristic_summary(run_id=run_id, blueprint_text=cleaned)

    def generate_milestones(self, *, run_id: str, summary: BlueprintSummary) -> MilestonePlan:
        """Generate exactly five milestones from the approved summary."""

        if not self._client:
            return self._heuristic_milestones(run_id=run_id, summary=summary)

        sections = [
            self._format_section("Highlights", summary.highlights),
            self._format_section("Known risks", summary.risks),
            self._format_section("Components to cover", summary.components),
        ]
        prompt = textwrap.dedent(
            f"""
            Approved application summary:
            {summary.summary}

            {sections[0]}

            {sections[1]}

            {sections[2]}

            Return milestones in the specified JSON schema.
            """
        ).strip()
        messages = [
            {"role": "system", "content": MILESTONE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        log_prompt(
            agent="MilestonesAgent",
            role="system",
            prompt=MILESTONE_SYSTEM_PROMPT,
            run_id=run_id,
            stage="request",
            model=self._milestone_model,
        )
        log_prompt(
            agent="MilestonesAgent",
            role="user",
            prompt=prompt,
            run_id=run_id,
            stage="request",
            model=self._milestone_model,
        )
        try:
            response = create_chat_completion(
                self._client,
                model=self._milestone_model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            metadata = extract_choice_metadata(response)
            content = self._extract_content(response)
            log_prompt(
                agent="MilestonesAgent",
                role="assistant",
                prompt=content,
                run_id=run_id,
                stage="response",
                model=metadata.get("model", self._milestone_model),
                metadata=metadata,
            )
            payload = self._parse_json(content)
            raw_milestones: Iterable[Dict[str, Any]] = payload.get("milestones", [])
            milestones = [
                Milestone(
                    milestone_id=int(item.get("milestoneID", idx + 1)),
                    details=str(item.get("MilestoneDetails", "")).strip(),
                    context=str(item.get("Context", "")).strip(),
                )
                for idx, item in enumerate(raw_milestones)
            ]
            milestones = self._ensure_five_milestones(milestones)
            return MilestonePlan(run_id=run_id, milestones=milestones, raw_response=content)
        except Exception:
            LOGGER.exception(
                "Milestone generation via GPT failed; using heuristics.",
                extra={"event": "orchestrator.milestones.milestone_error", "run_id": run_id},
            )
            return self._heuristic_milestones(run_id=run_id, summary=summary)

    @staticmethod
    def _compress_text(text: str) -> str:
        cleaned = (text or "").strip()
        if len(cleaned) <= MAX_CONTEXT_CHARS:
            return cleaned
        return cleaned[:MAX_CONTEXT_CHARS]

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
    def _format_section(label: str, items: List[str]) -> str:
        if not items:
            return f"{label}:\n- (none identified)"
        lines = "\n".join(f"- {item}" for item in items)
        return f"{label}:\n{lines}"

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

    def _heuristic_summary(self, *, run_id: str, blueprint_text: str) -> BlueprintSummary:
        lines = [line.strip() for line in blueprint_text.splitlines() if line.strip()]
        summary = " ".join(lines[:5])[:800]
        highlights = lines[:3]
        risks = [line for line in lines if "risk" in line.lower()][:3]
        components = [
            line
            for line in lines
            if any(keyword in line.lower() for keyword in ("api", "service", "database", "frontend"))
        ][:5]
        LOGGER.info(
            "Heuristic summary generated",
            extra={"event": "orchestrator.milestones.heuristic_summary", "run_id": run_id},
        )
        return BlueprintSummary(
            run_id=run_id,
            summary=summary or blueprint_text[:400],
            highlights=highlights,
            risks=risks,
            components=components,
            metadata={"mode": "heuristic"},
        )

    def _heuristic_milestones(self, *, run_id: str, summary: BlueprintSummary) -> MilestonePlan:
        base_titles = [
            "Collect detailed requirements",
            "Design target architecture",
            "Implement core services",
            "Integrate experience and data",
            "Validate and prepare launch",
        ]
        milestones = []
        for idx, title in enumerate(base_titles, start=1):
            context_bits = summary.highlights[idx - 1 : idx + 1]
            milestones.append(
                Milestone(
                    milestone_id=idx,
                    details=title,
                    context=" ".join(context_bits) if context_bits else summary.summary,
                )
            )
        LOGGER.info(
            "Heuristic milestones generated",
            extra={"event": "orchestrator.milestones.heuristic_milestones", "run_id": run_id},
        )
        return MilestonePlan(run_id=run_id, milestones=milestones)

    def _ensure_five_milestones(self, milestones: List[Milestone]) -> List[Milestone]:
        filtered = [m for m in milestones if m.details]
        existing_ids = {m.milestone_id for m in filtered}
        next_id = 1
        while len(filtered) < 5:
            while next_id in existing_ids:
                next_id += 1
            filtered.append(
                Milestone(
                    milestone_id=next_id,
                    details=f"Milestone {next_id}: Expand coverage",
                    context="",
                )
            )
            existing_ids.add(next_id)
            next_id += 1
        filtered.sort(key=lambda item: item.milestone_id)
        return filtered[:5]


__all__ = ["MilestonesAgent"]
