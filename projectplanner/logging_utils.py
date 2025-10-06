"""Shared logging utilities for project planner and future modules."""
from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import FrameType
from typing import Any, Deque, Dict, Mapping, MutableMapping, Optional, Sequence, Union

JSONValue = Union[str, int, float, bool, None, list["JSONValue"], Dict[str, "JSONValue"]]

def _resolve_capacity(value: Optional[str]) -> Optional[int]:
    """Translate configuration input into an optional log buffer capacity."""

    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    normalized = candidate.lower()
    if normalized in {"0", "none", "unbounded", "infinite", "inf", "all"}:
        return None
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return max(256, parsed)


_DEFAULT_CAPACITY = _resolve_capacity(os.getenv("PROJECTPLANNER_LOG_CAPACITY"))

try:
    _PROMPT_PREVIEW_LIMIT = int(os.getenv("PROJECTPLANNER_LOG_PROMPT_PREVIEW", "600"))
    _PROMPT_PREVIEW_LIMIT = max(120, min(_PROMPT_PREVIEW_LIMIT, 4000))
except (TypeError, ValueError):
    _PROMPT_PREVIEW_LIMIT = 600



_PROMPT_LOG_ENV = os.getenv('PROJECTPLANNER_PROMPT_LOG')
if _PROMPT_LOG_ENV and _PROMPT_LOG_ENV.strip():
    _PROMPT_LOG_PATH = Path(_PROMPT_LOG_ENV).expanduser()
else:
    _PROMPT_LOG_PATH = Path(__file__).resolve().parent / 'data' / 'prompt_audit.jsonl'
_PROMPT_LOG_PATH = _PROMPT_LOG_PATH.resolve()
_PROMPT_LOG_LOCK = RLock()

def get_prompt_audit_path() -> Path:
    return _PROMPT_LOG_PATH


