import pytest
from sqlalchemy import create_engine

from projectplanner.services.store import ProjectPlannerStore


@pytest.fixture()
def store(tmp_path) -> ProjectPlannerStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'planner.db'}", future=True)
    store = ProjectPlannerStore(engine)
    store.ensure_schema()
    return store
