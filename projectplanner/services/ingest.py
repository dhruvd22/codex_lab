"""Document ingestion utilities."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import httpx

from projectplanner.models import DocumentStats, IngestionRequest, IngestionResponse
from projectplanner.services.store import ProjectPlannerStore, StoredChunk

from projectplanner.logging_utils import get_logger, log_prompt

UPLOAD_ROOT = (Path(__file__).resolve().parent / "../data/uploads").resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

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


async def ingest_document(payload: IngestionRequest, *, store: ProjectPlannerStore) -> IngestionResponse:
    """Ingest content, persist chunks, and return run metadata."""

    run_id = str(uuid.uuid4())
    LOGGER.info(
        "Starting ingestion run %s",
        run_id,
        extra={
            "event": "ingest.start",
            "run_id": run_id,
            "payload": {
                "has_text": bool(payload.text),
                "has_url": bool(payload.url),
                "has_file": bool(payload.file_id),
            },
        },
    )
    raw_text, source = await _load_text(payload)
    LOGGER.info(
        "Loaded source for run %s from %s",
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
    embeddings = await _embed_chunks(unique_chunks, run_id=run_id)

    stored_chunks = [
        StoredChunk(idx=i, text=chunk, embedding=embedding, metadata={"source": source})
        for i, (chunk, embedding) in enumerate(zip(unique_chunks, embeddings))
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


async def _load_text(payload: IngestionRequest) -> Tuple[str, str]:
    if payload.text:
        decoded = _maybe_decode_text(payload.text, payload.format_hint)
        return decoded, "inline"
    if payload.file_id:
        return _load_from_file(payload.file_id, payload.format_hint), "upload"
    if payload.url:
        return await _load_from_url(str(payload.url), payload.format_hint), "url"
    raise ValueError("No content source provided.")


def _load_from_file(file_id: str, format_hint: Optional[str]) -> str:
    path = UPLOAD_ROOT / file_id
    if not path.exists():
        raise FileNotFoundError(f"File {file_id} not found in uploads directory.")
    raw = path.read_bytes()
    suffix = path.suffix.lstrip(".") if path.suffix else None
    LOGGER.debug(
        "Loaded %s bytes from uploaded file %s",
        len(raw),
        file_id,
        extra={"event": "ingest.file.load", "payload": {"file_id": file_id, "bytes": len(raw)}},
    )
    return _parse_by_format(raw, suffix or format_hint or "txt")



def _maybe_decode_text(text: str, format_hint: Optional[str]) -> str:
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


async def _load_from_url(url: str, format_hint: Optional[str]) -> str:
    LOGGER.info(
        "Fetching ingestion content from %s",
        url,
        extra={"event": "ingest.fetch.start", "payload": {"url": url}},
    )
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception:
            LOGGER.warning(
                "Failed to fetch ingestion content from %s",
                url,
                exc_info=True,
                extra={"event": "ingest.fetch.error", "payload": {"url": url}},
            )
            raise
    content_type = response.headers.get("content-type", "text/plain")
    suffix = _infer_suffix_from_content_type(content_type)
    LOGGER.info(
        "Fetched %s bytes from %s",
        len(response.content),
        url,
        extra={
            "event": "ingest.fetch.complete",
            "payload": {"url": url, "content_type": content_type},
        },
    )
    return _parse_by_format(response.content, suffix or format_hint or "txt")


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


async def _embed_chunks(chunks: Sequence[str], run_id: Optional[str]) -> List[Optional[List[float]]]:
    loop = asyncio.get_running_loop()
    LOGGER.debug(
        "Embedding %s chunks",
        len(chunks),
        extra={"event": "ingest.embedding.start", "run_id": run_id, "payload": {"count": len(chunks)}},
    )
    tasks = [
        loop.run_in_executor(None, _embed_text, chunk, run_id, idx)
        for idx, chunk in enumerate(chunks)
    ]
    results = await asyncio.gather(*tasks)
    LOGGER.debug(
        "Embedding finished for %s chunks",
        len(chunks),
        extra={"event": "ingest.embedding.complete", "run_id": run_id, "payload": {"count": len(chunks)}},
    )
    return results


def _embed_text(text: str, run_id: Optional[str] = None, index: Optional[int] = None) -> Optional[List[float]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        LOGGER.debug(
            "Embedding fallback (hash) used for chunk %s",
            index,
            extra={
                "event": "ingest.embedding.hash",
                "run_id": run_id,
                "payload": {"index": index},
            },
        )
        return [int(b) / 255.0 for b in digest[:64]]
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        log_prompt(
            agent="EmbeddingService",
            role="embedding",
            prompt=text,
            run_id=run_id,
            model="text-embedding-3-small",
            metadata={"index": index},
        )
        response = client.embeddings.create(input=[text], model="text-embedding-3-small")
        return list(response.data[0].embedding)
    except Exception:
        LOGGER.warning(
            "Embedding request failed for chunk %s",
            index,
            exc_info=True,
            extra={
                "event": "ingest.embedding.failure",
                "run_id": run_id,
                "payload": {"index": index},
            },
        )
        return None
