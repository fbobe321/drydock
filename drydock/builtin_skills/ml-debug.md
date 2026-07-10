---
name: ml-debug
description: Diagnose and fix a broken PyTorch training run (NaN loss, not learning, shapes)
---
Diagnose and FIX the broken training: $ARGS

Be systematic — reproduce first, read the exact symptom, then:
- Loss NaN/Inf → LR too high (lower it), log/sqrt of ≤0, divide-by-zero, unnormalized
  inputs, or NaNs in data. Add `torch.autograd.set_detect_anomaly(True)` and
  `nn.utils.clip_grad_norm_`.
- Loss flat / not learning → missing `opt.zero_grad()` or `opt.step()`; wrong loss
  (softmax BEFORE CrossEntropyLoss); LR too low; model not in `train()`; frozen params;
  labels/logits misaligned. Print the loss and a param's grad-norm each step.
- Shape mismatch → print `.shape` at each layer; CrossEntropyLoss wants logits (N,C) and
  Long targets (N,).
- Train↑ val↓ → overfitting; regularize / augment / early-stop.
Overfit a tiny batch first to isolate the bug. Fix, rerun, and confirm it converges and
meets the target before finishing.
