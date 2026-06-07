---
license: apache-2.0
base_model: Qwen/Qwen2.5-1.5B-Instruct
library_name: peft
tags:
  - text-classification
  - intent-classification
  - lora
  - telecom
---

# RelayOps Intent Classifier (Qwen2.5-1.5B LoRA)

Tier-1 intent classifier for [RelayOps](https://github.com/patibandlavenkatamanideep/relayops),
a production-shaped telecom customer-service agent. It maps a customer message to
exactly one of six intents and emits **strict JSON, intent only**.

This is a **small open-source model fine-tuned with LoRA — it is not Claude.**
RelayOps keeps frontier (Claude) models for Tier-2 reasoning; this cheap classifier
exists to handle the easy-majority of routing so the frontier model is reserved for
hard / low-confidence / action cases.

## Model details
- **Base:** `Qwen/Qwen2.5-1.5B-Instruct`
- **Method:** Unsloth + LoRA/QLoRA (adapter only)
- **Task:** single-label intent classification, output `{"intent": "<label>"}`
- **Labels:** `reset_device`, `device_status`, `device_faq`, `billing`, `greeting`, `unknown`
- **Dataset:** 2,400 examples, 400 per intent, curated seeds + deterministic
  template paraphrases, with `group` ids so paraphrase families don't leak across
  splits.

## Intended use & scope
- **Input:** one customer chat message. **Output:** one intent label as JSON.
- The model classifies intent **only**. It does **not** decide risk, route,
  permissions, billing, offers, or account access — those are enforced by RelayOps'
  deterministic access gate and router (policy stays out of model weights).
- Confidence is read from the model's own token probabilities at inference, not
  baked into labels.

## Evaluation
| Split | Accuracy | Macro-F1 |
|---|---:|---:|
| Held-out (seed-13, group-aware, 726 ex) | 0.999 | 0.999 |
| Hand-written adversarial / paraphrase (24 ex) | 0.958 | 0.804 |

Baselines on the same sets: keyword 0.506 / 0.250 acc; Complement NB 0.933 / 0.667 acc.

**Honest caveat.** The held-out set is template-generated synthetic data, so high
in-distribution scores are expected even with anti-leakage splits. Treat the
held-out number as routing-slice validation, not a production benchmark; the
adversarial set is the truer generalization signal, and the adversarial macro-F1
(0.804 < 0.958 accuracy) shows the model is still uneven on the hardest classes.

## Limitations
- Trained on synthetic telecom data for six intents; not a general intent model.
- Out-of-taxonomy / mixed-intent / abusive messages map to `unknown`, which RelayOps
  escalates — the model does not resolve them.
- Adversarial set is small (24); per-class adversarial recall and a larger set are
  follow-ups.

## How to use (in RelayOps)
```bash
RELAYOPS_INTENT_MODEL=<this-repo-or-local-adapter-dir> \
  python -m src.eval.run_intent_eval
```
or in code:
```python
from src.router.registry import get_classifier
clf = get_classifier("finetuned")   # reads RELAYOPS_INTENT_MODEL
clf.classify("my internet is down")  # -> Classification(intent=reset_device, ...)
```

## Reproduce
Training recipe: `src/router/finetune_train.py` (Unsloth LoRA). Data export:
`src/eval/export_finetune_data.py`. Colab notebook:
`notebooks/finetune_intent_colab.ipynb`.
