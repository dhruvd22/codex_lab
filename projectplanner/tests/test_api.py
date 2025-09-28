from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from projectplanner.api.main import create_app
from projectplanner.models import PromptStep
from projectplanner.services.store import ProjectPlannerStore


def _make_client(tmp_path):
    app = create_app()
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", future=True)
    store = ProjectPlannerStore(engine)
    store.ensure_schema()
    app.state.store = store
    return TestClient(app)


def test_api_plan_flow(tmp_path):
    client = _make_client(tmp_path)

    ingest_response = client.post(
        "/api/projectplanner/ingest",
        json={"text": "Goals: improve ux\nRisks: timeline"},
    )
    assert ingest_response.status_code == 200
    run_id = ingest_response.json()["run_id"]

    plan_response = client.post(
        "/api/projectplanner/plan",
        json={"run_id": run_id, "style": "strict"},
    )
    assert plan_response.status_code == 200
    data = plan_response.json()
    assert data["plan"]["goals"]
    steps = data["steps"]

    steps[0]["title"] = "Updated Title"
    update_response = client.put(
        f"/api/projectplanner/steps/{run_id}",
        json={"steps": steps},
    )
    assert update_response.status_code == 200
    assert update_response.json()["steps"][0]["title"] == "Updated Title"

    export_response = client.post(
        "/api/projectplanner/export",
        json={"run_id": run_id, "format": "yaml"},
    )
    assert export_response.status_code == 200
    assert "plan:" in export_response.text
