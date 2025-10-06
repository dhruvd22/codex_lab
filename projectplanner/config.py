"""Central configuration helpers for The Coding Conductor defaults."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

LOGGER = logging.getLogger(__name__)

ENV_PREFIX = "CODING_CONDUCTOR"
LEGACY_ENV_PREFIX = "PROJECTPLANNER"


def get_setting(name: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch configuration values honoring new and legacy environment prefixes."""

    primary = os.getenv(f"{ENV_PREFIX}_{name}")
    if primary is not None:
        return primary
    legacy = os.getenv(f"{LEGACY_ENV_PREFIX}_{name}")
    if legacy is not None:
        return legacy
    return default


def resolve_env_key(name: str) -> str:
    """Return the environment variable key currently in use for `name`."""

    primary = f"{ENV_PREFIX}_{name}"
    if os.getenv(primary) is not None:
        return primary
    legacy = f"{LEGACY_ENV_PREFIX}_{name}"
    if os.getenv(legacy) is not None:
        return legacy
    return primary


MAX_COMPLETION_TOKENS_DEFAULT = 16384
_COMPLETION_TOKEN_SUFFIXES = (
    "MAX_COMPLETION_TOKENS",
    "COORDINATOR_MAX_COMPLETION_TOKENS",
)


@lru_cache()
def get_max_completion_tokens() -> int:
    """Resolve the completion token ceiling, honoring environment overrides."""

    for suffix in _COMPLETION_TOKEN_SUFFIXES:
        raw_value = get_setting(suffix)
        if not raw_value:
            continue
        env_key = resolve_env_key(suffix)
        try:
            parsed = int(raw_value)
        except ValueError:
            LOGGER.warning(
                "Ignoring invalid %s=%r; expected positive integer.",
                env_key,
                raw_value,
                extra={"event": "config.invalid_max_completion_tokens", "env_var": env_key},
            )
            continue
        if parsed > 0:
            return parsed
        LOGGER.warning(
            "Ignoring non-positive %s=%r; expected positive integer.",
            env_key,
            raw_value,
            extra={"event": "config.invalid_max_completion_tokens", "env_var": env_key},
        )
    return MAX_COMPLETION_TOKENS_DEFAULT


MAX_COMPLETION_TOKENS = get_max_completion_tokens()
