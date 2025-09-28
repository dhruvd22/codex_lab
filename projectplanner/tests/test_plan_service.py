import pytest

from projectplanner.models import ExportRequest, IngestionRequest, PlanRequest
from projectplanner.services import ingest, plan


@pytest.mark.asyncio
async def test_planning_workflow_generates_plan(store):
    ingest_response = await ingest.ingest_document(
        IngestionRequest(text="Goals: Ship app\nRisks: scope"),
        store=store,
    )
    request = PlanRequest(run_id=ingest_response.run_id, style="strict")
    response = await plan.run_planning_workflow(request, store=store)

    assert response.plan.goals
    assert response.steps
    assert response.report.overall_score >= 0

    export_bundle = await plan.export_prompts(
        ExportRequest(run_id=ingest_response.run_id, format="jsonl"),
        store=store,
    )
    assert "plan" in export_bundle.content
