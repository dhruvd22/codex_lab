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

def extract_choice_metadata(response: Any) -> Dict[str, Any]:
    """Collect useful metadata from the first choice of a completion response."""

    metadata: Dict[str, Any] = {}
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, Mapping):
        choices = response.get("choices")
    choice = None
    if isinstance(choices, Sequence) and choices:
        choice = choices[0]
    if choice is not None:
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is None and isinstance(choice, Mapping):
            finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            metadata["finish_reason"] = finish_reason
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, Mapping):
            message = choice.get("message")
        if message is not None:
            refusal = getattr(message, "refusal", None)
            if refusal is None and isinstance(message, Mapping):
                refusal = message.get("refusal")
            if refusal:
                metadata["refusal"] = refusal
            role = getattr(message, "role", None)
            if role is None and isinstance(message, Mapping):
                role = message.get("role")
            if role:
                metadata.setdefault("role", role)
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage")
    if usage:
        usage_dict: Dict[str, Any] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if value is None and isinstance(usage, Mapping):
                value = usage.get(key)
            if value is not None:
                usage_dict[key] = value
        if usage_dict:
            metadata["usage"] = usage_dict
    response_id = getattr(response, "id", None)
    if response_id is None and isinstance(response, Mapping):
        response_id = response.get("id")
    if response_id:
        metadata["response_id"] = response_id
    model = getattr(response, "model", None)
    if model is None and isinstance(response, Mapping):
        model = response.get("model")
    if model:
        metadata.setdefault("model", model)
    return metadata

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
        {"max_output_tokens": max_tokens},
        {"extra_body": {"max_output_tokens": max_tokens}},
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
                if (
                    "use 'max_output_tokens'" in lowered
                    and ("max_completion_tokens" in extra or "extra_body" in extra)
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

