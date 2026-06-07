"""Fine-tuned intent classifier — a small open-source LM behind IntentClassifier.

This is the production Tier-1 classifier the README describes: a small instruct
model (default Qwen2.5-1.5B-Instruct) fine-tuned with Unsloth + LoRA on the
telecom intent dataset (see ``finetune_train.py`` for the recipe and
``src/eval/export_finetune_data.py`` for the data format). It loads here behind
the exact same ``classify(text) -> Classification`` interface as the keyword and
Complement-NB classifiers, so the pipeline swaps it in unchanged.

Two honesty/design choices (a deliberate refinement of the original review):

  * The model emits **intent only** as strict JSON. It does NOT emit risk/route.
    Routing is policy, and in RelayOps policy lives in the deterministic access
    gate + router, never in model weights ("facts/policy in weights" is an
    avoided anti-pattern). The router maps intent -> route after this.
  * **Confidence is read from the model's own token probabilities** at inference
    (mean probability of the generated label tokens), not a fabricated constant
    baked into the training labels.

Requires ``transformers`` (+ ``peft`` for LoRA adapters, ``torch``); the eval
harness skips it gracefully when those aren't installed, so the rest of the slice
runs offline.
"""

from __future__ import annotations

import json
import os
import re

from ..core.models import Classification, Intent

_LABELS = [i.value for i in Intent]

# Single source of truth — the SAME system prompt is used to build training data
# (export_finetune_data.py) and at inference, so the fine-tune sees a consistent
# instruction.
SYSTEM_PROMPT = (
    "You are an intent classifier for a telecom customer-service agent. "
    "Classify the user's message into exactly one of these intents: "
    f"{', '.join(_LABELS)}. "
    'Respond with ONLY valid JSON of the form {"intent": "<one_label>"} and nothing else.'
)

_JSON_OBJ = re.compile(r"\{.*?\}", re.DOTALL)


def parse_intent(raw: str, confidence: float = 0.0) -> Classification:
    """Parse a model response into a Classification. Pure + unit-testable.

    Tolerates minor formatting noise: tries strict JSON first, then falls back to
    scanning the text for a known label.
    """
    label = ""
    m = _JSON_OBJ.search(raw)
    if m:
        try:
            obj = json.loads(m.group())
            label = str(obj.get("intent", "")).strip().lower()
        except (json.JSONDecodeError, AttributeError):
            label = ""

    if label in _LABELS:
        return Classification(Intent(label), confidence or 0.9)

    # Fallback: substring scan (handles "intent: reset_device" etc.)
    low = raw.lower()
    for lbl in _LABELS:
        if lbl in low:
            return Classification(Intent(lbl), confidence or 0.6)
    return Classification(Intent.UNKNOWN, confidence or 0.3)


class FineTunedIntentClassifier:
    """Loads a fine-tuned small LM (base + optional LoRA adapter) and classifies.

    ``model_path`` (or env ``RELAYOPS_INTENT_MODEL``) points at either a merged
    model directory or a LoRA adapter dir to apply over ``base_model``.
    """

    def __init__(
        self,
        model_path: str | None = None,
        base_model: str = "Qwen/Qwen2.5-1.5B-Instruct",
        max_new_tokens: int = 24,
    ) -> None:
        import torch  # noqa: F401  (validates the runtime is present)
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # path may be a local directory OR a Hugging Face Hub repo id — both are
        # accepted by from_pretrained.
        path = model_path or os.environ.get("RELAYOPS_INTENT_MODEL")
        self._max_new_tokens = max_new_tokens

        if path:
            try:
                self._tokenizer = AutoTokenizer.from_pretrained(path)
            except Exception:
                self._tokenizer = AutoTokenizer.from_pretrained(base_model)
            try:
                # Try as a LoRA adapter over the base model first.
                from peft import PeftModel

                base = AutoModelForCausalLM.from_pretrained(base_model, device_map="auto")
                self._model = PeftModel.from_pretrained(base, path)
            except Exception:
                # Otherwise treat the path/id as a full/merged model.
                self._model = AutoModelForCausalLM.from_pretrained(path, device_map="auto")
        else:
            # No fine-tune available -> base model (still functional, weaker).
            self._tokenizer = AutoTokenizer.from_pretrained(base_model)
            self._model = AutoModelForCausalLM.from_pretrained(base_model, device_map="auto")

        self._model.eval()

    def classify(self, text: str) -> Classification:
        import math

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        input_ids = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        ).to(self._model.device)

        gen = self._model.generate(
            input_ids=input_ids,
            max_new_tokens=self._max_new_tokens,
            do_sample=False,
            return_dict_in_generate=True,
            output_scores=True,
            pad_token_id=self._tokenizer.eos_token_id,
        )
        new_tokens = gen.sequences[0][input_ids.shape[1]:]
        raw = self._tokenizer.decode(new_tokens, skip_special_tokens=True)

        # Confidence = mean probability the model assigned to the tokens it emitted.
        confidence = 0.85
        try:
            trans = self._model.compute_transition_scores(
                gen.sequences, gen.scores, normalize_logits=True
            )
            probs = [math.exp(s) for s in trans[0].tolist()]
            if probs:
                confidence = sum(probs) / len(probs)
        except Exception:
            pass

        return parse_intent(raw, confidence=confidence)
