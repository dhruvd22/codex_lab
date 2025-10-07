"""Service helpers for The Coding Orchestrator module."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List, Tuple

from projectplanner.logging_utils import get_logger
from projectplanner.models import IngestionRequest
from projectplanner.orchestrator.models import (
    BlueprintSummary,
    GraphCoverageSnapshot,
    MilestonePlan,
    OrchestratorResult,
    OrchestratorSessionStatus,
    PromptBundle,
)
from projectplanner.orchestrator.workflow import CodingOrchestrator
from projectplanner.services.ingest import decode_blueprint_payload

LOGGER = get_logger(__name__)


@dataclass
class _OrchestratorSession:
    orchestrator: CodingOrchestrator
    source: str | None
    created_at: datetime
    updated_at: datetime


_SESSIONS: Dict[str, _OrchestratorSession] = {}
_LOCK = RLock()


class OrchestratorSessionNotFound(KeyError):
    """Raised when a run id is not registered with the orchestrator service."""


class OrchestratorInvalidState(RuntimeError):
    """Raised when callers attempt to advance the workflow out of order."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_session(run_id: str) -> _OrchestratorSession:
    with _LOCK:
        session = _SESSIONS.get(run_id)
    if not session:
        raise OrchestratorSessionNotFound(run_id)
    return session


def _touch(session: _OrchestratorSession) -> None:
    with _LOCK:
        session.updated_at = _now()


def create_session(payload: IngestionRequest) -> Tuple[str, BlueprintSummary, str | None]:
    """Create a new orchestrator session and return its initial summary."""

    text, source = decode_blueprint_payload(payload)
    orchestrator = CodingOrchestrator()
    summary = orchestrator.ingest_blueprint(text)
    timestamp = _now()
    with _LOCK:
        _SESSIONS[orchestrator.run_id] = _OrchestratorSession(
            orchestrator=orchestrator,
            source=source,
            created_at=timestamp,
            updated_at=timestamp,
        )
    LOGGER.info(
        "Orchestrator session created",
        extra={
            "event": "orchestrator.session.created",
            "run_id": orchestrator.run_id,
            "payload": {"source": source, "summary_ready": True},
        },
    )
    return orchestrator.run_id, summary, source


def regenerate_summary(run_id: str) -> BlueprintSummary:
    """Regenerate the summary for an existing session."""

    session = _get_session(run_id)
    try:
        summary = session.orchestrator.regenerate_summary()
    except RuntimeError as exc:  # pragma: no cover - defensive
        raise OrchestratorInvalidState(str(exc)) from exc
    _touch(session)
    return summary


def get_summary(run_id: str) -> BlueprintSummary:
    session = _get_session(run_id)
    summary = session.orchestrator.get_summary()
    if summary is None:
        raise OrchestratorInvalidState("Summary not available for this run.")
    return summary


def approve_summary(run_id: str, approved: bool) -> None:
    session = _get_session(run_id)
    session.orchestrator.approve_summary(approved)
    _touch(session)
    LOGGER.info(
        "Summary approval recorded",
        extra={
            "event": "orchestrator.summary.approval.recorded",
            "run_id": run_id,
            "payload": {"approved": approved},
        },
    )


def generate_milestones(run_id: str) -> Tuple[MilestonePlan, GraphCoverageSnapshot]:
    session = _get_session(run_id)
    try:
        plan, snapshot = session.orchestrator.generate_milestones()
    except RuntimeError as exc:
        raise OrchestratorInvalidState(str(exc)) from exc
    _touch(session)
    return plan, snapshot


def get_milestones(run_id: str) -> Tuple[MilestonePlan, GraphCoverageSnapshot]:
    session = _get_session(run_id)
    plan = session.orchestrator.get_milestone_plan()
    if plan is None:
        raise OrchestratorInvalidState("Milestones have not been generated for this run.")
    snapshot = session.orchestrator.current_graph_snapshot()
    return plan, snapshot


def approve_milestones(run_id: str, approved: bool) -> None:
    session = _get_session(run_id)
    session.orchestrator.approve_milestones(approved)
    _touch(session)
    LOGGER.info(
        "Milestone approval recorded",
        extra={
            "event": "orchestrator.milestones.approval.recorded",
            "run_id": run_id,
            "payload": {"approved": approved},
        },
    )


def generate_prompts(run_id: str) -> PromptBundle:
    session = _get_session(run_id)
    try:
        prompts = session.orchestrator.generate_prompts()
    except RuntimeError as exc:
        raise OrchestratorInvalidState(str(exc)) from exc
    _touch(session)
    return prompts


def get_prompts(run_id: str) -> PromptBundle:
    session = _get_session(run_id)
    prompts = session.orchestrator.get_prompts()
    if prompts is None:
        raise OrchestratorInvalidState("Prompts have not been generated for this run.")
    return prompts


def finalize(run_id: str) -> OrchestratorResult:
    session = _get_session(run_id)
    try:
        result = session.orchestrator.finalize()
    except RuntimeError as exc:
        raise OrchestratorInvalidState(str(exc)) from exc
    _touch(session)
    return result


def describe_session(run_id: str) -> OrchestratorSessionStatus:
    session = _get_session(run_id)
    orchestrator = session.orchestrator
    return OrchestratorSessionStatus(
        run_id=run_id,
        source=session.source,
        summary_ready=orchestrator.summary_ready,
        summary_approved=orchestrator.summary_approved,
        milestones_ready=orchestrator.milestones_ready,
        milestones_approved=orchestrator.milestones_approved,
        prompts_ready=orchestrator.prompts_ready,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def list_sessions() -> List[OrchestratorSessionStatus]:
    with _LOCK:
        run_ids = list(_SESSIONS.keys())
    return [describe_session(run_id) for run_id in run_ids]


def discard_session(run_id: str) -> bool:
    with _LOCK:
        session = _SESSIONS.pop(run_id, None)
    if session:
        LOGGER.info(
            "Orchestrator session discarded",
            extra={"event": "orchestrator.session.discarded", "run_id": run_id},
        )
        return True
    return False
