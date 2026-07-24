---
name: document-canvas
description: Edit a large document (md/txt) via the Document Canvas — search, patch, validate, commit, without loading it all
---
Edit the document as a CANVAS — never load the whole thing into context. Target
for this task: $ARGS

Work like a programmer editing a large codebase. Address blocks by their stable
id (para-0182, sec-0004, …), not by line numbers or page numbers.

1. **Open.** Call DocOpen with the file path. Read the outline it returns to
   understand the structure. (The document name is the filename.)

2. **Locate — don't read everything.** Use DocSearch to find the blocks relevant
   to the change (exact text, or regex:true for patterns like requirement numbers).
   Use DocOutline to navigate the hierarchy. Only the blocks you open enter context.

3. **Read the target region.** Call DocRead on each candidate block with a small
   `before`/`after` window so you understand its context. Note each target's
   content **hash** — you must pass it back as `expected_hash`.

4. **Patch — hash-guarded, staged.** Call DocPatch with op (replace / insert_before
   / insert_after / delete), target_id, the `expected_hash` from DocRead, and
   new_text plus a short `reason`. Patches are STAGED in a working copy — the source
   file is NOT changed yet. If a hash is stale the patch is rejected; re-DocRead and
   retry. For several related edits, pass a `patches` list so they apply atomically.

5. **Preview.** Call DocDiff to see every staged change as a unified diff. Confirm
   you changed only what you intended and did not over-edit neighbouring text.

6. **Validate.** Call DocValidate (pass `prohibited` for terms that must be gone and
   `required` for terms that must remain). Resolve any ⚠ findings before committing.

7. **Commit.** Only when the diff and validation look right, call DocCommit. It
   writes the source file and preserves the untouched original as <file>.orig. Use
   DocRollback to discard staged edits instead.

For a global change (e.g. a terminology update), search for ALL occurrences, then
process them in small batches — you do not need every passage in context at once.
Report which blocks you changed and the final validation result.
