"""API router exposing The Coding Orchestrator endpoints."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Response

from projectplanner.logging_utils import get_logger
from projectplanner.models import IngestionRequest
from projectplanner.orchestrator.models import (
    OrchestratorApprovalResponse,
    OrchestratorMilestonesEnvelope,
    OrchestratorPromptsEnvelope,
    OrchestratorResult,
    OrchestratorSessionStatus,
    OrchestratorSummaryEnvelope,
    SummaryDecision,
)
from projectplanner.services import orchestrator as orchestrator_service

router = APIRouter()
LOGGER = get_logger(__name__)


def _handle_error(run_id: str, error: Exception) -> None:
    if isinstance(error, orchestrator_service.OrchestratorSessionNotFound):
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.") from error
    if isinstance(error, orchestrator_service.OrchestratorInvalidState):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise HTTPException(status_code=500, detail="Unexpected orchestrator error.") from error


@router.post("/runs", response_model=OrchestratorSummaryEnvelope)
async def create_run(payload: IngestionRequest) -> OrchestratorSummaryEnvelope:
    """Create a new orchestrator run and return the generated summary."""

    run_id, summary, source = orchestrator_service.create_session(payload)
    LOGGER.info(
        "Orchestrator run %s created",
        run_id,
        extra={"event": "api.orchestrator.run_created", "run_id": run_id},
    )
    return OrchestratorSummaryEnvelope(run_id=run_id, summary=summary, source=source)


@router.get("/runs", response_model=List[OrchestratorSessionStatus])
async def list_runs() -> List[OrchestratorSessionStatus]:
    """List active orchestrator sessions."""

    return orchestrator_service.list_sessions()


@router.get("/runs/{run_id}", response_model=OrchestratorSessionStatus)
async def get_run(run_id: str) -> OrchestratorSessionStatus:
    try:
        return orchestrator_service.describe_session(run_id)
    except Exception as error:  # pragma: no cover - FastAPI converts to HTTP later
        _handle_error(run_id, error)
        raise  # unreachable


@router.delete("/runs/{run_id}", status_code=204, response_class=Response)
async def delete_run(run_id: str) -> Response:
    deleted = orchestrator_service.discard_session(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")
    return Response(status_code=204)


@router.get("/runs/{run_id}/summary", response_model=OrchestratorSummaryEnvelope)
async def get_summary(run_id: str) -> OrchestratorSummaryEnvelope:
    try:
        summary = orchestrator_service.get_summary(run_id)
        status = orchestrator_service.describe_session(run_id)
    except Exception as error:  # pragma: no cover - translated below
        _handle_error(run_id, error)
        raise
    return OrchestratorSummaryEnvelope(run_id=run_id, summary=summary, source=status.source)


@router.post("/runs/{run_id}/summary/regenerate", response_model=OrchestratorSummaryEnvelope)
async def regenerate_summary(run_id: str) -> OrchestratorSummaryEnvelope:
    try:
        summary = orchestrator_service.regenerate_summary(run_id)
        status = orchestrator_service.describe_session(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorSummaryEnvelope(run_id=run_id, summary=summary, source=status.source)


@router.post("/runs/{run_id}/summary/decision", response_model=OrchestratorApprovalResponse)
async def summary_decision(run_id: str, decision: SummaryDecision) -> OrchestratorApprovalResponse:
    try:
        orchestrator_service.approve_summary(run_id, decision.approved)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorApprovalResponse(run_id=run_id, stage="summary", approved=decision.approved)


@router.post("/runs/{run_id}/milestones", response_model=OrchestratorMilestonesEnvelope)
async def generate_milestones(run_id: str) -> OrchestratorMilestonesEnvelope:
    try:
        plan, snapshot = orchestrator_service.generate_milestones(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorMilestonesEnvelope(run_id=run_id, milestones=plan, graph=snapshot)


@router.get("/runs/{run_id}/milestones", response_model=OrchestratorMilestonesEnvelope)
async def get_milestones(run_id: str) -> OrchestratorMilestonesEnvelope:
    try:
        plan, snapshot = orchestrator_service.get_milestones(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorMilestonesEnvelope(run_id=run_id, milestones=plan, graph=snapshot)


@router.post("/runs/{run_id}/milestones/decision", response_model=OrchestratorApprovalResponse)
async def milestones_decision(run_id: str, decision: SummaryDecision) -> OrchestratorApprovalResponse:
    try:
        orchestrator_service.approve_milestones(run_id, decision.approved)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorApprovalResponse(run_id=run_id, stage="milestones", approved=decision.approved)


@router.post("/runs/{run_id}/prompts", response_model=OrchestratorPromptsEnvelope)
async def generate_prompts(run_id: str) -> OrchestratorPromptsEnvelope:
    try:
        prompts = orchestrator_service.generate_prompts(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorPromptsEnvelope(run_id=run_id, prompts=prompts)


@router.get("/runs/{run_id}/prompts", response_model=OrchestratorPromptsEnvelope)
async def get_prompts(run_id: str) -> OrchestratorPromptsEnvelope:
    try:
        prompts = orchestrator_service.get_prompts(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise
    return OrchestratorPromptsEnvelope(run_id=run_id, prompts=prompts)


@router.post("/runs/{run_id}/finalize", response_model=OrchestratorResult)
async def finalize(run_id: str) -> OrchestratorResult:
    try:
        return orchestrator_service.finalize(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise


@router.get("/runs/{run_id}/result", response_model=OrchestratorResult)
async def get_result(run_id: str) -> OrchestratorResult:
    try:
        status = orchestrator_service.describe_session(run_id)
        if not status.prompts_ready:
            raise orchestrator_service.OrchestratorInvalidState("Prompts have not been generated for this run.")
        return orchestrator_service.finalize(run_id)
    except Exception as error:
        _handle_error(run_id, error)
        raise

