"""Utility helpers for invoking OpenAI chat completions with backward compatibility."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

try:  # pragma: no cover - optional dependency guard
    from openai import BadRequestError
except Exception:  # pragma: no cover
    BadRequestError = Exception  # type: ignore[assignment]

def _coerce_content_fragment(value: Any) -> str:
    """Normalize structured message content into text segments."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("text", "content", "value", "message"):
            candidate = value.get(key)
            if candidate is not None:
                normalized = _coerce_content_fragment(candidate)
                if normalized:
                    return normalized
        content_type = value.get("type")
        if content_type in {"text", "output_text"} and "data" in value:
            normalized = _coerce_content_fragment(value.get("data"))
            if normalized:
                return normalized
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = [_coerce_content_fragment(item) for item in value]
        cleaned = [part for part in parts if part]
        if cleaned:
            return "\n".join(cleaned).strip()
        return ""
    for attr in ("text", "content", "value", "message"):
        if hasattr(value, attr):
            attr_value = getattr(value, attr)
            if attr_value:
                normalized = _coerce_content_fragment(attr_value)
                if normalized:
                    return normalized
    return ""


def extract_message_content(message: Any) -> str:
    """Extract textual content from a chat completion message across SDK variants."""
    if message is None:
        return ""
    primary = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
    normalized = _coerce_content_fragment(primary)
    if normalized:
        return normalized.strip()
    secondary = message.get("text") if isinstance(message, Mapping) else getattr(message, "text", None)
    normalized = _coerce_content_fragment(secondary)
    if normalized:
        return normalized.strip()
    return ""

def create_chat_completion(
    client: Any,
    *,
    model: str,
    messages: Sequence[Mapping[str, str]],
    temperature: float,
    max_tokens: int,
) -> Any:
    """Call chat.completions.create while supporting legacy parameter names.

    Newer OpenAI models expect `max_completion_tokens`, but some client versions
    still expose only `max_tokens`. We optimistically try the new parameter and
    fall back to alternative encodings when necessary.

    If a model rejects custom temperature values, we retry without overriding
    temperature so the server default (1) is applied.
    """

    if client is None:
        raise RuntimeError("OpenAI client is unavailable.")

    attempts = (
        {"max_completion_tokens": max_tokens},
        {"extra_body": {"max_completion_tokens": max_tokens}},
        {"max_tokens": max_tokens},
        {},  # final fallback relies on server defaults
    )

    normalized_messages = list(messages)
    include_temperature = True
    last_error: Exception | None = None

    while True:
        retry_without_temperature = False
        for extra in attempts:
            kwargs: Dict[str, Any] = {"model": model, "messages": normalized_messages}
            if include_temperature:
                kwargs["temperature"] = temperature
            try:
                return client.chat.completions.create(  # type: ignore[attr-defined]
                    **kwargs,
                    **extra,
                )
            except TypeError as exc:  # unexpected keyword for this client version
                last_error = exc
                continue
            except BadRequestError as exc:  # type: ignore[misc]
                message = getattr(exc, "message", "") or str(exc)
                lowered = message.lower()
                if include_temperature and "temperature" in lowered and "default (1)" in lowered:
                    last_error = exc
                    retry_without_temperature = True
                    break
                if (
                    "use 'max_completion_tokens' instead" in lowered
                    and "max_tokens" in extra
                ):
                    last_error = exc
                    continue
                raise
        if retry_without_temperature:
            include_temperature = False
            continue
        break

    if last_error is not None:
        raise last_error

    raise RuntimeError("Failed to create chat completion with available parameters.")

