"""Bundled technique recipes — compact "how to do X" references retrieved by task
text and injected into context, so a fixed local model has the *technique* a task
needs instead of guessing. Retrieval is keyword-overlap (stdlib, no embeddings):
cheap, deterministic, and good enough for a small curated set.

All content original to Drydock. Gated by config `recipes` (default on).
"""
from __future__ import annotations

import re

# (title, keywords, body). Keep bodies tight and COMMAND-first — the model needs
# the move, not an essay. Grounded in tbench failure categories we observed.
RECIPES: list[tuple[str, list[str], str]] = [
    ("Recover deleted / hidden / fragmented data from files",
     ["recover", "deleted", "forensic", "forensics", "password", "strings", "binary",
      "carve", "fragment", "hidden", "launchcode", "undelete", "disk", "raw"],
     "Deleted or embedded content often still sits in a file/blob as raw bytes.\n"
     "1. Search binary-safe for the pattern across everything:\n"
     "   grep -aroE 'PATTERN' /path   (grep -a treats binary as text)\n"
     "2. Pull printable strings and grep them — the target may be split across a\n"
     "   non-printable byte, so `strings` shows it on TWO adjacent lines:\n"
     "   strings BIGFILE | grep -A1 'MARKER'\n"
     "   Then CONCATENATE the fragments (strip the break) to rebuild the value.\n"
     "3. Inspect bytes to understand structure: xxd FILE | less  (or od -A x -t x1z).\n"
     "If a value has a known shape (e.g. 23 chars, starts 8XD ends W54), build the\n"
     "exact ERE: 8XD[A-Z0-9]{17}W54 — count the middle length precisely."),

    ("Remove a secret / file from ALL git history",
     ["git", "history", "secret", "sanitize", "remove", "rewrite", "filter", "purge",
      "credential", "commit", "bfg", "filter-branch", "filter-repo"],
     "Deleting a file in a new commit does NOT remove it from history. Rewrite it:\n"
     "  git filter-repo --path SECRET --invert-paths      # preferred if installed\n"
     "  git filter-repo --replace-text <(echo 'SECRET==>REDACTED')\n"
     "If filter-repo is absent, use the built-in (slower):\n"
     "  git filter-branch --force --index-filter \\\n"
     "    'git rm --cached --ignore-unmatch PATH' --prune-empty --tag-name-filter cat -- --all\n"
     "Then: rm -rf .git/refs/original; git reflog expire --expire=now --all; git gc --prune=now.\n"
     "Verify it's gone from every commit: git log --all --oneline -- PATH  (should be empty),\n"
     "and grep history: git rev-list --all | xargs -I{} git grep -I SECRET {} 2>/dev/null."),

    ("Fix NumPy 2.0 incompatibility",
     ["numpy", "np", "compat", "compatibility", "attributeerror", "deprecated",
      "alias", "float", "int", "bool", "migrate", "2.0"],
     "NumPy 2.0 removed the deprecated scalar aliases and renamed a few APIs:\n"
     "  np.float  -> float   (or np.float64)   np.int -> int (or np.int64)\n"
     "  np.bool   -> bool     np.object -> object   np.str -> str\n"
     "  np.NaN    -> np.nan   np.Inf -> np.inf      np.product -> np.prod\n"
     "  np.NINF   -> -np.inf  np.float_ -> np.float64   np.unicode_ -> np.str_\n"
     "  np.in1d   -> np.isin  np.round_ -> np.round\n"
     "Find them: grep -rnE 'np\\.(float|int|bool|object|str|NaN|Inf|product|in1d)\\b' .\n"
     "For C/Cython ext: rebuild against the installed NumPy headers and pin\n"
     "'oldest-supported-numpy' at build time; the ABI is forward-compatible from 2.0."),

    ("Build & install a Python C/Cython extension from source",
     ["cython", "extension", "compile", "build", "setup.py", "pyx", "c extension",
      "build_ext", "install", "source", "pip install"],
     "  pip install -e .                 # build + editable-install using pyproject/setup\n"
     "  python setup.py build_ext --inplace   # just compile the .so next to sources\n"
     "  pip install .                    # normal install\n"
     "If Cython isn't run: pip install cython numpy first, then rebuild. To force a\n"
     "clean rebuild: rm -rf build/ **/*.so **/*.c(generated) and repeat. Check the\n"
     "extension imports: python -c 'import pkg.mod'. Read the FIRST compiler error —\n"
     "usually a missing header (apt/pip a -dev package) or a NumPy API mismatch."),

    ("Create & verify a self-signed certificate with openssl",
     ["openssl", "certificate", "cert", "self-signed", "tls", "ssl", "x509", "key",
      "pem", "csr"],
     "One-shot self-signed cert + key (no prompts):\n"
     "  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \\\n"
     "    -days 365 -subj '/CN=dev-internal.company.local'\n"
     "Read fields back (scriptable, no extra deps):\n"
     "  openssl x509 -in cert.pem -noout -subject          # CN=...\n"
     "  openssl x509 -in cert.pem -noout -enddate          # notAfter=...\n"
     "  openssl x509 -in cert.pem -noout -dates -subject\n"
     "Prefer shelling out to `openssl` over a Python crypto lib — the grader's env\n"
     "may not have `cryptography` installed."),

    ("Parse logs / extract fields from text",
     ["log", "parse", "extract", "field", "grep", "awk", "sed", "regex", "count",
      "lines", "match", "column"],
     "  grep -c 'ERROR' file            # count matching LINES\n"
     "  grep -oE 'PATTERN' file | wc -l # count matching OCCURRENCES\n"
     "  awk -F',' '{print $3}' file     # 3rd CSV field\n"
     "  awk '{sum+=$1} END{print sum}'  # sum a column\n"
     "  sed -n '10,20p' file            # lines 10-20\n"
     "  grep -oE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' f   # emails\n"
     "Use grep -P for lookaround if available; -a for binary-safe; sort | uniq -c to tally."),

    ("Work with JSON / CSV / SQLite data files",
     ["json", "csv", "sqlite", "data", "parse", "query", "database", "db", "jq",
      "pandas", "table", "schema"],
     "JSON:  jq '.key[]' f.json    |  python -c 'import json;d=json.load(open(\"f\"))'\n"
     "CSV:   python -c 'import csv;[print(r) for r in csv.DictReader(open(\"f.csv\"))]'\n"
     "SQLite: sqlite3 db '.schema'     sqlite3 db 'SELECT * FROM t LIMIT 5;'\n"
     "        sqlite3 -header -csv db 'SELECT ...' > out.csv\n"
     "Prefer stdlib (csv, json, sqlite3) — always present. For big data, stream row\n"
     "by row instead of loading all into memory."),

    ("Always verify against the task's own check before finishing",
     ["test", "verify", "check", "eval", "grader", "pass", "requirement", "make",
      "pytest", "assert", "expected", "output"],
     "Before you declare done:\n"
     "1. Re-read the task and list EVERY requirement (exact filenames, paths, formats,\n"
     "   values, exit codes). Check each literally — a near-miss (5/6) still fails.\n"
     "2. If the task ships a checker, RUN it and fix until green:\n"
     "   ls *test* *eval* Makefile 2>/dev/null; bash test.sh; python eval.py; make test\n"
     "3. Anything you write must run in a CLEAN env — don't depend on a package you\n"
     "   just pip-installed unless the task says to; prefer stdlib / the task's tools.\n"
     "4. Print the produced file/output and eyeball it against the spec."),

    ("Search / optimization over parameters (grid, random, bisect)",
     ["search", "optimize", "distribution", "parameter", "fit", "minimize",
      "grid", "bisect", "tune", "best", "objective"],
     "To find a parameter/value that satisfies a target:\n"
     "- Monotonic target -> BISECTION: lo,hi; while hi-lo>eps: mid=(lo+hi)/2; move the\n"
     "  bound based on f(mid) vs target.  (also `bisect` module for sorted lookup)\n"
     "- Unknown landscape -> coarse GRID scan, then refine around the best point.\n"
     "- Fitting a distribution: compute the empirical moments/quantiles and match, or\n"
     "  scipy.optimize.minimize on the negative log-likelihood. Validate with a KS test.\n"
     "Always define the objective explicitly and print it each iteration so you can see\n"
     "it converge instead of guessing."),

    ("Numerical sampling methods (rejection, inverse-CDF, adaptive)",
     ["sampling", "sample", "rejection", "adaptive", "distribution", "random",
      "monte", "carlo", "pdf", "cdf", "envelope"],
     "Draw from a density f you can evaluate:\n"
     "- INVERSE-CDF: if F^-1 is known, x = F^-1(U), U~Uniform(0,1).\n"
     "- REJECTION: pick envelope g with f(x) <= M*g(x); sample x~g, accept if\n"
     "  U*M*g(x) <= f(x). Efficiency = 1/M, so keep M tight.\n"
     "- ADAPTIVE rejection (log-concave f): build a piecewise-linear upper hull of\n"
     "  log f from tangent lines at support points; sample from the hull's exp, accept/\n"
     "  reject, and ADD each rejected point to the hull so it tightens over time.\n"
     "Seed the RNG for reproducibility and validate the sample mean/var against f."),

    ("Classification metrics — accuracy, precision, recall, F1, MCC, ROC/AUC, confusion matrix",
     ["metric", "metrics", "accuracy", "precision", "recall", "f1", "mcc", "roc",
      "auc", "confusion", "tp", "fp", "fn", "tn", "classification", "eval",
      "evaluate", "score", "true", "positive", "negative"],
     "Given y_true and y_pred (and y_score = P(positive) for ROC):\n"
     "  from sklearn.metrics import (accuracy_score, precision_score, recall_score,\n"
     "    f1_score, matthews_corrcoef, roc_auc_score, roc_curve, confusion_matrix)\n"
     "  acc=accuracy_score(y_true,y_pred); f1=f1_score(y_true,y_pred)\n"
     "  prec=precision_score(y_true,y_pred); rec=recall_score(y_true,y_pred)\n"
     "  mcc=matthews_corrcoef(y_true,y_pred); auc=roc_auc_score(y_true,y_score)\n"
     "  tn,fp,fn,tp = confusion_matrix(y_true,y_pred).ravel()   # BINARY order!\n"
     "  fpr,tpr,thr = roc_curve(y_true,y_score)   # plot tpr vs fpr; AUC=area\n"
     "Definitions: precision=TP/(TP+FP), recall=TP/(TP+FN), F1=2PR/(P+R),\n"
     "MCC=(TP*TN-FP*FN)/sqrt((TP+FP)(TP+FN)(TN+FP)(TN+FN)). Multiclass: pass\n"
     "average='macro'/'weighted'. Save metrics as JSON; plot ROC with matplotlib\n"
     "(plt.plot(fpr,tpr); savefig). Watch class order and the positive label."),

    ("PyTorch training / eval loop (correct structure)",
     ["pytorch", "torch", "train", "training", "model", "loss", "optimizer",
      "epoch", "cnn", "network", "nn", "fit", "gpu", "cuda", "dataloader"],
     "  import torch, torch.nn as nn\n"
     "  dev = 'cuda' if torch.cuda.is_available() else 'cpu'; model.to(dev)\n"
     "  opt = torch.optim.AdamW(model.parameters(), lr=1e-3)\n"
     "  loss_fn = nn.CrossEntropyLoss()   # expects raw logits + int class targets\n"
     "  for epoch in range(E):\n"
     "    model.train()\n"
     "    for xb,yb in train_loader:\n"
     "      xb,yb = xb.to(dev), yb.to(dev)\n"
     "      opt.zero_grad(); out = model(xb); loss = loss_fn(out, yb)\n"
     "      loss.backward(); opt.step()\n"
     "    model.eval()\n"
     "    with torch.no_grad(): ... # compute val metric, no grad\n"
     "Gotchas: zero_grad EVERY step; CrossEntropyLoss takes logits (no softmax);\n"
     "targets are Long class indices; move BOTH model and batch to the same device;\n"
     "set model.eval() (disables dropout/BN update) for validation. Seed torch for repro."),

    ("LoRA / full fine-tuning with transformers + peft",
     ["lora", "peft", "finetune", "fine-tune", "fine", "tuning", "adapter",
      "transformers", "pretrained", "huggingface", "bert", "gpt", "vit", "freeze"],
     "FULL fine-tune: load a pretrained model, train ALL params on your data with a\n"
     "small LR (2e-5..5e-5), AdamW, a few epochs; save with model.save_pretrained(dir).\n"
     "LoRA (train ~0.1% of params): \n"
     "  from peft import LoraConfig, get_peft_model, TaskType\n"
     "  cfg = LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,\n"
     "                   target_modules=['q_proj','v_proj'], task_type=TaskType.SEQ_CLS)\n"
     "  model = get_peft_model(base_model, cfg)\n"
     "  model.print_trainable_parameters()   # sanity: only adapters train\n"
     "  ... train ... ; model.save_pretrained('adapter')\n"
     "Reload: PeftModel.from_pretrained(base, 'adapter'). target_modules names differ\n"
     "per architecture — inspect model.named_modules() to find the linear layers."),

    ("Prepare / organize / split a dataset",
     ["data", "dataset", "prep", "prepare", "organize", "split", "train", "val",
      "test", "normalize", "preprocess", "balance", "stratify", "label", "clean"],
     "  from sklearn.model_selection import train_test_split\n"
     "  Xtr,Xtmp,ytr,ytmp = train_test_split(X,y,test_size=0.3,stratify=y,random_state=0)\n"
     "  Xval,Xte,yval,yte = train_test_split(Xtmp,ytmp,test_size=0.5,stratify=ytmp,random_state=0)\n"
     "Normalize features on TRAIN ONLY, then apply to val/test (no leakage):\n"
     "  from sklearn.preprocessing import StandardScaler\n"
     "  sc=StandardScaler().fit(Xtr); Xtr=sc.transform(Xtr); Xte=sc.transform(Xte)\n"
     "Images: resize + ToTensor + Normalize(mean,std). Keep a fixed split (seed) and\n"
     "check class balance (np.bincount(y)). Never fit scalers/encoders on test data."),

    ("Read/write HDF5 (.h5) and convert to/from JSON",
     ["h5", "hdf5", "h5py", "json", "convert", "dataset", "array", "save", "load",
      "file", "format", "serialize"],
     "  import h5py, numpy as np, json\n"
     "  with h5py.File('data.h5','r') as f:\n"
     "    print(list(f.keys())); arr = f['images'][:]; labels = f['labels'][:]\n"
     "    attrs = dict(f['images'].attrs)   # metadata\n"
     "  with h5py.File('out.h5','w') as f:\n"
     "    f.create_dataset('images', data=arr, compression='gzip')\n"
     "H5<->JSON: arrays aren't JSON-native — use arr.tolist() to dump, np.array to\n"
     "reload. json.dump({'shape':list(arr.shape),'data':arr.tolist()}, open('x.json','w')).\n"
     "For big arrays keep them in H5 and put only metadata/paths in JSON."),

    ("Implement a Transformer block / ViT",
     ["transformer", "attention", "vit", "vision", "encoder", "block", "self-attention",
      "multihead", "patch", "embedding", "positional", "layernorm"],
     "Transformer encoder block (pre-norm):\n"
     "  attn = nn.MultiheadAttention(d, heads, batch_first=True)\n"
     "  x = x + attn(ln1(x), ln1(x), ln1(x))[0]      # residual\n"
     "  x = x + mlp(ln2(x))   # mlp: Linear(d,4d)->GELU->Linear(4d,d)\n"
     "ViT: split image into patches (Conv2d(kernel=stride=patch) -> flatten), prepend a\n"
     "learnable [CLS] token, ADD positional embeddings, run N encoder blocks, classify\n"
     "from the CLS token. Shapes: (B,C,H,W)->(B, num_patches+1, d). Verify a forward\n"
     "pass runs and output is (B, num_classes); check grads flow (loss.backward())."),

    ("Diagnose a broken PyTorch training run",
     ["debug", "diagnose", "nan", "loss", "not", "learning", "overfit", "underfit",
      "shape", "mismatch", "error", "troubleshoot", "fix", "exploding", "gradient"],
     "- Loss is NaN/Inf: LR too high (lower it), log/sqrt of <=0, divide-by-zero, or\n"
     "  unnormalized inputs. Add torch.autograd.set_detect_anomaly(True); clip grads\n"
     "  (nn.utils.clip_grad_norm_); check for NaN in the data.\n"
     "- Loss flat / not learning: forgot opt.zero_grad or opt.step; LR too low; wrong\n"
     "  loss (softmax before CrossEntropyLoss); labels/logits misaligned; model not in\n"
     "  train(); frozen params. Print loss each step + a param's grad norm.\n"
     "- Shape mismatch: print .shape at each layer; CrossEntropyLoss wants logits\n"
     "  (N,C) + targets (N,) Long. - Train acc high, val low: overfitting -> augment/\n"
     "  regularize/early-stop. Always overfit a tiny batch first as a sanity check."),

    ("Generate a LaTeX report / results table",
     ["latex", "report", "table", "pdf", "document", "tex", "pdflatex", "figure",
      "results", "write", "tabular", "booktabs"],
     "Emit a compilable .tex, then build it:\n"
     "  \\documentclass{article}\\usepackage{booktabs,graphicx}\\begin{document}\n"
     "  \\begin{tabular}{lr}\\toprule Metric & Value\\\\\\midrule\n"
     "  Accuracy & 0.94\\\\ F1 & 0.92\\\\\\bottomrule\\end{tabular}\n"
     "  \\includegraphics[width=.6\\linewidth]{roc.png}\\end{document}\n"
     "Build: pdflatex -interaction=nonstopmode report.tex  (run twice for refs).\n"
     "Escape %, _, &, # in text. pandas: df.to_latex('t.tex', index=False). Confirm\n"
     "report.pdf exists and pdflatex exit code is 0."),
]

_WORD = re.compile(r"[a-z0-9.]+")


def retrieve_recipes(task_text: str, k: int = 2) -> list[tuple[str, str]]:
    """Return up to k (title, body) recipes most relevant to the task, by keyword
    overlap. Empty if nothing scores — never inject irrelevant noise."""
    if not task_text:
        return []
    words = set(_WORD.findall(task_text.lower()))
    scored = []
    for title, keywords, body in RECIPES:
        score = sum(1 for kw in keywords if kw in words)
        # also credit multiword keywords found as substrings (e.g. "pip install")
        score += sum(1 for kw in keywords if " " in kw and kw in task_text.lower())
        if score:
            scored.append((score, title, body))
    scored.sort(key=lambda x: -x[0])
    return [(t, b) for _, t, b in scored[:k]]


def recipe_context(task_text: str, k: int = 2) -> str:
    """A system-prompt appendix of the recipes relevant to this task, or ''."""
    hits = retrieve_recipes(task_text, k)
    if not hits:
        return ""
    blocks = "\n\n".join(f"### {title}\n{body}" for title, body in hits)
    return (
        "\n\nRELEVANT TECHNIQUE RECIPES (reference for THIS task — use if applicable):\n"
        + blocks
    )
