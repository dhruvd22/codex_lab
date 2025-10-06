"""Observability snapshot utilities for the coding conductor workflow."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from projectplanner.logging_utils import get_log_manager
from projectplanner.models import (
    ObservabilityCall,
    ObservabilityEdge,
    ObservabilityNode,
    ObservabilityResponse,
)

MAX_LOGS_PER_STREAM = 400
MAX_CALLS = 150


@dataclass(frozen=True)
class ModuleDefinition:
    """Declarative description of a workflow module surfaced in the dashboard."""

    id: str
    name: str
    category: str
    description: str
    event_prefixes: Tuple[str, ...] = ()
    event_names: Tuple[str, ...] = ()
    logger_prefixes: Tuple[str, ...] = ()
    prompt_agents: Tuple[str, ...] = ()
    duration_pairs: Tuple[Tuple[str, str], ...] = ()


MODULE_DEFINITIONS: Sequence[ModuleDefinition] = (
    ModuleDefinition(
        id="api_ingest",
        name="Ingest Endpoint",
        category="endpoint",
        description="Receives documents from the UI and triggers ingestion.",
        event_prefixes=("api.ingest.",),
        duration_pairs=(("api.ingest.start", "api.ingest.complete"),),
    ),
    ModuleDefinition(
        id="ingestion_pipeline",
        name="Ingestion Pipeline",
        category="pipeline",
        description="Normalizes input and chunks content for downstream planning.",
        event_prefixes=("ingest.",),
        logger_prefixes=("projectplanner.services.ingest",),
        duration_pairs=(("ingest.start", "ingest.complete"),),
    ),
    ModuleDefinition(
        id="document_store",
        name="Document Store",
        category="storage",
        description="Persists normalized chunks and run artifacts for planning.",
        event_prefixes=("store.",),
        logger_prefixes=("projectplanner.services.store",),
    ),
    ModuleDefinition(
        id="api_plan",
        name="Plan Endpoint",
        category="endpoint",
        description="Streams multi-agent planning updates back to the UI.",
        event_names=("planning.start", "planning.finalize"),
        event_prefixes=("api.plan.",),
        logger_prefixes=("projectplanner.services.plan",),
    ),
    ModuleDefinition(
        id="coordinator_agent",
        name="Coordinator Agent",
        category="agent",
        description="Synthesizes milestone objectives from document context.",
        event_prefixes=("planning.coordinator.", "agent.coordinator."),
        prompt_agents=("CoordinatorAgent",),
        duration_pairs=(("planning.coordinator.start", "planning.coordinator.complete"),),
    ),
    ModuleDefinition(
        id="planner_agent",
        name="Planner Agent",
        category="agent",
        description="Drafts the high-level execution steps for the workflow.",
        event_prefixes=("planning.planner.", "agent.planner."),
        prompt_agents=("PlannerAgent",),
        duration_pairs=(("planning.planner.start", "planning.planner.complete"),),
    ),
    ModuleDefinition(
        id="decomposer_agent",
        name="Decomposer Agent",
        category="agent",
        description="Breaks long-horizon work into executable step-level prompts.",
        event_prefixes=("planning.decomposer.", "agent.decomposer."),
        prompt_agents=("DecomposerAgent",),
        duration_pairs=(("planning.decomposer.start", "planning.decomposer.complete"),),
    ),
    ModuleDefinition(
        id="reviewer_agent",
        name="Reviewer Agent",
        category="agent",
        description="Scores draft plans and injects review feedback.",
        event_prefixes=("planning.reviewer.", "agent.reviewer."),
        prompt_agents=("ReviewerAgent",),
        duration_pairs=(("planning.reviewer.start", "planning.reviewer.complete"),),
    ),
    ModuleDefinition(
        id="api_export",
        name="Export Endpoint",
        category="endpoint",
        description="Packages finalized artifacts for download.",
        event_prefixes=("api.export.", "planning.export."),
        duration_pairs=(
            ("api.export.start", "api.export.complete"),
            ("planning.export.start", "planning.export.complete"),
        ),
    ),
)

EDGE_DEFINITIONS: Sequence[Tuple[str, str, Optional[str]]] = (
    ("api_ingest", "ingestion_pipeline", "Document intake"),
    ("ingestion_pipeline", "document_store", "Persist context"),
    ("document_store", "api_plan", "Supply context"),
    ("api_plan", "coordinator_agent", "Launch objectives"),
    ("coordinator_agent", "planner_agent", "Share milestones"),
    ("planner_agent", "decomposer_agent", "Break into steps"),
    ("decomposer_agent", "reviewer_agent", "Send drafts"),
    ("reviewer_agent", "api_plan", "Return feedback"),
    ("planner_agent", "document_store", "Store plan artifacts"),
    ("api_plan", "api_export", "Finalize outputs"),
    ("document_store", "api_export", "Read artifacts"),
)


def build_observability_snapshot(
    *,
    limit: Optional[int] = None,
    max_calls: int = MAX_CALLS,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> ObservabilityResponse:
    """Assemble a near-real-time snapshot for the observability dashboard."""

    manager = get_log_manager()
    session_started_at = manager.session_started_at
    effective_limit = limit if limit is not None else MAX_LOGS_PER_STREAM
    call_limit = max(1, max_calls)
    runtime_logs = manager.get_logs(limit=effective_limit, log_type="runtime", start=start, end=end)
    prompt_logs = manager.get_logs(limit=effective_limit, log_type="prompts", start=start, end=end)

    records: List[Tuple[datetime, str, Dict[str, Any]]] = []
    for entry in runtime_logs:
        records.append((_parse_timestamp(entry.get("timestamp")), "runtime", entry))
    for entry in prompt_logs:
        records.append((_parse_timestamp(entry.get("timestamp")), "prompts", entry))
    records.sort(key=lambda item: item[0])

    stats_map = _initial_stats()
    active_durations: Dict[Tuple[str, int, str], datetime] = {}
    calls: List[ObservabilityCall] = []

    for timestamp, log_type, record in records:
        module_id = _match_module(record, log_type)
        if not module_id:
            continue
        stats = stats_map[module_id]
        stats["event_count"] += 1
        level = (record.get("level") or "INFO").upper()
        stats["levels"].add(level)
        if level in {"ERROR", "CRITICAL"}:
            stats["error_count"] += 1
        elif level == "WARNING":
            stats["warning_count"] += 1

        run_id = record.get("run_id")
        if run_id:
            stats["run_ids"].add(run_id)
        event = record.get("event")
        if stats["last_timestamp"] is None or timestamp >= stats["last_timestamp"]:
            stats["last_timestamp"] = timestamp
            stats["last_event"] = event
            stats["last_message"] = record.get("message")

        _update_durations(stats, module_id, run_id, event, timestamp, active_durations)

        sanitized_payload = _sanitize_payload(record.get("payload"))
        calls.append(
            ObservabilityCall(
                module_id=module_id,
                timestamp=timestamp,
                level=level,
                event=event,
                message=record.get("message", ""),
                log_type=log_type,
                run_id=run_id,
                payload=sanitized_payload,
            )
        )

    calls.sort(key=lambda call: call.timestamp, reverse=True)
    if len(calls) > call_limit:
        calls = calls[:call_limit]

    nodes: List[ObservabilityNode] = []
    for definition in MODULE_DEFINITIONS:
        stats = stats_map[definition.id]
        status = _derive_status(stats["levels"])
        metrics: Dict[str, Any] = {
            "total_runs": len(stats["run_ids"]),
            "warning_count": stats["warning_count"],
            "error_count": stats["error_count"],
        }
        if stats["latencies"]:
            avg = sum(stats["latencies"]) / len(stats["latencies"])
            metrics["avg_latency_ms"] = round(avg, 2)
            metrics["p95_latency_ms"] = round(_percentile(stats["latencies"], 0.95), 2)
            metrics["last_latency_ms"] = round(stats["last_latency"] or stats["latencies"][-1], 2)
        if stats["last_message"]:
            metrics["last_message"] = stats["last_message"]
        nodes.append(
            ObservabilityNode(
                id=definition.id,
                name=definition.name,
                category=definition.category,
                description=definition.description,
                status=status,
                event_count=stats["event_count"],
                run_ids=sorted(stats["run_ids"]),
                last_event=stats["last_event"],
                last_timestamp=stats["last_timestamp"],
                metrics=metrics,
            )
        )

    edges = [
        ObservabilityEdge(source=source, target=target, label=label)
        for source, target, label in EDGE_DEFINITIONS
    ]

    return ObservabilityResponse(
        generated_at=datetime.now(timezone.utc),
        session_started_at=session_started_at,
        nodes=nodes,
        edges=edges,
        calls=calls,
    )


def _initial_stats() -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for definition in MODULE_DEFINITIONS:
        stats[definition.id] = {
            "definition": definition,
            "event_count": 0,
            "levels": set(),
            "run_ids": set(),
            "last_event": None,
            "last_timestamp": None,
            "last_message": None,
            "latencies": [],
            "last_latency": None,
            "warning_count": 0,
            "error_count": 0,
        }
    return stats


def _parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)


def _match_module(record: Dict[str, Any], log_type: str) -> Optional[str]:
    event = record.get("event") or ""
    logger_name = record.get("logger") or ""
    payload = record.get("payload")

    for definition in MODULE_DEFINITIONS:
        if event and event in definition.event_names:
            return definition.id
    for definition in MODULE_DEFINITIONS:
        if event and any(event.startswith(prefix) for prefix in definition.event_prefixes):
            return definition.id
    for definition in MODULE_DEFINITIONS:
        if any(logger_name.startswith(prefix) for prefix in definition.logger_prefixes):
            return definition.id
    if log_type == "prompts" and isinstance(payload, dict):
        agent = str(payload.get("agent") or "").lower()
        if agent:
            for definition in MODULE_DEFINITIONS:
                if any(agent.startswith(candidate.lower()) for candidate in definition.prompt_agents):
                    return definition.id
    return None


def _derive_status(levels: Iterable[str]) -> str:
    normalized = {level for level in levels if level}
    if not normalized:
        return "idle"
    if any(level in {"ERROR", "CRITICAL"} for level in normalized):
        return "error"
    if "WARNING" in normalized:
        return "degraded"
    return "healthy"


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = percentile * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _sanitize_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    sanitized: Dict[str, Any] = {}
    for key, value in payload.items():
        if key == "content":
            continue
        sanitized[key] = value
    return sanitized or None


def _update_durations(
    stats: Dict[str, Any],
    module_id: str,
    run_id: Optional[str],
    event: Optional[str],
    timestamp: datetime,
    active: Dict[Tuple[str, int, str], datetime],
) -> None:
    definition: ModuleDefinition = stats["definition"]
    if not event or not definition.duration_pairs:
        return
    run_key = run_id or "__global__"
    for index, (start_event, end_event) in enumerate(definition.duration_pairs):
        key = (module_id, index, run_key)
        if event == start_event:
            active[key] = timestamp
        elif event == end_event:
            started = active.pop(key, None)
            if started is None:
                continue
            duration_ms = max(0.0, (timestamp - started).total_seconds() * 1000.0)
            stats["latencies"].append(duration_ms)
            stats["last_latency"] = duration_ms


__all__ = ["build_observability_snapshot"]
