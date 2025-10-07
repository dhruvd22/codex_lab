"""Configuration helpers for The Coding Orchestrator."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from projectplanner.config import MAX_COMPLETION_TOKENS as CONDUCTOR_MAX_COMPLETION_TOKENS
from projectplanner.config import get_setting as conductor_get_setting

LOGGER = logging.getLogger(__name__)

ENV_PREFIX = "CODING_ORCHESTRATOR"
LEGACY_ENV_PREFIX = "CODING_ORCHESTRATOR"

DEFAULT_SUMMARY_MODEL = "gpt-5.1-mini"
DEFAULT_MILESTONE_MODEL = "gpt-5.1-mini"
DEFAULT_PROMPT_MODEL = "gpt-5.1"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_COMPLETION_TOKENS = CONDUCTOR_MAX_COMPLETION_TOKENS


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    primary = os.getenv(f"{ENV_PREFIX}_{name}")
    if primary is not None:
        return primary
    legacy = os.getenv(f"{LEGACY_ENV_PREFIX}_{name}")
    if legacy is not None:
        return legacy
    return conductor_get_setting(name, default)


def get_setting(name: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch orchestrator configuration honoring dedicated prefixes."""

    return _get_env(name, default)


@lru_cache()
def get_summary_model() -> str:
    return get_setting("SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL) or DEFAULT_SUMMARY_MODEL


@lru_cache()
def get_milestone_model() -> str:
    return get_setting("MILESTONE_MODEL", DEFAULT_MILESTONE_MODEL) or DEFAULT_MILESTONE_MODEL


@lru_cache()
def get_prompt_model() -> str:
    return get_setting("PROMPT_MODEL", DEFAULT_PROMPT_MODEL) or DEFAULT_PROMPT_MODEL


def _parse_float(value: Optional[str], fallback: float) -> float:
    if not value:
        return fallback
    try:
        parsed = float(value)
    except ValueError:
        LOGGER.warning(
            "Invalid float for %s temperature; using fallback %.2f",
            ENV_PREFIX,
            fallback,
            extra={"event": "orchestrator.config.invalid_temperature", "value": value},
        )
        return fallback
    return max(0.0, min(parsed, 1.0))


@lru_cache()
def get_temperature() -> float:
    return _parse_float(get_setting("TEMPERATURE"), DEFAULT_TEMPERATURE)


def _parse_int(value: Optional[str], fallback: int) -> int:
    if not value:
        return fallback
    try:
        parsed = int(value)
    except ValueError:
        LOGGER.warning(
            "Invalid integer for %s max tokens; using fallback %d",
            ENV_PREFIX,
            fallback,
            extra={"event": "orchestrator.config.invalid_max_tokens", "value": value},
        )
        return fallback
    if parsed <= 0:
        return fallback
    return parsed


@lru_cache()
def get_max_completion_tokens() -> int:
    return _parse_int(get_setting("MAX_COMPLETION_TOKENS"), DEFAULT_MAX_COMPLETION_TOKENS)


__all__ = [
    "get_setting",
    "get_summary_model",
    "get_milestone_model",
    "get_prompt_model",
    "get_temperature",
    "get_max_completion_tokens",
]
