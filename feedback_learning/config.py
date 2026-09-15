"""Environment-backed feature flags for the feedback subsystem."""

import os


_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable using the project's common values."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def feedback_enabled() -> bool:
    """Whether user-facing feedback collection is available."""
    # Keep the historical behavior when older deployments have no new setting.
    return env_flag("FEEDBACK_ENABLED", default=True)


def feedback_learning_enabled() -> bool:
    """Whether feedback-derived learning may be generated and applied."""
    return feedback_enabled() and env_flag("FEEDBACK_LEARNING_ENABLED", default=False)
