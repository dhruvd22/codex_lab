"""Utility helpers for invoking OpenAI chat completions with backward compatibility."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

try:  # pragma: no cover - optional dependency guard
    from openai import BadRequestError
except Exception:  # pragma: no cover
    BadRequestError = Exception  # type: ignore[assignment]


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
    """

    if client is None:
        raise RuntimeError("OpenAI client is unavailable.")

    base_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
    }

    attempts = (
        {"max_completion_tokens": max_tokens},
        {"extra_body": {"max_completion_tokens": max_tokens}},
        {"max_tokens": max_tokens},
        {},  # final fallback relies on server defaults
    )

    last_error: Exception | None = None

    for extra in attempts:
        try:
            return client.chat.completions.create(  # type: ignore[attr-defined]
                **base_kwargs,
                **extra,
            )
        except TypeError as exc:  # unexpected keyword for this client version
            last_error = exc
            continue
        except BadRequestError as exc:  # type: ignore[misc]
            message = getattr(exc, "message", "") or str(exc)
            if (
                "Use 'max_completion_tokens' instead" in message
                and "max_tokens" in extra
            ):
                last_error = exc
                continue
            raise

    if last_error is not None:
        raise last_error

    raise RuntimeError("Failed to create chat completion with available parameters.")

