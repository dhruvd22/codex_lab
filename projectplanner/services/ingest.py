"""Document ingestion utilities."""
from __future__ import annotations

import base64
import hashlib
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Sequence, Tuple


from projectplanner.models import DocumentStats, IngestionRequest, IngestionResponse
from projectplanner.services.store import ProjectPlannerStore, StoredChunk

from projectplanner.logging_utils import get_logger

CHUNK_CHAR_LIMIT = 1200
CHUNK_OVERLAP = 200


LOGGER = get_logger(__name__)


_EXTRA_WHITESPACE_TRANSLATION = {
    ord('\u200b'): ' ',  # zero-width space
    ord('\u200c'): ' ',  # zero-width non-joiner
    ord('\u200d'): ' ',  # zero-width joiner
    ord('\u2060'): ' ',  # word joiner
    ord('\ufeff'): ' ',  # byte-order mark
    0: ' ',                # NULL bytes from malformed PDFs
}


@dataclass
class IngestionResult:
    run_id: str
    stats: DocumentStats
    chunks: Sequence[StoredChunk]



def decode_blueprint_payload(payload: IngestionRequest) -> tuple[str, str]:
    """Decode the submitted blueprint into text and its source label."""

    return _decode_blueprint(payload)


async def ingest_document(payload: IngestionRequest, *, store: ProjectPlannerStore) -> IngestionResponse:
    """Ingest blueprint content, persist chunks, and return run metadata."""

    run_id = str(uuid.uuid4())
    LOGGER.info(
        "Starting ingestion run %s",
        run_id,
        extra={
            "event": "ingest.start",
            "run_id": run_id,
            "payload": {
                "filename": payload.filename,
                "format_hint": payload.format_hint,
                "encoded": payload.blueprint.startswith("base64:"),
            },
        },
    )
    raw_text, source = decode_blueprint_payload(payload)
    LOGGER.info(
        "Loaded blueprint for run %s from %s",
        run_id,
        source,
        extra={
            "event": "ingest.source_loaded",
            "run_id": run_id,
            "payload": {"source": source, "raw_chars": len(raw_text)},
        },
    )
    normalized_text = _normalize_text(raw_text)
    chunks = _chunk_text(normalized_text)
    unique_chunks = _dedupe_chunks(chunks)
    LOGGER.debug(
        "Chunked document into %s unique segments (from %s chunks)",
        len(unique_chunks),
        len(chunks),
        extra={
            "event": "ingest.chunking",
            "run_id": run_id,
            "payload": {"chunk_count": len(chunks), "unique_count": len(unique_chunks)},
        },
    )

    stored_chunks = [
        StoredChunk(idx=i, text=chunk, metadata={"source": source})
        for i, chunk in enumerate(unique_chunks)
    ]

    stats = DocumentStats(
        word_count=_count_words(normalized_text),
        char_count=len(normalized_text),
        chunk_count=len(stored_chunks),
    )

    store.register_run(run_id, source=source, stats=stats.dict())
    store.add_chunks(run_id, stored_chunks)
    LOGGER.info(
        "Completed ingestion run %s",
        run_id,
        extra={
            "event": "ingest.complete",
            "run_id": run_id,
            "payload": {
                "word_count": stats.word_count,
                "chunk_count": stats.chunk_count,
                "source": source,
            },
        },
    )
    return IngestionResponse(run_id=run_id, stats=stats)


def _decode_blueprint(payload: IngestionRequest) -> Tuple[str, str]:
    """Decode the submitted blueprint into plain text and describe its origin."""

    blueprint = payload.blueprint
    decoded = _decode_blueprint_text(blueprint, payload.format_hint)
    if blueprint.startswith("base64:"):
        source = payload.filename or "uploaded-blueprint"
    else:
        source = payload.filename or "inline-blueprint"
    return decoded, source



def _decode_blueprint_text(text: str, format_hint: Optional[str]) -> str:
    if text.startswith("base64:"):
        _, meta, encoded = text.split(":", 2)
        data = base64.b64decode(encoded)
        suffix = _infer_suffix_from_content_type(meta) or format_hint or "txt"
        LOGGER.debug(
            "Decoded base64 payload (%s bytes) with suffix %s",
            len(data),
            suffix,
            extra={"event": "ingest.inline.decode", "payload": {"bytes": len(data), "suffix": suffix}},
        )
        return _parse_by_format(data, suffix)
    LOGGER.debug(
        "Using inline text payload (%s chars)",
        len(text),
        extra={"event": "ingest.inline.text", "payload": {"chars": len(text)}},
    )
    return text



def _infer_suffix_from_content_type(content_type: str) -> Optional[str]:
    content_type = content_type.lower()
    if "pdf" in content_type:
        return "pdf"
    if "markdown" in content_type or "md" in content_type:
        return "md"
    if "word" in content_type or "docx" in content_type:
        return "docx"
    if "plain" in content_type:
        return "txt"
    return None


def _parse_by_format(data: bytes, format_hint: str) -> str:
    suffix = (format_hint or "txt").lower().lstrip(".")
    if suffix == "pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard for optional dependency
            raise RuntimeError("Install pypdf to ingest PDF files.") from exc
        reader = PdfReader(BytesIO(data))
        parsed = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix in {"md", "markdown"}:
        parsed = data.decode("utf-8", errors="ignore")
    elif suffix == "docx":
        try:
            import docx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install python-docx to ingest DOCX files.") from exc
        document = docx.Document(BytesIO(data))
        parsed = "\n".join(p.text for p in document.paragraphs)
    else:
        parsed = data.decode("utf-8", errors="ignore")
    LOGGER.debug(
        "Parsed %s bytes as %s (%s chars)",
        len(data),
        suffix,
        len(parsed),
        extra={"event": "ingest.parse", "payload": {"suffix": suffix, "chars": len(parsed)}},
    )
    return parsed


def _normalize_text(text: str) -> str:
    if not text:
        return ""

    sanitized = text.translate(_EXTRA_WHITESPACE_TRANSLATION)
    collapsed = re.sub(r"\s+", " ", sanitized).strip()
    LOGGER.debug(
        "Normalized text from %s to %s chars",
        len(text),
        len(collapsed),
        extra={"event": "ingest.normalize", "payload": {"input_chars": len(text), "output_chars": len(collapsed)}},
    )
    return collapsed


def _chunk_text(text: str) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + CHUNK_CHAR_LIMIT, length)
        chunk = text[start:end]
        chunks.append(chunk.strip())
        if end == length:
            break
        start = max(end - CHUNK_OVERLAP, 0)
        if start == end:
            break
    filtered = [chunk for chunk in chunks if chunk]
    LOGGER.debug(
        "Chunked text into %s segments",
        len(filtered),
        extra={"event": "ingest.chunk.create", "payload": {"chunk_count": len(filtered)}},
    )
    return filtered


def _dedupe_chunks(chunks: Sequence[str]) -> List[str]:
    seen = set()
    unique: List[str] = []
    for chunk in chunks:
        fingerprint = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(chunk)
    LOGGER.debug(
        "Deduplicated %s chunks down to %s",
        len(chunks),
        len(unique),
        extra={"event": "ingest.chunk.dedupe", "payload": {"input": len(chunks), "output": len(unique)}},
    )
    return unique


def _count_words(text: str) -> int:
    if not text:
        LOGGER.debug(
            "Word count requested for empty text",
            extra={"event": "ingest.wordcount", "payload": {"words": 0}},
        )
        return 0
    words = len(text.split())
    LOGGER.debug(
        "Computed word count",
        extra={"event": "ingest.wordcount", "payload": {"words": words}},
    )
    return words




