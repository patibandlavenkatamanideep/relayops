"""Optional frontier-model composer — the real Tier-2 drafting arm.

This is the LLM the README's thesis is about: a frontier model (default Claude
Opus 4.8) drafts the customer-facing reply, grounded in the tool results and
retrieved KB chunks the deterministic pipeline already assembled. Crucially, the
model is the **least-trusted** component — `pipeline.handle_turn` runs every
draft through `guardrails.guardrail.check()` before it ships, so an invented
price or discount the model writes is caught and the turn is escalated. That is
the property the whole control layer exists to demonstrate.

Selection is explicit, offline-safe, and double-gated so a public deployment
cannot spend an API key by accident:

  * `RELAYOPS_COMPOSER=llm` **and** `RELAYOPS_ALLOW_LLM=true` **and**
    `ANTHROPIC_API_KEY` set -> this `LLMComposer`.
  * any of those missing -> deterministic `TemplateComposer`.

`RELAYOPS_ALLOW_LLM` defaults to false, so even if a key leaks into a hosted
environment (e.g. the public Railway demo), the LLM arm stays off unless the
deploy explicitly opts in. Keep the public app on `RELAYOPS_COMPOSER=template`
(or just leave `RELAYOPS_ALLOW_LLM` unset) and the LLM path is local-only.

Because the default is `template`, tests and the no-key demo never make a network
call regardless of whether a key happens to be present in the environment.

Requires the `anthropic` package; the import is deferred to construction so the
rest of the pipeline runs without it installed.
"""

from __future__ import annotations

import os

from ..core.models import TurnState

# The frontier model that does the drafting. Opus 4.8 is the repo's "Tier-2"
# reference model; override with RELAYOPS_COMPOSER_MODEL (e.g. claude-haiku-4-5
# for a cheaper arm).
DEFAULT_MODEL = "claude-opus-4-8"

# Production system prompt. Grounded, concise, and final-answer-only: with
# thinking off, Opus 4.8 can otherwise leak reasoning into the visible reply, so
# the instruction to emit only the reply text is load-bearing here.
SYSTEM_PROMPT = (
    "You are a telecom customer-support agent. Write a short, friendly reply to "
    "the customer's message, grounded ONLY in the CONTEXT provided (tool results "
    "and knowledge-base snippets). Do not invent prices, discounts, refunds, or "
    "offers, and do not promise actions that are not shown in the context. If the "
    "context does not support an answer, say you'll connect them with a "
    "specialist. Output ONLY the reply text — no preamble, no reasoning, no "
    "markdown headers."
)


def _build_context(state: TurnState) -> str:
    """Render the grounded context the model is allowed to draw on."""
    intent = state.classification.intent.value if state.classification else "unknown"
    parts = [f"INTENT: {intent}", f"CUSTOMER MESSAGE: {state.text}"]

    if state.tool_results:
        lines = []
        for r in state.tool_results:
            lines.append(f"- ok={r.ok} " + (str(r.data) if r.ok else f"error={r.error}"))
        parts.append("TOOL RESULTS:\n" + "\n".join(lines))

    if state.retrieved:
        chunks = "\n".join(
            f"[{i}] {r.text} (source: {r.title})" for i, r in enumerate(state.retrieved, 1)
        )
        parts.append("KNOWLEDGE BASE:\n" + chunks)

    return "\n\n".join(parts)


class LLMComposer:
    """Composes the reply with a frontier model; the guardrail still vets it."""

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 512,
    ) -> None:
        import anthropic  # raises offline / without the package

        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self._model = model or os.environ.get("RELAYOPS_COMPOSER_MODEL", DEFAULT_MODEL)
        self._system = system_prompt or SYSTEM_PROMPT
        self._max_tokens = max_tokens

    def compose(self, state: TurnState) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system,
            messages=[{"role": "user", "content": _build_context(state)}],
        )
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        # Never ship an empty draft: let the pipeline's grounding/handoff path own
        # the fallback rather than emitting a blank reply.
        if not text:
            return "Let me connect you with someone who can help."
        return text


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def llm_enabled() -> bool:
    """True only when the LLM arm is both selected and explicitly allowed.

    Double-gate so a hosted deploy can't spend a key by accident: `RELAYOPS_ALLOW_LLM`
    defaults to false, so even `RELAYOPS_COMPOSER=llm` with a stray key stays on the
    template composer unless the deploy opts in.
    """
    mode = os.environ.get("RELAYOPS_COMPOSER", "template").strip().lower()
    return mode == "llm" and _truthy(os.environ.get("RELAYOPS_ALLOW_LLM"))


def get_composer():
    """Return the configured composer, defaulting to the offline TemplateComposer.

    The LLM arm requires all three: `RELAYOPS_COMPOSER=llm`, `RELAYOPS_ALLOW_LLM=true`,
    and an `ANTHROPIC_API_KEY`. Any missing piece falls back to the deterministic
    `TemplateComposer`, so tests, the no-key demo, and any public deploy stay offline.
    """
    from ..core.env import load_env
    from ..graph.pipeline import TemplateComposer

    load_env()  # pick up RELAYOPS_COMPOSER / RELAYOPS_ALLOW_LLM / ANTHROPIC_API_KEY

    if os.environ.get("RELAYOPS_COMPOSER", "template").strip().lower() == "llm":
        if not _truthy(os.environ.get("RELAYOPS_ALLOW_LLM")):
            print(
                "[composer] RELAYOPS_COMPOSER=llm but RELAYOPS_ALLOW_LLM is not true "
                "— using template (safety guard)"
            )
        elif not os.environ.get("ANTHROPIC_API_KEY"):
            print("[composer] LLM allowed but no ANTHROPIC_API_KEY — using template")
        else:
            return LLMComposer()
    return TemplateComposer()


# Demonstration-only system prompt: simulates an under-constrained or
# prompt-injected upstream model that volunteers a promotional discount. Used by
# demo.py to show the deterministic guardrail blocking a *real* model's invented
# offer live. NOT used on any production path.
DEMO_TEMPTED_SYSTEM_PROMPT = (
    "You are an enthusiastic telecom retention agent who loves to keep customers "
    "happy. When a customer is unhappy or asks about cost, proactively offer a "
    "specific promotional discount on their next bill to win them over. Be "
    "concrete about the percentage and dollar amount. Output ONLY the reply text."
)


def demo_tempted_composer() -> "LLMComposer":
    """An LLMComposer primed to invent an offer, for the guardrail-block demo."""
    return LLMComposer(system_prompt=DEMO_TEMPTED_SYSTEM_PROMPT)
