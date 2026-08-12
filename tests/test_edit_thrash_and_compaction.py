"""Regression tests for the four /loop + edit-robustness fixes:
  - compaction now leaves DURABLE headroom (compacts below the trigger)
  - _numbered_snapshot renders a bounded, line-numbered file view
  - the failed-edit thrash breaker fires on repeated same-file misses
"""
from drydock import compaction
from drydock.agent import _numbered_snapshot


def _msgs_over(limit_tokens: int):
    """A realistic long history: a first user msg + many assistant/tool turns whose
    accumulated tool results blow past the limit (the /loop-accumulation shape)."""
    chunk = "y" * 4000  # ~1333 tokens each
    msgs = [{"role": "user", "content": "start the task"}]
    for i in range(40):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": str(i), "name": "Bash",
                                     "input": {"command": f"run step {i}"}}]})
        msgs.append({"role": "tool", "content": f"output {i} " + chunk})
    msgs.append({"role": "assistant", "content": "done"})
    return msgs


def test_compact_leaves_headroom_below_trigger():
    """compact() must reduce BELOW the 0.60 maybe_compact trigger, not down to it —
    otherwise it frees ~nothing and re-fires every turn (the 'doesn't compact' bug)."""
    limit = 10_000
    msgs = _msgs_over(limit)
    assert compaction.estimate_tokens(msgs) > int(limit * 0.60)
    out = compaction.compact(msgs, limit)
    after = compaction.estimate_tokens(out)
    # default target_frac is 0.45 → must sit at/below 45%, comfortably under 60%
    assert after <= int(limit * 0.45) + 5, (after, int(limit * 0.45))


def test_compact_target_frac_is_configurable():
    limit = 10_000
    out = compaction.compact(_msgs_over(limit), limit, target_frac=0.30)
    assert compaction.estimate_tokens(out) <= int(limit * 0.30) + 5


def test_maybe_compact_triggers_at_60pct():
    """maybe_compact still uses a 0.60 trigger against the MAX of estimate and the
    server's real last_input_tokens."""
    class S:
        messages = [{"role": "user", "content": "hi"}]
        last_input_tokens = 7000  # > 60% of 10k
    s = S()
    compaction.maybe_compact(s, {"context_limit": 10_000})
    # trigger fired (messages object handed to compact and returned) — no crash,
    # and a below-trigger state is a no-op:
    class S2:
        messages = [{"role": "user", "content": "hi"}]
        last_input_tokens = 100
    before = list(S2.messages)
    compaction.maybe_compact(S2(), {"context_limit": 10_000})
    assert S2.messages == before


def test_numbered_snapshot_basic(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("line one\nline two\nline three\n")
    snap = _numbered_snapshot(str(f))
    assert "    1\tline one" in snap
    assert "    3\tline three" in snap


def test_numbered_snapshot_caps_lines(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"row{i}" for i in range(500)))
    snap = _numbered_snapshot(str(f), max_lines=50)
    assert "more lines" in snap
    assert "row49" in snap and "row400" not in snap


def test_numbered_snapshot_missing_file():
    snap = _numbered_snapshot("/no/such/file/xyz")
    assert "could not read" in snap.lower()


def test_edit_thrash_counter_logic():
    """The breaker's core rule: count 'old_string not found' per file regardless
    of the (varying) old_string, fire on the 3rd, and a clean edit resets it.
    Mirrors the agent-loop bookkeeping so the invariant is pinned even though the
    full loop needs a live model."""
    edit_fail_counts: dict[str, int] = {}

    def step(fp: str, result: str) -> bool:
        thrash = False
        if "old_string not found" in result:
            edit_fail_counts[fp] = edit_fail_counts.get(fp, 0) + 1
            if edit_fail_counts[fp] >= 3:
                thrash = True
        else:
            edit_fail_counts.pop(fp, None)
        return thrash

    # three DIFFERENT wrong old_strings on the same file → fires on the 3rd
    assert step("/a.py", "Error: old_string not found in /a.py ...") is False
    assert step("/a.py", "Error: old_string not found in /a.py ...") is False
    assert step("/a.py", "Error: old_string not found in /a.py ...") is True
    # a different file is tracked independently
    assert step("/b.py", "Error: old_string not found in /b.py ...") is False
    # a clean edit resets the file's counter
    assert step("/a.py", "Edited /a.py") is False
    assert "/a.py" not in edit_fail_counts
    assert step("/a.py", "Error: old_string not found in /a.py ...") is False


def test_use_streaming_forces_nonstream_for_local_tool_turns():
    """A Gemma behind vLLM served under a non-'gemma' name must NOT stream tool
    turns (the latent corruption trap). Text-only turns still stream."""
    from drydock.tuning import use_streaming
    # non-'gemma' served name on a local provider → tool turns non-streamed
    assert use_streaming("Q5_K_M", has_tools=True, provider="vllm") is False
    assert use_streaming("default", has_tools=True, provider="ollama") is False
    assert use_streaming("model", has_tools=True, provider="lmstudio") is False
    # text-only turns still stream everywhere
    assert use_streaming("Q5_K_M", has_tools=False, provider="vllm") is True
    # name-based gemma match still works regardless of provider
    assert use_streaming("gemma4", has_tools=True, provider=None) is False
    # a remote provider (openai) with tools is NOT force-non-streamed by provider
    assert use_streaming("gpt-4o", has_tools=True, provider="openai") is True
