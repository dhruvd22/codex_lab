import pytest

from projectplanner.models import IngestionRequest, PlanRequest
from projectplanner.services import ingest, plan


@pytest.mark.asyncio
async def test_planner_outputs_milestones_and_steps(store):
    sample_doc = """
    Goals: Improve onboarding
    Risks: Integration debt and timeline slip
    Milestone: Discovery sync and scope lock
    Milestone: Architecture workshop
    Milestone: Build guided flows
    Milestone: QA regression
    Milestone: Launch readiness
    """.strip()

    ingest_response = await ingest.ingest_document(
        IngestionRequest(text=sample_doc),
        store=store,
    )

    response = await plan.run_planning_workflow(
        PlanRequest(run_id=ingest_response.run_id, style="strict"),
        store=store,
    )

    assert len(response.plan.milestones) >= 1
    assert len(response.steps) >= 5
