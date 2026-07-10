---
name: ml-data
description: Organize, prep, split, normalize, or convert a dataset (incl. HDF5/JSON)
---
Data-engineering task: $ARGS

1. INSPECT first — format (H5/CSV/JSON/images), shapes, dtypes, class balance
   (`np.bincount`), missing values.
2. Splits → stratified train/val/test; fit any scaler/encoder on TRAIN ONLY and apply
   to val/test (NEVER fit on val/test — that leaks). Use a fixed random_state.
3. HDF5 ↔ JSON → preserve shapes, dtypes, and attrs; `h5py` for arrays (compression for
   big ones), `arr.tolist()` when a JSON needs the values; keep large arrays in H5 and
   only metadata/paths in JSON.
4. Save outputs EXACTLY as specified (names, keys, dtypes) and validate by reloading and
   checking shapes/counts.
