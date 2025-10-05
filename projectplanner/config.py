"""Central configuration helpers for Projectplanner defaults."""
from __future__ import annotations

import logging
import os
from functools import lru_cache

LOGGER = logging.getLogger(__name__)

MAX_COMPLETION_TOKENS_DEFAULT = 4096
_COMPLETION_TOKEN_ENV_KEYS = (
    "PROJECTPLANNER_MAX_COMPLETION_TOKENS",
    "PROJECTPLANNER_COORDINATOR_MAX_COMPLETION_TOKENS",
)


@lru_cache()
def get_max_completion_tokens() -> int:
    """Resolve the completion token ceiling, honoring environment overrides."""
    for key in _COMPLETION_TOKEN_ENV_KEYS:
        raw_value = os.getenv(key)
        if not raw_value:
            continue
        try:
            parsed = int(raw_value)
        except ValueError:
            LOGGER.warning(
                "Ignoring invalid %s=%r; expected positive integer.",
                key,
                raw_value,
                extra={"event": "config.invalid_max_completion_tokens", "env_var": key},
            )
            continue
        if parsed > 0:
            return parsed
        LOGGER.warning(
            "Ignoring non-positive %s=%r; expected positive integer.",
            key,
            raw_value,
            extra={"event": "config.invalid_max_completion_tokens", "env_var": key},
        )
    return MAX_COMPLETION_TOKENS_DEFAULT


MAX_COMPLETION_TOKENS = get_max_completion_tokens()
