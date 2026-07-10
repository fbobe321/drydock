---
name: ml-metrics
description: Compute a full classification metrics report (accuracy/precision/recall/F1/MCC/ROC-AUC/confusion)
---
Compute an evaluation-metrics report for: $ARGS

1. Load the ground-truth labels and the predictions (and predicted probabilities /
   scores if ROC-AUC is needed). Confirm shapes and the positive label / class order.
2. Using scikit-learn compute: accuracy, precision, recall, F1, MCC, ROC-AUC, and the
   confusion matrix. For binary, report the counts TP/FP/TN/FN
   (`tn,fp,fn,tp = confusion_matrix(y_true,y_pred).ravel()`). For multiclass use
   `average='macro'` (and per-class arrays) and `roc_auc_score(..., multi_class='ovr')`.
3. Save the metrics as JSON with clear keys, and plot the ROC curve
   (`roc_curve` → plot tpr vs fpr) to an image if asked.
4. Print a readable table. Double-check definitions: precision=TP/(TP+FP),
   recall=TP/(TP+FN), F1=2PR/(P+R), MCC uses all four cells.
