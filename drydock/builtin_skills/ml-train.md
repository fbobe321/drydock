---
name: ml-train
description: Build and train a PyTorch model on a dataset to a target metric
---
You are doing a machine-learning TRAINING task: $ARGS

Work in this order:
1. INSPECT the data first — load it, print shapes, dtypes, number of classes, and
   which splits exist (train/val/test). Never assume; look.
2. BUILD an appropriate model in PyTorch — a CNN for images, an MLP/Transformer for
   vectors/sequences. Keep it small unless the task demands otherwise.
3. Write a CORRECT training loop: AdamW, CrossEntropyLoss on RAW logits (no softmax),
   `opt.zero_grad()` every step, mini-batches via DataLoader, `model.train()` for
   training and `model.eval()` + `torch.no_grad()` for evaluation, and move BOTH the
   model and each batch to the same device. Seed torch for reproducibility.
4. Sanity-check: overfit a single tiny batch to near-zero loss first.
5. Track the train and validation metric each epoch; stop when the target is met.
6. Produce EXACTLY the artifacts the task asks for (file names, formats, order), then
   re-read the requirements and verify each one before you finish.
