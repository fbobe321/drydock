"""Tests for advisory loop detection."""
from __future__ import annotations

from drydock.loop_detect import LoopTracker, loop_note, path_thrash_note, tool_signature


def test_path_thrash_note_quiet_until_third():
    assert path_thrash_note("a.py", 1) is None
    assert path_thrash_note("a.py", 2) is None
    assert path_thrash_note("a.py", 3) is not None
    assert "write #3" in path_thrash_note("a.py", 3)


def test_path_thrash_fires_on_different_content_writes():
    t = LoopTracker()
    r1 = t.annotate("Write", {"file_path": "x.py", "content": "v1"}, "ok")
    r2 = t.annotate("Write", {"file_path": "x.py", "content": "v2"}, "ok")
    r3 = t.annotate("Write", {"file_path": "x.py", "content": "v3"}, "ok")
    assert "NOTE" not in r1 and "NOTE" not in r2
    assert "write #3" in r3


def test_path_thrash_ignores_non_write_tools():
    t = LoopTracker()
    r = "ok"
    for _ in range(5):
        r = t.annotate("Read", {"file_path": "x.py"}, "ok")
    assert "write #" not in r


def test_signature_stable_regardless_of_key_order():
    a = tool_signature("Grep", {"pattern": "x", "path": "."})
    b = tool_signature("Grep", {"path": ".", "pattern": "x"})
    assert a == b


def test_signature_distinguishes_inputs():
    assert tool_signature("Read", {"file_path": "a"}) != tool_signature(
        "Read", {"file_path": "b"}
    )


def test_loop_note_escalates():
    assert loop_note("Read", 1) is None
    assert "already" in loop_note("Read", 2)
    assert "Stop repeating" in loop_note("Read", 3)
    assert "looping" in loop_note("Read", 5)


def test_tracker_counts_and_annotates():
    t = LoopTracker()
    r1 = t.annotate("Read", {"file_path": "a"}, "contents")
    assert r1 == "contents"  # first call: no note
    r2 = t.annotate("Read", {"file_path": "a"}, "contents")
    assert "NOTE" in r2 and "contents" in r2  # repeat: annotated
    # a different call is not annotated
    r3 = t.annotate("Read", {"file_path": "b"}, "other")
    assert r3 == "other"


def test_tracker_never_raises_on_unserializable_input():
    t = LoopTracker()
    # objects that aren't JSON-serializable must still produce a signature
    result = t.annotate("X", {"obj": object()}, "ok")
    assert result == "ok"
    result2 = t.annotate("X", {"obj": object()}, "ok")
    # signatures use repr() fallback; two distinct objects differ, so no note —
    # the point is simply that annotate() does not raise.
    assert "ok" in result2


def test_repeated_identical_failure_body_is_pruned_after_third():
    # PRD Epic J3 / the large-scale-text-editing trajectory: the same failing test
    # rerun 55× with a byte-identical error. First two failures keep their text
    # (it's the fix); the 3rd+ identical failure gets its body pruned so it stops
    # bloating context.
    t = LoopTracker()
    err = "Error: expected foo but got bar\n" + "x" * 500
    r1 = t.annotate("Bash", {"command": "pytest"}, err)
    r2 = t.annotate("Bash", {"command": "pytest"}, err)
    r3 = t.annotate("Bash", {"command": "pytest"}, err)
    assert "x" * 500 in r1  # first failure: full text kept
    assert "x" * 500 in r2  # second: still kept (fix info)
    assert "x" * 500 not in r3  # third: body pruned
    assert "identical error" in r3


def test_changing_failure_body_is_not_pruned():
    # A failing call whose error is actually CHANGING is progress — never prune it.
    t = LoopTracker()
    for i in range(4):
        r = t.annotate("Bash", {"command": "pytest"}, f"Error: failure number {i}\n")
        assert f"failure number {i}" in r


def test_record_outcome_counts_identical_bodies():
    t = LoopTracker()
    assert t.record_outcome("Bash", {"command": "x"}, "same") == 1
    assert t.record_outcome("Bash", {"command": "x"}, "same") == 2
    # different body -> its own counter
    assert t.record_outcome("Bash", {"command": "x"}, "different") == 1
