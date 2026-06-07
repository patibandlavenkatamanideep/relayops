---
license: apache-2.0
base_model: Qwen/Qwen2.5-1.5B-Instruct
library_name: peft
tags:
  - qwen
  - lora
  - unsloth
  - peft
  - text-classification
  - intent-classification
  - telecom
  - relayops
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
- **Base (inference):** `Qwen/Qwen2.5-1.5B-Instruct` — RelayOps loads the adapter
  over this full-precision base.
- **Trained on:** `unsloth/qwen2.5-1.5b-instruct-unsloth-bnb-4bit` (Unsloth 4-bit
  QLoRA); the adapter loads on either base.
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

## Out-of-scope use
Do **not** use this model to make billing, payment, plan-change, access-control,
offer, or customer-eligibility decisions. It predicts **intent only**; those
decisions belong to RelayOps' deterministic access gate, router, and human
escalation. It is not a general-purpose intent model — it is trained on six
telecom intents over synthetic data.

## Evaluation
| Split | Accuracy | Macro-F1 |
|---|---:|---:|
| Held-out (seed-13, group-aware, 726 ex) | 0.999 | 0.999 |
| Legacy hand-written adversarial / paraphrase (24 ex) | 0.958 | 0.804 |
| v1.2 hand-written adversarial / safety set (100 ex) | pending rerun | pending rerun |

Baselines on the legacy 24-case adversarial set: keyword 0.250 acc; Complement
NB 0.667 acc. On the v1.2 100-case adversarial set, keyword is 0.490 acc,
Complement NB is 0.660 acc, and safe calibrated NB is 0.880 acc.

**Honest caveat.** The held-out set is template-generated synthetic data, so high
in-distribution scores are expected even with anti-leakage splits. Treat the
held-out number as routing-slice validation, not a production benchmark; the
adversarial set is the truer generalization signal. The v1.2 repo now has a
larger 100-case adversarial/safety set; the Qwen rerun is intentionally marked
pending until measured on that exact set.

## Limitations
- Trained on synthetic telecom data for six intents; not a general intent model.
- Out-of-taxonomy / mixed-intent / abusive messages map to `unknown`, which RelayOps
  escalates — the model does not resolve them.
- Qwen has not yet been rerun on RelayOps' newer 100-case adversarial/safety set.

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
