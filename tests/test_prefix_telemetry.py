"""Prefix-difference telemetry (drydock/prefix_telemetry.py) — cache-aware MCR spec
§32 item 2: measure how much of successive real prompts is identical, before changing
any behaviour."""
import json

from drydock.prefix_telemetry import (
    PrefixTelemetry,
    common_prefix_len,
    enabled,
    message_divergence,
    record_for,
    reset_registry,
)

SYS = {"role": "system", "content": "you are drydock"}
U1 = {"role": "user", "content": "fix the parser"}
A1 = {"role": "assistant", "content": "looking"}
T1 = {"role": "tool", "content": "pytest output here"}


def test_disabled_by_default_no_side_effect(tmp_path):
    assert enabled(None) is False and enabled({}) is False
    assert record_for({"cwd": str(tmp_path)}, [SYS, U1]) == {}
    assert not (tmp_path / ".drydock" / "research" / "prefix_diff").exists()


def test_enabled_by_config_or_env(monkeypatch):
    assert enabled({"prefix_telemetry": True}) is True
    monkeypatch.setenv("DRYDOCK_PREFIX_TELEMETRY", "1")
    assert enabled({}) is True


def test_common_prefix_len():
    assert common_prefix_len("abcdef", "abcXYZ") == 3
    assert common_prefix_len("", "abc") == 0
    assert common_prefix_len("abc", "abc") == 3


def test_message_divergence_locates_the_changed_message():
    assert message_divergence([SYS, U1, A1], [SYS, U1, T1]) == 2
    assert message_divergence([SYS, U1], [SYS, U1, A1]) == 2      # pure append
    assert message_divergence([SYS], [{"role": "system", "content": "REWRITTEN"}]) == 0


def test_first_call_has_no_reuse(tmp_path):
    t = PrefixTelemetry(root=str(tmp_path), session="s1")
    row = t.record([SYS, U1], model="nemotron")
    assert row["seq"] == 1 and row["reuse_pct"] == 0.0
    assert row["prompt_tokens_est"] > 0 and row["model"] == "nemotron"


def test_appending_a_message_reuses_almost_everything(tmp_path):
    """The append-only case: a tool result on the end should preserve the prefix."""
    t = PrefixTelemetry(root=str(tmp_path), session="s2")
    t.record([SYS, U1])
    row = t.record([SYS, U1, T1])
    assert row["reuse_pct"] > 60.0
    assert row["diverged_at_message"] == 2        # only the appended message is new


def test_rewriting_the_system_prompt_destroys_reuse(tmp_path):
    """The compaction case: rewriting an EARLY message invalidates everything after.

    Uses realistic-length content on purpose — with toy strings the JSON scaffolding
    (`[{"role": "system", "content": "`) is itself a shared prefix and would show as
    ~22% spurious reuse. At real prompt sizes that boilerplate is negligible."""
    big_sys = {"role": "system", "content": "you are drydock. " * 400}
    other_sys = {"role": "system", "content": "a completely different kernel. " * 400}
    body = [{"role": "user", "content": "fix the parser. " * 400}]
    t = PrefixTelemetry(root=str(tmp_path), session="s3")
    t.record([big_sys, *body])
    row = t.record([other_sys, *body])
    assert row["diverged_at_message"] == 0
    assert row["reuse_pct"] < 5.0
    assert row["uncached_tokens_est"] > 0


def test_append_reuses_far_more_than_an_early_rewrite(tmp_path):
    """The comparison that matters: same edit size, different position."""
    big_sys = {"role": "system", "content": "kernel. " * 400}
    body = {"role": "user", "content": "task. " * 400}
    tail = {"role": "tool", "content": "output. " * 50}
    a = PrefixTelemetry(root=str(tmp_path), session="s3a")
    a.record([big_sys, body])
    appended = a.record([big_sys, body, tail])
    b = PrefixTelemetry(root=str(tmp_path), session="s3b")
    b.record([big_sys, body])
    rewritten = b.record([{"role": "system", "content": "other. " * 400}, body])
    assert appended["reuse_pct"] > 90.0 > rewritten["reuse_pct"]


def test_identical_prompt_is_full_reuse(tmp_path):
    t = PrefixTelemetry(root=str(tmp_path), session="s4")
    t.record([SYS, U1])
    row = t.record([SYS, U1])
    assert row["reuse_pct"] == 100.0 and row["uncached_tokens_est"] == 0


def test_rows_are_persisted_as_jsonl(tmp_path):
    t = PrefixTelemetry(root=str(tmp_path), session="s5")
    t.record([SYS, U1])
    t.record([SYS, U1, A1])
    rows = [json.loads(x) for x in t.path.read_text().splitlines() if x.strip()]
    assert [r["seq"] for r in rows] == [1, 2]


def test_record_never_raises_on_unserialisable(tmp_path):
    t = PrefixTelemetry(root=str(tmp_path), session="s6")
    assert t.record([{"role": "user", "content": object()}]) is not None


def test_recorder_follows_the_run_not_the_config_object(tmp_path):
    """Regression: the recorder used to live on the config dict, which is shallow-copied
    per call — so every inference built a NEW recorder and every row read seq=1 /
    reuse 0.0. It must persist across calls within a run even when the config is a
    fresh object each time."""
    reset_registry()
    record_for({"cwd": str(tmp_path), "prefix_telemetry": True}, [SYS, U1])
    row = record_for({"cwd": str(tmp_path), "prefix_telemetry": True}, [SYS, U1, A1])
    assert row["seq"] == 2                       # different dict, same run -> continues
    assert row["reuse_pct"] > 0.0                # and therefore has something to compare


def test_separate_working_dirs_are_separate_runs(tmp_path):
    reset_registry()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    record_for({"cwd": str(a), "prefix_telemetry": True}, [SYS, U1])
    assert record_for({"cwd": str(b), "prefix_telemetry": True}, [SYS, U1])["seq"] == 1
