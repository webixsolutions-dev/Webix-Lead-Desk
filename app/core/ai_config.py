"""Select task-specific model pricing without reusing another model's rates."""

from typing import Literal

from app.core.config import Settings


def task_settings(settings: Settings, task: Literal["audit", "draft"]) -> Settings:
    """Use an explicit model and its own rates, or the full legacy configuration."""
    model = getattr(settings, f"openai_{task}_model")
    if not model:
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
