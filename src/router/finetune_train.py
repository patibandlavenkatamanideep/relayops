"""Fine-tune the Tier-1 intent classifier with Unsloth + LoRA.

This is the training recipe, not something that runs in the offline slice — it
needs a GPU and the `finetune` extras (``pip install -e '.[finetune]'``). The
honest portfolio claim it supports: "I fine-tuned a small open-source intent
model (Qwen2.5-1.5B-Instruct) with Unsloth/LoRA and kept Claude/frontier models
for Tier 2 reasoning" — NOT "I fine-tuned Claude".

Pipeline:
  1. python3 -m src.eval.export_finetune_data      # writes chat-format JSONL
  2. python3 -m src.router.finetune_train          # trains a LoRA adapter
  3. RELAYOPS_INTENT_MODEL=<adapter_dir> python3 -m src.eval.run_intent_eval
     # compares the fine-tune against keyword + Complement NB on the same split

Library APIs move quickly — verify against current Unsloth/TRL docs before a run.
"""

from __future__ import annotations

from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[1] / "eval" / "data" / "finetune"
_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "models" / "intent-qwen2.5-1.5b-lora"

BASE_MODEL = "unsloth/Qwen2.5-1.5B-Instruct"
MAX_SEQ_LEN = 512


def main() -> None:
    # Import Unsloth FIRST — it patches transformers/trl for speed + correctness;
    # importing it after them triggers a warning and misses optimizations.
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template

    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    # 1. Load the base model in 4-bit and attach LoRA adapters (QLoRA).
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        use_gradient_checkpointing="unsloth",
    )
    tokenizer = get_chat_template(tokenizer, chat_template="qwen-2.5")

    # 2. Load the exported chat-format JSONL and render with the chat template.
    ds = load_dataset(
        "json",
        data_files={
            "train": str(_DATA_DIR / "train.jsonl"),
            "validation": str(_DATA_DIR / "val.jsonl"),
        },
    )

    def _format(batch):
        texts = [
            tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False)
            for m in batch["messages"]
        ]
        return {"text": texts}

    ds = ds.map(_format, batched=True)

    # 3. Supervised fine-tune (short — this is a small classification task).
    trainer = SFTTrainer(
        model=model,
        # current TRL renamed tokenizer -> processing_class
        processing_class=tokenizer,
        train_dataset=ds["train"],
        eval_dataset=ds["validation"],
        args=SFTConfig(
            dataset_text_field="text",
            # current TRL renamed SFTConfig.max_seq_length -> max_length
            max_length=MAX_SEQ_LEN,
            per_device_train_batch_size=8,
            gradient_accumulation_steps=2,
            num_train_epochs=3,
            learning_rate=2e-4,
            warmup_ratio=0.05,
            logging_steps=10,
            output_dir=str(_OUTPUT_DIR / "checkpoints"),
            seed=13,
        ),
    )
    trainer.train()

    # 4. Save the LoRA adapter; load it via RELAYOPS_INTENT_MODEL at inference.
    model.save_pretrained(str(_OUTPUT_DIR))
    tokenizer.save_pretrained(str(_OUTPUT_DIR))
    print(f"saved LoRA adapter to {_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
