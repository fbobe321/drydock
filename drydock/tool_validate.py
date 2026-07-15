"""Tool-argument validation + deterministic repair (PRD Epic G).

Weak local models mangle tool arguments — a trailing comma, an integer sent as
"10", a required field omitted, or the whole payload smuggled back as a raw
string. Executing those either fails confusingly or loops. This layer, run just
before execution:

  1. deterministically REPAIRS the safe, unambiguous cases (re-parse a raw JSON
     blob, drop a trailing comma, coerce "10" -> 10 where the schema wants an int);
  2. VALIDATES what remains against the tool's JSON schema (required properties
     present, obvious type mismatches) and, when it still doesn't fit, returns a
     typed INVALID_ARGUMENTS result the model can act on — WITHOUT executing.

Deterministic only: there is no repair *model* (Drydock never spends an extra LLM
call here), so there's no unbounded repair loop to cap — one repair pass, then
validate. Stdlib-only, never raises. All logic original to Drydock.
"""
from __future__ import annotations

import json
import re

INVALID_ARGUMENTS = "INVALID_ARGUMENTS"

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

# JSON-schema type name -> Python types that satisfy it (bool excluded from int:
# in JSON, true is not an integer).
_TYPE_OK = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _input_schema(schema: dict) -> dict:
    """The object schema for a tool's arguments, from either key spelling."""
    if not isinstance(schema, dict):
        return {}
    return schema.get("input_schema") or schema.get("parameters") or {}


def _coerce_scalar(value, target_type: str):
    """Safely coerce a scalar to the schema's type, or return the value unchanged.
    Only unambiguous conversions are performed (a clean numeric string, an exact
    'true'/'false'); anything doubtful is left alone for validation to flag."""
    if target_type in ("integer", "number") and isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if target_type == "number" and re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
    if target_type == "boolean" and isinstance(value, str):
        low = value.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
    return value  # not a safe/unambiguous coercion — leave it for validation


def repair_args(schema: dict, args: dict) -> tuple[dict, list[str]]:
    """Deterministically repair `args` toward the schema. Returns (repaired, notes)
    where notes describes each change made. Never raises."""
    notes: list[str] = []
    if not isinstance(args, dict):
        return {}, notes

    # 1. The whole payload came back as a raw string — re-parse it (dropping a
    #    trailing comma, the single most common syntactic slip).
    if set(args) == {"_raw"} and isinstance(args["_raw"], str):
        raw = args["_raw"]
        for candidate in (raw, _TRAILING_COMMA.sub(r"\1", raw)):
            try:
                parsed = json.loads(candidate, strict=False)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                notes.append("re-parsed raw JSON arguments")
                args = parsed
                break

    props = _input_schema(schema).get("properties") or {}
    repaired = dict(args)
    # 2. Coerce obvious scalar type slips (string "10" where an int is wanted).
    for key, spec in props.items():
        if key in repaired and isinstance(spec, dict):
            want = spec.get("type")
            if isinstance(want, str):
                new = _coerce_scalar(repaired[key], want)
                if type(new) is not type(repaired[key]) or new != repaired[key]:
                    repaired[key] = new
                    notes.append(f"coerced '{key}' to {want}")
    return repaired, notes


def validate_args(schema: dict, args: dict) -> list[str]:
    """Validate `args` against the tool's JSON schema. Returns a list of human
    error strings (empty == valid). Conservative: it flags missing REQUIRED
    properties and clear scalar type mismatches, and ignores extra properties so
    a slightly-off schema never blocks a workable call."""
    errors: list[str] = []
    isch = _input_schema(schema)
    if not isch:
        return errors  # nothing to validate against
    if not isinstance(args, dict) or set(args) == {"_raw"}:
        return [f"arguments are not a valid object: {args!r}"]

    props = isch.get("properties") or {}
    for req in isch.get("required") or []:
        if req not in args or args[req] is None:
            errors.append(f"missing required property: '{req}'")

    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue  # unknown/extra property — allowed
        want = spec.get("type")
        ok_types = _TYPE_OK.get(want) if isinstance(want, str) else None
        if ok_types is None:
            continue
        # bool is a subclass of int — exclude it from integer/number unless wanted.
        if want in ("integer", "number") and isinstance(value, bool):
            errors.append(f"property '{key}' should be {want}, got boolean")
        elif not isinstance(value, ok_types):
            errors.append(f"property '{key}' should be {want}, got {type(value).__name__}")
    return errors


def repair_and_validate(schema: dict, args: dict) -> tuple[dict, list[str], list[str]]:
    """One-shot: repair, then validate. Returns (repaired_args, errors, repair_notes).
    errors empty means the call is safe to execute."""
    repaired, notes = repair_args(schema, args)
    return repaired, validate_args(schema, repaired), notes
