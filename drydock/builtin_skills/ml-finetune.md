---
name: ml-finetune
description: Fine-tune a model — full or LoRA (peft) — to a target metric
---
Fine-tuning task: $ARGS

1. Load the base/pretrained model and inspect it (`model.named_modules()`), and load
   the fine-tune data (with a held-out eval split).
2. Choose the method:
   - LoRA (train ~0.1% of params): `from peft import LoraConfig, get_peft_model`;
     r=8, lora_alpha=16, lora_dropout=0.05, target_modules = the attention linear
     layers you found; `get_peft_model(...)`; call `print_trainable_parameters()` to
     CONFIRM only adapters train. Save with `model.save_pretrained('adapter')`.
   - FULL: train all params with a SMALL LR (2e-5–5e-5), AdamW, a few epochs; freeze
     anything that should stay fixed. `model.save_pretrained(dir)`.
3. Train, evaluating on the held-out set; small LR + few epochs — don't destroy the
   pretrained features. Report the eval metric and save exactly what's requested.
