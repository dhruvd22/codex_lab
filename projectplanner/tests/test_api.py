import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from projectplanner.api.main import create_app
from projectplanner.services.store import ProjectPlannerStore


def _make_client(tmp_path):
    app = create_app()
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", future=True)
    store = ProjectPlannerStore(engine)
    store.ensure_schema()
    app.state.store = store
    return TestClient(app)


def _parse_final_plan(stream_text: str) -> dict:
    normalized = stream_text.replace('\\r\\n', '\\n')
    blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    for block in blocks:
        event_type = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if event_type == "final_plan" and data_lines:
            data_str = "\n".join(data_lines)
            return json.loads(data_str)
    raise AssertionError("final_plan event not found in stream")

def test_api_plan_flow(tmp_path):
    client = _make_client(tmp_path)

    ingest_response = client.post(
        "/api/codingconductor/ingest",
        json={"blueprint": "Goals: improve ux\nRisks: timeline"},
    )
    assert ingest_response.status_code == 200
    run_id = ingest_response.json()["run_id"]

    with client.stream(
        "POST",
        "/api/codingconductor/plan",
        json={"run_id": run_id, "style": "strict"},
    ) as stream:
        assert stream.status_code == 200
        body = "".join(chunk for chunk in stream.iter_text())

    final_payload = _parse_final_plan(body)
    assert final_payload["plan"]["goals"]
    steps = final_payload["steps"]

    steps[0]["title"] = "Updated Title"
    update_response = client.put(
        f"/api/codingconductor/steps/{run_id}",
        json={"steps": steps},
    )
    assert update_response.status_code == 200
    assert update_response.json()["steps"][0]["title"] == "Updated Title"

    export_response = client.post(
        "/api/codingconductor/export",
        json={"run_id": run_id, "format": "yaml"},
    )
    assert export_response.status_code == 200
    assert "plan:" in export_response.text
