"""Provider-independent generation results and expected article failures."""
from dataclasses import dataclass
from datetime import datetime


class SummaryValidationError(ValueError):
    """Input or generated output is unusable."""


class ModelsUnavailable(RuntimeError):
    """No configured model can currently produce a summary."""


@dataclass(frozen=True)
class GeneratedSummary:
    content: str
    provider: str
    model: str
    prompt_version: str
    generated_at: datetime
