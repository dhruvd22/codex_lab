"""Shared logging utilities for project planner and future modules."""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Deque, Dict, Mapping, MutableMapping, Optional, Sequence, Union

JSONValue = Union[str, int, float, bool, None, list["JSONValue"], Dict[str, "JSONValue"]]

_DEFAULT_CAPACITY = 2000
try:
    _DEFAULT_CAPACITY = max(256, int(os.getenv("PROJECTPLANNER_LOG_CAPACITY", "2000")))
except (TypeError, ValueError):
    _DEFAULT_CAPACITY = 2000

try:
    _PROMPT_PREVIEW_LIMIT = int(os.getenv("PROJECTPLANNER_LOG_PROMPT_PREVIEW", "600"))
    _PROMPT_PREVIEW_LIMIT = max(120, min(_PROMPT_PREVIEW_LIMIT, 4000))
except (TypeError, ValueError):
    _PROMPT_PREVIEW_LIMIT = 600


def _coerce_level(level: str | int | None) -> Optional[int]:
    if level is None:
        return None
    if isinstance(level, int):
        return level
    candidate = getattr(logging, str(level).upper(), None)
    if isinstance(candidate, int):
        return candidate
    try:
        return int(level)
    except (TypeError, ValueError):
        return None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _sanitize(value: Any) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _sanitize(val) for key, val in value.items()}
    if _is_sequence(value):
        return [_sanitize(item) for item in value]
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return _sanitize(value.model_dump())  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - defensive
            return str(value)
    if hasattr(value, "dict") and callable(getattr(value, "dict")):
        try:
            return _sanitize(value.dict())  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - defensive
            return str(value)
    if hasattr(value, "__dict__"):
        return _sanitize(vars(value))
    return str(value)


def _preview_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    remainder = len(text) - limit
    return f"{text[:limit]}... (+{remainder} chars)"


class InMemoryLogHandler(logging.Handler):
    """Logging handler that keeps a bounded in-memory buffer."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        super().__init__()
        self.capacity = capacity
        self._buffer: Deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = RLock()
        self._sequence = 0
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - exercised via integrations
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            message = str(record.msg)

        entry: dict[str, Any] = {
            "sequence": 0,
            "created": record.created,
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname,
            "levelno": record.levelno,
            "message": message,
            "run_id": getattr(record, "run_id", None),
            "event": getattr(record, "event", None),
            "payload": _sanitize(getattr(record, "payload", None)),
            "context": _sanitize(getattr(record, "context", None)),
            "exception": None,
            "thread": record.threadName,
        }
        if record.exc_info:
            entry["exception"] = self._formatter.formatException(record.exc_info)
        elif getattr(record, "exc_text", None):
            entry["exception"] = str(record.exc_text)

        with self._lock:
            self._sequence += 1
            entry["sequence"] = self._sequence
            self._buffer.append(entry)

    def records(
        self,
        *,
        after: Optional[int] = None,
        limit: Optional[int] = None,
        levelno: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = list(self._buffer)
        if after is not None:
            snapshot = [item for item in snapshot if item["sequence"] > after]
        if levelno is not None:
            snapshot = [item for item in snapshot if item["levelno"] >= levelno]
        if limit is not None:
            snapshot = snapshot[-limit:]
        return snapshot

    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._sequence = 0


class LogManager:
    """Coordinator for shared logging state across modules."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self.handler = InMemoryLogHandler(capacity)
        self._lock = RLock()
        self._configured = False
        self._attached_logger: Optional[logging.Logger] = None

    @property
    def configured(self) -> bool:
        return self._configured

    def configure(
        self,
        *,
        logger_name: Optional[str] = None,
        level: str | int | None = None,
    ) -> None:
        with self._lock:
            target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
            if self._attached_logger is not target_logger:
                if self._attached_logger is not None:
                    try:
                        self._attached_logger.removeHandler(self.handler)
                    except (ValueError, AttributeError):  # pragma: no cover - defensive
                        pass
                if self.handler not in target_logger.handlers:
                    target_logger.addHandler(self.handler)
                self._attached_logger = target_logger
            resolved_level = _coerce_level(level)
            if resolved_level is not None:
                target_logger.setLevel(resolved_level)
            elif not target_logger.handlers:
                target_logger.setLevel(logging.INFO)
            self._configured = True

    def get_logs(
        self,
        *,
        after: Optional[int] = None,
        limit: Optional[int] = None,
        level: str | int | None = None,
    ) -> list[dict[str, Any]]:
        levelno = _coerce_level(level)
        records = self.handler.records(after=after, limit=limit, levelno=levelno)
        return [
            {
                "sequence": item["sequence"],
                "timestamp": item["timestamp"],
                "level": item["level"],
                "logger": item["logger"],
                "message": item["message"],
                "run_id": item.get("run_id"),
                "event": item.get("event"),
                "payload": item.get("payload"),
                "exception": item.get("exception"),
            }
            for item in records
        ]

    def latest_cursor(self) -> int:
        return self.handler.latest_sequence()

    def clear(self) -> None:
        self.handler.clear()


_LOG_MANAGER = LogManager()


def ensure_configured() -> None:
    if not _LOG_MANAGER.configured:
        default_level = os.getenv("PROJECTPLANNER_LOG_LEVEL") or os.getenv("APP_LOG_LEVEL")
        default_logger = os.getenv("PROJECTPLANNER_LOGGER_NAME") or None
        _LOG_MANAGER.configure(logger_name=default_logger, level=default_level)


def configure_logging(*, logger_name: Optional[str] = None, level: str | int | None = None) -> None:
    ensure_configured()
    _LOG_MANAGER.configure(logger_name=logger_name, level=level)


def get_log_manager() -> LogManager:
    ensure_configured()
    return _LOG_MANAGER


def get_logger(name: str) -> logging.Logger:
    ensure_configured()
    return logging.getLogger(name)


def log_prompt(
    *,
    agent: str,
    role: str,
    prompt: str,
    run_id: Optional[str] = None,
    stage: str = "request",
    model: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    ensure_configured()
    target = logger or logging.getLogger(f"{agent}.prompt")
    preview = _preview_text(prompt, _PROMPT_PREVIEW_LIMIT)
    payload: MutableMapping[str, Any] = {
        "agent": agent,
        "role": role,
        "stage": stage,
        "chars": len(prompt),
        "preview": preview,
    }
    if model:
        payload["model"] = model
    if metadata:
        payload["metadata"] = _sanitize(metadata)
    target.info(
        "%s prompt for %s (%d chars)",
        stage.capitalize(),
        agent,
        len(prompt),
        extra={
            "run_id": run_id,
            "event": f"prompt.{stage}",
            "payload": payload,
        },
    )


__all__ = [
    "configure_logging",
    "get_log_manager",
    "get_logger",
    "log_prompt",
]
