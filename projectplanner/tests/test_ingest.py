import pytest

from projectplanner.models import IngestionRequest
from projectplanner.services import ingest


@pytest.mark.asyncio
async def test_ingest_text(store):
    payload = IngestionRequest(blueprint="Goals: Build a planner", format_hint="md")
    response = await ingest.ingest_document(payload, store=store)

    assert response.stats.word_count >= 4
    assert response.stats.chunk_count >= 1
    assert store.run_exists(response.run_id)

@pytest.mark.asyncio
async def test_ingest_zero_width_space_text(store):
    zero_width_space = "\u200b"  # zero-width spaces inserted by some PDF extracts
    payload = IngestionRequest(
        blueprint=f"Hello{zero_width_space}world from{zero_width_space}PDF",
        format_hint='md',
    )
    response = await ingest.ingest_document(payload, store=store)

    assert response.stats.word_count == 4
    assert response.stats.chunk_count >= 1