def _append_prompt_audit(entry: Mapping[str, Any]) -> None:
    try:
        _PROMPT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with _PROMPT_LOG_LOCK:
            with _PROMPT_LOG_PATH.open('a', encoding='utf-8') as handle:
                handle.write(line)
                handle.write('\n')
    except Exception:
        logging.getLogger('projectplanner.prompt_audit').exception(
            'Failed to write prompt audit entry.',
            extra={'event': 'prompt.audit.write_failed'},
        )


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


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Normalize various datetime inputs into aware UTC datetimes."""

    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        normalized = candidate.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


class InMemoryLogHandler(logging.Handler):
    """Logging handler that keeps an in-memory buffer for the current session."""

    def __init__(self, capacity: Optional[int] = _DEFAULT_CAPACITY) -> None:
        super().__init__()
        resolved_capacity = capacity if capacity and capacity > 0 else None
        self.capacity = resolved_capacity
        self._buffer: Deque[dict[str, Any]] = deque(maxlen=resolved_capacity)
        self._lock = RLock()
        self._sequence = 0
        self._formatter = logging.Formatter()
        self._session_started = datetime.now(timezone.utc)

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
        raw_type = getattr(record, "log_type", "runtime")
        if isinstance(raw_type, str):
            entry["type"] = raw_type.lower()
        else:
            entry["type"] = str(raw_type)
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
        log_type: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = list(self._buffer)
        normalized_type = log_type.lower() if isinstance(log_type, str) else None
        if after is not None:
            snapshot = [item for item in snapshot if item["sequence"] > after]
        if levelno is not None:
            snapshot = [item for item in snapshot if item["levelno"] >= levelno]
        if normalized_type is not None:
            snapshot = [item for item in snapshot if item.get("type", "runtime") == normalized_type]
        if start_time is not None:
            snapshot = [item for item in snapshot if item["created"] >= start_time]
        if end_time is not None:
            snapshot = [item for item in snapshot if item["created"] <= end_time]
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
            self._session_started = datetime.now(timezone.utc)

    @property
    def session_started_at(self) -> datetime:
        return self._session_started


class LogManager:
    """Coordinator for shared logging state across modules."""

    def __init__(self, capacity: Optional[int] = _DEFAULT_CAPACITY) -> None:
        self.handler = InMemoryLogHandler(capacity)
        self._lock = RLock()
        self._configured = False
        self._attached_logger: Optional[logging.Logger] = None

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def session_started_at(self) -> datetime:
        return self.handler.session_started_at

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
            elif target_logger.level > logging.INFO:
                # Ensure we capture INFO level logs by default
                target_logger.setLevel(logging.INFO)
            self._configured = True

    def get_logs(
        self,
        *,
        after: Optional[int] = None,
        limit: Optional[int] = None,
        level: str | int | None = None,
        log_type: str | None = None,
        start: datetime | str | float | None = None,
        end: datetime | str | float | None = None,
    ) -> list[dict[str, Any]]:
        levelno = _coerce_level(level)
        normalized_type = log_type.lower() if isinstance(log_type, str) else None
        start_dt = _coerce_datetime(start)
        end_dt = _coerce_datetime(end)
        start_ts = start_dt.timestamp() if start_dt else None
        end_ts = end_dt.timestamp() if end_dt else None
        records = self.handler.records(
            after=after,
            limit=limit,
            levelno=levelno,
            log_type=normalized_type,
            start_time=start_ts,
            end_time=end_ts,
        )
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
                "type": item.get("type", "runtime"),
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


_FUNCTION_CALL_TRACE_ENABLED = False
_FUNCTION_CALL_TRACE_LOCK = RLock()
_FUNCTION_CALL_TRACE_PREVIOUS: dict[str, object] = {"sys": None, "threading": None}
_FUNCTION_CALL_TRACE_LOGGER_NAME = "projectplanner.trace"
_CALL_TRACE_THREAD_STATE = threading.local()


def _normalize_prefixes(prefixes: Optional[Sequence[str]]) -> tuple[str, ...]:
    if not prefixes:
        return ("projectplanner", "app")
    cleaned: list[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        if not prefix:
            continue
        value = prefix.strip().rstrip('.')
        if not value or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    if not cleaned:
        return ("projectplanner", "app")
    return tuple(cleaned)


def _normalize_excludes(excludes: Optional[Sequence[str]]) -> tuple[str, ...]:
    if not excludes:
        return ()
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in excludes:
        if not item:
            continue
        value = item.strip().rstrip('.')
        if not value or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    return tuple(cleaned)


def _format_call_arg(value: Any, limit: int = 160) -> str:
    try:
        text = repr(value)
    except Exception:
        text = f"<unrepr {type(value).__name__}>"
    return _preview_text(text, limit)


def _should_trace_module(module: str, packages: tuple[str, ...], excludes: tuple[str, ...]) -> bool:
    if not module:
        return False
    for prefix in excludes:
        if module == prefix or module.startswith(prefix + '.'):
            return False
    for prefix in packages:
        if module == prefix or module.startswith(prefix + '.'):
            return True
    return False


def enable_function_call_logging(
    *,
    packages: Optional[Sequence[str]] = None,
    exclude_modules: Optional[Sequence[str]] = None,
    logger: Optional[logging.Logger] = None,
    level: str | int | None = None,
) -> None:
    global _FUNCTION_CALL_TRACE_ENABLED, _FUNCTION_CALL_TRACE_PREVIOUS
    with _FUNCTION_CALL_TRACE_LOCK:
        if _FUNCTION_CALL_TRACE_ENABLED:
            return
        ensure_configured()
        prefixes = _normalize_prefixes(packages)
        base_excludes = list(exclude_modules or [])
        excludes = _normalize_excludes(base_excludes)
        target_logger = logger or logging.getLogger(_FUNCTION_CALL_TRACE_LOGGER_NAME)
        resolved_level = _coerce_level(level)
        if resolved_level is not None:
            target_logger.setLevel(resolved_level)
        trace_state = _CALL_TRACE_THREAD_STATE

        def tracefunc(frame: FrameType, event: str, arg):
            if event != 'call':
                return tracefunc
            module = frame.f_globals.get('__name__', '')
            if not _should_trace_module(module, prefixes, excludes):
                return tracefunc
            if getattr(trace_state, 'active', False):
                return tracefunc
            trace_state.active = True
            try:
                func_name = frame.f_code.co_name
                if not func_name or func_name.startswith('<'):
                    return tracefunc
                qualname = getattr(frame.f_code, 'co_qualname', func_name)
                qualified = f"{module}.{qualname}" if module else qualname
                preview_items: list[str] = []
                try:
                    arginfo = inspect.getargvalues(frame)
                    for name in list(arginfo.args)[:5]:
                        if name in {'self', 'cls'}:
                            continue
                        preview_items.append(f"{name}={_format_call_arg(arginfo.locals.get(name))}")
                    if arginfo.varargs:
                        preview_items.append(f"*{arginfo.varargs}={_format_call_arg(arginfo.locals.get(arginfo.varargs))}")
                    if arginfo.keywords:
                        preview_items.append(f"**{arginfo.keywords}={_format_call_arg(arginfo.locals.get(arginfo.keywords))}")
                except Exception:
                    preview_items = []
                payload: MutableMapping[str, Any] = {
                    'qualified_name': qualified,
                    'module': module,
                    'function': func_name,
                    'file': frame.f_code.co_filename,
                    'line': frame.f_code.co_firstlineno,
                }
                if preview_items:
                    payload['arguments'] = preview_items
                target_logger.info(
                    'Call %s',
                    qualified,
                    extra={
                        'event': 'function.call',
                        'payload': payload,
                    },
                )
            finally:
                trace_state.active = False
            return tracefunc

        _FUNCTION_CALL_TRACE_PREVIOUS = {
            'sys': sys.getprofile(),
            'threading': threading.getprofile(),
        }
        sys.setprofile(tracefunc)
        threading.setprofile(tracefunc)
        _FUNCTION_CALL_TRACE_ENABLED = True
        target_logger.info(
            'Function call tracing enabled',
            extra={
                'event': 'function.trace.enabled',
                'payload': {
                    'packages': list(prefixes),
                    'excludes': list(excludes),
                },
            },
        )


def disable_function_call_logging() -> None:
    global _FUNCTION_CALL_TRACE_ENABLED, _FUNCTION_CALL_TRACE_PREVIOUS
    with _FUNCTION_CALL_TRACE_LOCK:
        if not _FUNCTION_CALL_TRACE_ENABLED:
            return
        prev_sys = _FUNCTION_CALL_TRACE_PREVIOUS.get('sys')
        prev_threading = _FUNCTION_CALL_TRACE_PREVIOUS.get('threading')
        sys.setprofile(prev_sys)
        threading.setprofile(prev_threading)
        _FUNCTION_CALL_TRACE_ENABLED = False
        _FUNCTION_CALL_TRACE_PREVIOUS = {'sys': None, 'threading': None}


def is_function_call_logging_enabled() -> bool:
    with _FUNCTION_CALL_TRACE_LOCK:
        return _FUNCTION_CALL_TRACE_ENABLED


def _should_enable_call_logging() -> bool:
    flag = os.getenv('PROJECTPLANNER_TRACE_CALLS')
    if flag is None:
        return True
    return flag.strip().lower() not in {'0', 'false', 'no', 'off'}


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
    normalized_stage = stage or "request"
    preview = _preview_text(prompt, _PROMPT_PREVIEW_LIMIT)
    truncated = len(prompt) > _PROMPT_PREVIEW_LIMIT
    payload: MutableMapping[str, Any] = {
        "agent": agent,
        "role": role,
        "stage": normalized_stage,
        "chars": len(prompt),
        "preview": preview,
        "truncated": truncated,
    }
    metadata_payload = _sanitize(metadata) if metadata else None
    if model:
        payload["model"] = model
    if metadata_payload:
        payload["metadata"] = metadata_payload
    audit_entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "role": role,
        "stage": normalized_stage,
        "chars": len(prompt),
        "preview": preview,
        "truncated": truncated,
        "prompt": prompt,
    }
    if run_id:
        audit_entry["run_id"] = run_id
    if model:
        audit_entry["model"] = model
    if metadata_payload:
        audit_entry["metadata"] = metadata_payload
    _append_prompt_audit(audit_entry)
    target.info(
        "%s prompt for %s (%d chars)",
        normalized_stage.capitalize(),
        agent,
        len(prompt),
        extra={
            "run_id": run_id,
            "event": f"prompt.{normalized_stage}",
            "payload": payload,
            "log_type": "prompts",
        },
    )

if _should_enable_call_logging():
    try:
        enable_function_call_logging()
    except Exception:
        logging.getLogger(_FUNCTION_CALL_TRACE_LOGGER_NAME).exception(
            "Failed to enable function call logging.",
            extra={"event": "function.trace.error"},
        )


__all__ = [
    "configure_logging",
    "disable_function_call_logging",
    "enable_function_call_logging",
    "get_log_manager",
    "get_logger",
    "get_prompt_audit_path",
    "is_function_call_logging_enabled",
    "log_prompt",
]

