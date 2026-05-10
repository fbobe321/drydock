"""Tests for the admiral_history → pairs.jsonl extractor.

Synthetic fixtures only — no real admiral data, no GPU. We build a
tmp_path with the same shapes the production extractor consumes:

  admiral_state.json        — {"findings": {<name>: {"sessions": [...]}, ...}}
  sessions_root/<sid>/messages.jsonl   — JSON-per-line conversation transcript

and assert that:

- The right sessions get labelled (`derailed` for finding-flagged,
  `good` for unflagged).
- The extracted (prompt, completion) is the LAST user→assistant
  text pair in the session.
- Sessions whose directories don't exist anywhere are skipped.
- Sessions with only tool messages (no assistant text) are skipped.
- Pairs shorter than min_chars are dropped.
- The legacy admiral findings shape (list, not dict) is accepted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drydock.steering.train import extract_pairs as ep


def _write_session(root: Path, sid: str, messages: list[dict]) -> Path:
    sdir = root / sid
    sdir.mkdir(parents=True, exist_ok=True)
    with (sdir / "messages.jsonl").open("w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")
    return sdir


def _write_admiral_state(path: Path, findings: dict) -> None:
    path.write_text(json.dumps({"findings": findings}))


@pytest.fixture
def fixture(tmp_path):
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    # Two derailed sessions (in finding "empty_after_tool:bash")
    _write_session(sessions_root, "sess-derailed-1", [
        {"role": "user", "content": "earlier user"},
        {"role": "assistant", "content": "earlier assistant"},
        {"role": "user", "content": "fix the bug in foo.py"},
        {"role": "assistant", "content": "I'll look at it."},
    ])
    _write_session(sessions_root, "sess-derailed-2", [
        {"role": "user", "content": "build the thing"},
        {"role": "assistant", "content": "Working on it now."},
    ])
    # One derailed session with only tool/assistant-tool-call messages
    # → no usable text pair, should be skipped.
    _write_session(sessions_root, "sess-derailed-empty", [
        {"role": "user", "content": "do X"},
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "tool result"},
    ])
    # Two clean sessions (not in any finding)
    _write_session(sessions_root, "sess-clean-1", [
        {"role": "user", "content": "explain how Y works in this repo"},
        {"role": "assistant", "content": "Y is implemented via a registry pattern."},
    ])
    _write_session(sessions_root, "sess-clean-2", [
        {"role": "user", "content": "summarise this PRD please"},
        {"role": "assistant", "content": "The PRD covers these areas..."},
    ])

    state = tmp_path / "admiral_state.json"
    _write_admiral_state(state, {
        "empty_after_tool:bash": {
            "sessions": [
                "sess-derailed-1", "sess-derailed-2",
                "sess-derailed-empty", "sess-missing-from-disk",
            ],
            "last_seen": "2026-05-10T00:00:00+00:00",
        },
        "other_finding": {
            "sessions": ["sess-other"],
            "last_seen": "2026-05-09T00:00:00+00:00",
        },
    })
    return {
        "state": state,
        "sessions_root": sessions_root,
        "tmp": tmp_path,
    }


def test_extract_pairs_basic(fixture):
    pairs = ep.extract_pairs(
        admiral_state=fixture["state"],
        sessions_dirs=[fixture["sessions_root"]],
        finding="empty_after_tool:bash",
        max_derailed=10,
        max_good=10,
    )
    labels = [p["label"] for p in pairs]
    # Two derailed kept (third had no text pair, fourth had no dir),
    # two clean kept.
    assert labels.count("derailed") == 2
    assert labels.count("good") == 2

    derailed = [p for p in pairs if p["label"] == "derailed"]
    derailed_by_id = {p["id"].split(":")[0]: p for p in derailed}
    # The "fix the bug" session must keep the LATER pair, not the earlier one.
    p1 = derailed_by_id["sess-derailed-1"]
    assert p1["prompt"] == "fix the bug in foo.py"
    assert p1["completion"] == "I'll look at it."
    p2 = derailed_by_id["sess-derailed-2"]
    assert p2["prompt"] == "build the thing"


def test_extract_pairs_respects_max_derailed(fixture):
    pairs = ep.extract_pairs(
        admiral_state=fixture["state"],
        sessions_dirs=[fixture["sessions_root"]],
        finding="empty_after_tool:bash",
        max_derailed=1,
        max_good=10,
    )
    derailed = [p for p in pairs if p["label"] == "derailed"]
    assert len(derailed) == 1


def test_extract_pairs_respects_max_good(fixture):
    pairs = ep.extract_pairs(
        admiral_state=fixture["state"],
        sessions_dirs=[fixture["sessions_root"]],
        finding="empty_after_tool:bash",
        max_derailed=10,
        max_good=1,
    )
    good = [p for p in pairs if p["label"] == "good"]
    assert len(good) == 1


def test_extract_pairs_unknown_finding_exits(fixture):
    with pytest.raises(SystemExit, match="not in admiral_state"):
        ep.extract_pairs(
            admiral_state=fixture["state"],
            sessions_dirs=[fixture["sessions_root"]],
            finding="nope",
        )


def test_extract_pairs_min_chars_filters(fixture, tmp_path):
    # Add a tiny pair that should be filtered.
    _write_session(fixture["sessions_root"], "sess-tiny", [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ])
    # Mark it derailed so the filter has something to bite on.
    _write_admiral_state(fixture["state"], {
        "empty_after_tool:bash": {"sessions": ["sess-tiny"]},
    })
    pairs = ep.extract_pairs(
        admiral_state=fixture["state"],
        sessions_dirs=[fixture["sessions_root"]],
        finding="empty_after_tool:bash",
        max_derailed=10, max_good=10, min_chars=64,
    )
    # The tiny pair (4 chars) is below the threshold; nothing should
    # be labeled derailed.
    derailed = [p for p in pairs if p["label"] == "derailed"]
    assert derailed == []


def test_legacy_findings_list_shape(tmp_path):
    sessions = tmp_path / "s"
    sessions.mkdir()
    _write_session(sessions, "sess-A", [
        {"role": "user", "content": "the first prompt of any length"},
        {"role": "assistant", "content": "the first completion of any length"},
    ])
    state = tmp_path / "admiral_state.json"
    state.write_text(json.dumps({
        "findings": {
            "legacy_finding": ["sess-A"],   # raw list, not a dict
        }
    }))
    pairs = ep.extract_pairs(
        admiral_state=state,
        sessions_dirs=[sessions],
        finding="legacy_finding",
        max_derailed=10, max_good=10,
    )
    assert any(p["label"] == "derailed" and "sess-A" in p["id"] for p in pairs)


def test_session_id_short_suffix_match(tmp_path):
    """Some sessions are stored under `session_<ts>_<short-uuid>` rather
    than the full UUID. Extractor must still find them."""
    sessions = tmp_path / "s"
    sessions.mkdir()
    full_uuid = "5b55aacd-3606-4656-ad4b-eacf86506eda"
    short = full_uuid.replace("-", "")[:8]    # 5b55aacd
    _write_session(sessions, f"session_20260504_{short}", [
        {"role": "user", "content": "this is the actual prompt content"},
        {"role": "assistant", "content": "this is the actual completion content"},
    ])
    state = tmp_path / "admiral_state.json"
    state.write_text(json.dumps({
        "findings": {"f": {"sessions": [full_uuid]}}
    }))
    pairs = ep.extract_pairs(
        admiral_state=state, sessions_dirs=[sessions], finding="f",
        max_derailed=10, max_good=10,
    )
    assert len(pairs) == 1
    assert pairs[0]["label"] == "derailed"


def test_clean_excludes_sessions_in_other_findings(tmp_path):
    """A session flagged by ANY finding must not appear in the 'good' set."""
    sessions = tmp_path / "s"
    sessions.mkdir()
    _write_session(sessions, "sess-flagged-elsewhere", [
        {"role": "user", "content": "user message text long enough to keep"},
        {"role": "assistant", "content": "assistant message text"},
    ])
    _write_session(sessions, "sess-truly-clean", [
        {"role": "user", "content": "user message text long enough to keep"},
        {"role": "assistant", "content": "assistant message text"},
    ])
    state = tmp_path / "admiral_state.json"
    state.write_text(json.dumps({
        "findings": {
            "target_finding": {"sessions": []},
            "other_finding":  {"sessions": ["sess-flagged-elsewhere"]},
        }
    }))
    pairs = ep.extract_pairs(
        admiral_state=state, sessions_dirs=[sessions],
        finding="target_finding", max_derailed=10, max_good=10,
    )
    good_ids = [p["id"] for p in pairs if p["label"] == "good"]
    assert any("sess-truly-clean" in i for i in good_ids)
    assert not any("sess-flagged-elsewhere" in i for i in good_ids)


def test_cli_main_writes_pairs(fixture, tmp_path):
    out = tmp_path / "pairs.jsonl"
    rc = ep.main([
        "--admiral-state", str(fixture["state"]),
        "--sessions-dir", str(fixture["sessions_root"]),
        "--finding", "empty_after_tool:bash",
        "--out", str(out),
        "--log-level", "ERROR",
    ])
    assert rc == 0
    assert out.is_file()
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    records = [json.loads(l) for l in lines]
    labels = {r["label"] for r in records}
    assert labels == {"good", "derailed"}
    for r in records:
        assert {"id", "label", "prompt", "completion"} <= set(r.keys())
