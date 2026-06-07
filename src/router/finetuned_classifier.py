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
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..core.models import Classification, Intent

_LABELS = [i.value for i in Intent]
DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"

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


def _safe_extract_zip(zip_path: Path, out_dir: Path) -> None:
    """Extract a local model zip without allowing paths outside ``out_dir``."""
    out_root = out_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (out_root / member.filename).resolve()
            if target != out_root and out_root not in target.parents:
                raise ValueError(f"unsafe path in model zip: {member.filename!r}")
        zf.extractall(out_root)


def _model_root(path: Path) -> Path:
    """Return the directory that actually contains model/adapter files."""
    if (path / "adapter_config.json").exists() or (path / "config.json").exists():
        return path
    for child in path.iterdir():
        if child.is_dir() and (
            (child / "adapter_config.json").exists() or (child / "config.json").exists()
        ):
            return child
    return path


def _materialize_model_path(
    model_path: str, tempdirs: list[tempfile.TemporaryDirectory]
) -> str:
    """Return a loadable model path, extracting local zip adapters if needed."""
    path = Path(model_path).expanduser()
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="relayops-intent-lora-")
        tempdirs.append(tmp)
        _safe_extract_zip(path, Path(tmp.name))
        return str(_model_root(Path(tmp.name)))
    if path.exists():
        return str(path)
    return model_path


def _model_input_device(model: Any) -> Any:
    """Find the device for input tensors across Transformers and PEFT wrappers."""
    device = getattr(model, "device", None)
    if device is not None:
        return device
    try:
        return next(model.parameters()).device
    except (AttributeError, StopIteration):
        return None


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
        base_model: str | None = None,
        max_new_tokens: int = 24,
    ) -> None:
        import torch  # noqa: F401  (validates the runtime is present)
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # path may be a local directory OR a Hugging Face Hub repo id — both are
        # accepted by from_pretrained.
        raw_path = model_path or os.environ.get("RELAYOPS_INTENT_MODEL")
        self._tempdirs: list[tempfile.TemporaryDirectory] = []
        path = _materialize_model_path(raw_path, self._tempdirs) if raw_path else None
        base_override = base_model or os.environ.get("RELAYOPS_INTENT_BASE_MODEL")
        self._max_new_tokens = max_new_tokens

        if path:
            adapter_config = None
            try:
                # Try as a LoRA adapter over the base model first.
                from peft import PeftConfig, PeftModel

                adapter_config = PeftConfig.from_pretrained(path)
            except Exception as e:
                if (Path(path).exists() and (Path(path) / "adapter_config.json").exists()):
                    raise RuntimeError(f"could not read LoRA adapter config from {path!r}") from e

            if adapter_config is not None:
                adapter_base = getattr(adapter_config, "base_model_name_or_path", None)
                base_name = base_override or adapter_base or DEFAULT_BASE_MODEL
                try:
                    self._tokenizer = AutoTokenizer.from_pretrained(path)
                except Exception:
                    self._tokenizer = AutoTokenizer.from_pretrained(base_name)

                base = AutoModelForCausalLM.from_pretrained(base_name, device_map="auto")
                try:
                    self._model = PeftModel.from_pretrained(base, path)
                except Exception as e:
                    raise RuntimeError(
                        f"failed to load LoRA adapter {path!r} over base {base_name!r}"
                    ) from e
            else:
                # Otherwise treat the path/id as a full/merged model.
                self._tokenizer = AutoTokenizer.from_pretrained(path)
                self._model = AutoModelForCausalLM.from_pretrained(path, device_map="auto")
        else:
            # No fine-tune available -> base model (still functional, weaker).
            base_name = base_override or DEFAULT_BASE_MODEL
            self._tokenizer = AutoTokenizer.from_pretrained(base_name)
            self._model = AutoModelForCausalLM.from_pretrained(base_name, device_map="auto")

        self._model.eval()

        # Deterministic decoding for classification. Clearing the sampling params
        # also silences "generation flags are not valid" warnings (Qwen's config
        # ships temperature/top_p/top_k, which are ignored when do_sample=False).
        gc = getattr(self._model, "generation_config", None)
        if gc is not None:
            gc.do_sample = False
            gc.temperature = None
            gc.top_p = None
            gc.top_k = None
            # Qwen ships max_length=32768; we control length via max_new_tokens, so
            # clear it to avoid the "both max_new_tokens and max_length set" warning
            # printed on every prediction.
            gc.max_length = None

    def classify(self, text: str) -> Classification:
        import math

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        # return_dict=True gives input_ids + attention_mask (avoids the no-mask
        # warning and ambiguous padding when pad_token == eos_token).
        enc = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
        device = _model_input_device(self._model)
        if device is not None:
            enc = {k: v.to(device) for k, v in enc.items()}
        input_len = enc["input_ids"].shape[1]

        gen = self._model.generate(
            **enc,
            max_new_tokens=self._max_new_tokens,
            do_sample=False,
            return_dict_in_generate=True,
            output_scores=True,
            pad_token_id=self._tokenizer.eos_token_id,
        )
        new_tokens = gen.sequences[0][input_len:]
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
