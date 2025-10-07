"""Graph audit agent validating coverage between milestones and components."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

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
from projectplanner.orchestrator.graph_store import GraphStore
from projectplanner.orchestrator.models import (
    BlueprintSummary,
    GraphCoverageSnapshot,
    Milestone,
)

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


LOGGER = get_logger(__name__)

AUDIT_SYSTEM_PROMPT = (
    "You are GraphAuditAgent, accountable for verifying that every blueprint component is covered by the milestone plan. "
    "Given the component graph and milestone descriptions, respond with JSON containing: "
    "notes (string), uncovered_nodes (list of component names requiring additional work), "
    "covered_nodes (list of components that appear to be satisfied)."
)


class GraphAuditAgent:
    """Validates graph coverage using GPT or heuristics."""

    def __init__(self) -> None:
        self._model = get_prompt_model()
        self._temperature = get_temperature()
        self._max_tokens = get_max_completion_tokens()
        self._client = self._init_client()

    def _init_client(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or OpenAI is None:
            LOGGER.warning(
                "GraphAuditAgent running without OpenAI client; heuristic audit active.",
                extra={"event": "orchestrator.graphaudit.no_client"},
            )
            return None
        try:
            client = OpenAI(api_key=api_key)
            LOGGER.info(
                "GraphAuditAgent OpenAI client initialized",
                extra={"event": "orchestrator.graphaudit.client_ready", "payload": {"model": self._model}},
            )
            return client
        except Exception:
            LOGGER.exception(
                "Failed to initialize OpenAI client; using heuristic audit.",
                extra={"event": "orchestrator.graphaudit.client_error"},
            )
            return None

    def audit(
        self,
        *,
        run_id: str,
        summary: BlueprintSummary,
        milestones: List[Milestone],
        graph_store: GraphStore,
    ) -> GraphCoverageSnapshot:
        baseline_snapshot = graph_store.snapshot()
        if not self._client:
            return self._heuristic_snapshot(baseline_snapshot)

        payload = self._format_payload(summary, milestones, graph_store)
        log_prompt(
            agent="GraphAuditAgent",
            role="system",
            prompt=AUDIT_SYSTEM_PROMPT,
            run_id=run_id,
            stage="request",
            model=self._model,
        )
        log_prompt(
            agent="GraphAuditAgent",
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
                    {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            metadata = extract_choice_metadata(response)
            content = self._extract_content(response)
            log_prompt(
                agent="GraphAuditAgent",
                role="assistant",
                prompt=content,
                run_id=run_id,
                stage="response",
                model=metadata.get("model", self._model),
                metadata=metadata,
            )
            data = self._parse_json(content)
            covered = self._merge_lists(baseline_snapshot.covered_nodes, data.get("covered_nodes"))
            uncovered = self._merge_lists(baseline_snapshot.uncovered_nodes, data.get("uncovered_nodes"))
            return GraphCoverageSnapshot(
                run_id=run_id,
                covered_nodes=sorted(set(covered)),
                uncovered_nodes=sorted(set(uncovered)),
                notes=data.get("notes") or baseline_snapshot.notes,
            )
        except Exception:
            LOGGER.exception(
                "Graph coverage audit failed; returning heuristic snapshot.",
                extra={"event": "orchestrator.graphaudit.audit_error", "run_id": run_id},
            )
            return self._heuristic_snapshot(baseline_snapshot)

    @staticmethod
    def _format_payload(
        summary: BlueprintSummary,
        milestones: List[Milestone],
        graph_store: GraphStore,
    ) -> str:
        milestone_lines = "\n".join(
            f"Milestone {milestone.milestone_id}: {milestone.details} | Context: {milestone.context}"
            for milestone in sorted(milestones, key=lambda item: item.milestone_id)
        )
        node_lines = "\n".join(
            f"- {node.name} :: milestones {', '.join(str(mid) for mid in node.milestone_ids) or 'none'}"
            for node in sorted(graph_store.nodes(), key=lambda item: item.name.lower())
        )
        return (
            f"Application summary:\n{summary.summary}\n\n"
            f"Milestones:\n{milestone_lines or 'None'}\n\n"
            f"Graph nodes and linked milestones:\n{node_lines or '- None registered'}\n\n"
            "Confirm coverage for each node."
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
    def _merge_lists(base: List[str], extra: Any) -> List[str]:
        merged = list(base)
        if isinstance(extra, list):
            merged.extend(str(item).strip() for item in extra if str(item).strip())
        elif isinstance(extra, str) and extra.strip():
            merged.append(extra.strip())
        return merged

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

    @staticmethod
    def _heuristic_snapshot(snapshot: GraphCoverageSnapshot) -> GraphCoverageSnapshot:
        if snapshot.uncovered_nodes and not snapshot.notes:
            notes = (
                "Heuristic audit: components remain uncovered. Prioritize mapping milestones to: "
                + ", ".join(snapshot.uncovered_nodes)
            )
        else:
            notes = snapshot.notes or "Heuristic audit: coverage inferred from milestone text."
        return GraphCoverageSnapshot(
            run_id=snapshot.run_id,
            covered_nodes=snapshot.covered_nodes,
            uncovered_nodes=snapshot.uncovered_nodes,
            notes=notes,
        )


__all__ = ["GraphAuditAgent"]
