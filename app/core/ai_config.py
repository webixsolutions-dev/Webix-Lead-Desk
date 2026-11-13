"""Select task-specific model pricing without reusing another model's rates."""

from typing import Literal

from app.core.config import Settings


def search_settings(settings: Settings) -> Settings:
    """Allow dedicated workers to run budgeted web searches with the workspace key."""
    if settings.openai_search_enabled and not settings.openai_enabled:
        return settings.model_copy(
            update={
                "openai_enabled": True,
                "openai_model": "gpt-6-luna",
                "openai_input_usd_micro_per_million": 100_000,
                "openai_output_usd_micro_per_million": 500_000,
                "openai_max_output_tokens": settings.openai_search_max_output_tokens,
            }
        )
    return task_settings(settings, "audit").model_copy(
        update={"openai_max_output_tokens": settings.openai_search_max_output_tokens}
    )


def task_settings(settings: Settings, task: Literal["audit", "draft"]) -> Settings:
    """Use an explicit model and its own rates, or the full legacy configuration."""
    model = getattr(settings, f"openai_{task}_model")
    if not model or (
        settings.openai_model and f"openai_{task}_model" not in settings.model_fields_set
    ):
        return settings
    return settings.model_copy(
        update={
            "openai_model": model,
            "openai_input_usd_micro_per_million": getattr(
                settings, f"openai_{task}_input_usd_micro_per_million"
            ),
            "openai_output_usd_micro_per_million": getattr(
                settings, f"openai_{task}_output_usd_micro_per_million"
            ),
        }
    )
