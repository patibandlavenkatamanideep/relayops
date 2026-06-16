"""Reply composers — the (least-trusted) drafting step of the pipeline.

The pipeline treats the composer as the one component that may hallucinate: the
deterministic guardrail vets whatever it returns. ``TemplateComposer`` (in
``graph/pipeline.py``) is the offline, deterministic default; ``LLMComposer``
here is the optional frontier-model arm. ``get_composer`` selects between them
from the environment so tests and the default demo stay offline.
"""

from .llm_composer import LLMComposer, get_composer

__all__ = ["LLMComposer", "get_composer"]
