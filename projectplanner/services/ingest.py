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

UPLOAD_ROOT = (Path(__file__).resolve().parent / "../data/uploads").resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

CHUNK_CHAR_LIMIT = 1200
CHUNK_OVERLAP = 200


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
    raw_text, source = await _load_text(payload)
    normalized_text = _normalize_text(raw_text)
    chunks = _chunk_text(normalized_text)
    unique_chunks = _dedupe_chunks(chunks)
    embeddings = await _embed_chunks(unique_chunks)

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
    suffix = path.suffix.lstrip(".") if path.suffix else None
    return _parse_by_format(path.read_bytes(), suffix or format_hint or "txt")



def _maybe_decode_text(text: str, format_hint: Optional[str]) -> str:
    if text.startswith("base64:"):
        _, meta, encoded = text.split(":", 2)
        data = base64.b64decode(encoded)
        suffix = _infer_suffix_from_content_type(meta) or format_hint or "txt"
        return _parse_by_format(data, suffix)
    return text
async def _load_from_url(url: str, format_hint: Optional[str]) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/plain")
        suffix = _infer_suffix_from_content_type(content_type)
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
    suffix = format_hint.lower().lstrip(".")
    if suffix == "pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard for optional dependency
            raise RuntimeError("Install pypdf to ingest PDF files.") from exc
        reader = PdfReader(BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text
    if suffix in {"md", "markdown"}:
        return data.decode("utf-8", errors="ignore")
    if suffix == "docx":
        try:
            import docx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install python-docx to ingest DOCX files.") from exc
        document = docx.Document(BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    return data.decode("utf-8", errors="ignore")


def _normalize_text(text: str) -> str:
    if not text:
        return ""

    sanitized = text.translate(_EXTRA_WHITESPACE_TRANSLATION)
    collapsed = re.sub(r"\s+", " ", sanitized).strip()
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
    return [chunk for chunk in chunks if chunk]


def _dedupe_chunks(chunks: Sequence[str]) -> List[str]:
    seen = set()
    unique: List[str] = []
    for chunk in chunks:
        fingerprint = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(chunk)
    return unique


def _count_words(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


async def _embed_chunks(chunks: Sequence[str]) -> List[Optional[List[float]]]:
    loop = asyncio.get_running_loop()
    return await asyncio.gather(*(loop.run_in_executor(None, _embed_text, chunk) for chunk in chunks))


def _embed_text(text: str) -> Optional[List[float]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [int(b) / 255.0 for b in digest[:64]]
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(input=[text], model="text-embedding-3-small")
        return list(response.data[0].embedding)
    except Exception:
        return None
