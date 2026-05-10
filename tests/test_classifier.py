"""Tests for the failure classifier.

Drives the v0 rule set against synthetic log lines that mirror each
documented pattern from MODEL_SHORTCOMINGS.md / TRIAGE_v1.md, and
confirms each lands in the expected bucket.
"""
from __future__ import annotations

from drydock.core.classifier import (
    Bucket,
    Classifier,
    FailureSignal,
    classify_lines,
    classify_text,
)


def _bucket(signals, pattern_id):
    for s in signals:
        if s.pattern_id == pattern_id:
            return s.bucket
    return None


def test_harness_search_replace_not_found_loop():
    signals = classify_text(
        "[admiral] retry_after_error:search_replace fired 4x on session_X"
    )
    assert any(s.bucket == Bucket.HARNESS for s in signals)
    assert _bucket(signals, "harness:search_replace:not_found_loop") == Bucket.HARNESS


def test_harness_bash_heredoc_loop():
    signals = classify_text(
        "[admiral] loop:bash detected with cat << 'EOF' > plugin.py heredoc"
    )
    assert _bucket(signals, "harness:bash:heredoc_loop") == Bucket.HARNESS


def test_harness_bash_escape_loop():
    signals = classify_text(
        "[admiral] loop:bash echo -e \\n loop on yaml_to_toml CLI"
    )
    assert _bucket(signals, "harness:bash:escape_loop") == Bucket.HARNESS


def test_harness_grep_unescaped_pattern():
    signals = classify_text(
        "grep: Unmatched ( or \\(  for pattern: def run(self,"
    )
    assert _bucket(signals, "harness:grep:unescaped_pattern") == Bucket.HARNESS


def test_harness_grep_repeated_call_loop_classified_separately():
    """The 'this exact call to grep has been made N times' admiral text
    must NOT be classified as harness:grep:unescaped_pattern (it isn't
    an invalid regex — the model is just looping on a valid grep).
    Pre-2026-05-10 the rule conflated the two and produced thousands
    of false-positive unescaped_pattern fires."""
    signals = classify_text(
        "retry_after_error:grep:NOTE: this exact call to `grep` has been made 34 times"
    )
    # Loop-rule fires…
    assert _bucket(signals, "harness:grep:repeated_call_loop") == Bucket.HARNESS
    # …and the invalid-regex rule does NOT fire on this text.
    assert (
        _bucket(signals, "harness:grep:unescaped_pattern") is None
    ), "loop signal must not be misclassified as unescaped_pattern"


def test_harness_grep_repeated_call_loop_matches_match_text():
    """Admiral also writes the canned message with the matched-line
    fragment as the error head. Both shapes must hit the loop rule."""
    signals = classify_text(
        "retry_after_error:grep:matches: ./.tool_agent_memory/default.json:20"
    )
    assert _bucket(signals, "harness:grep:repeated_call_loop") == Bucket.HARNESS
    assert _bucket(signals, "harness:grep:unescaped_pattern") is None


def test_harness_hallucinated_tool():
    signals = classify_text(
        "[error] unknown tool: ralph_repo_index"
    )
    assert _bucket(signals, "harness:tool:hallucinated_name") == Bucket.HARNESS


def test_harness_thinking_stall():
    signals = classify_text("empty assistant message after tool result")
    assert _bucket(signals, "harness:thinking_stall") == Bucket.HARNESS


def test_harness_install_api_key():
    signals = classify_text(
        "MissingAPIKeyError: MISTRAL_API_KEY is not set"
    )
    assert _bucket(signals, "harness:install:api_key_demanded") == Bucket.HARNESS


def test_retrieval_cross_package_inheritance():
    signals = classify_text(
        "model called read_file on flask/wrappers.py 13 times looking for is_json"
    )
    assert any(s.bucket == Bucket.RETRIEVAL for s in signals)


def test_retrieval_multi_module_design_loss():
    signals = classify_text(
        "rewriting interpreter.py and breaking lexer.py — module type_checker regressed"
    )
    assert _bucket(signals, "retrieval:multi_module_design_loss") == Bucket.RETRIEVAL


def test_steering_no_web_search_when_stuck():
    signals = classify_text(
        "100 search_replace fail, 0 web_search calls — local-only failure loop"
    )
    assert any(s.bucket == Bucket.STEERING for s in signals)


def test_steering_rewrite_instead_of_patch():
    signals = classify_text(
        "score regression after fix: full-file rewrite broke 4 passing tests"
    )
    assert any(s.bucket == Bucket.STEERING for s in signals)


def test_steering_interactive_fallback():
    signals = classify_text(
        "subprocess raised getpass EOFError — ignored --password flag"
    )
    assert any(s.bucket == Bucket.STEERING for s in signals)


def test_model_subtle_logic_bug():
    signals = classify_text(
        "4 iterations same failing test; never traced data flow"
    )
    assert any(s.bucket == Bucket.MODEL_PRIOR for s in signals)


def test_ambiguous_input():
    signals = classify_text(
        "asked for clarification: prompt is ambiguous, don't understand the request"
    )
    assert any(s.bucket == Bucket.AMBIGUOUS_INPUT for s in signals)


def test_classifier_dedups_same_evidence():
    """Same line must produce only one signal, even if scanned twice."""
    line = "[admiral] retry_after_error:search_replace fired 4x"
    text = "\n".join([line] * 5)
    signals = classify_text(text)
    matches = [s for s in signals if s.pattern_id == "harness:search_replace:not_found_loop"]
    assert len(matches) == 1


def test_summarize_returns_per_bucket_counts():
    text = """
        [admiral] retry_after_error:search_replace 4x
        [admiral] retry_after_error:search_replace 5x   different evidence
        loop:bash with cat << EOF heredoc
        unknown tool: ralph_repo_index
    """
    signals = classify_text(text)
    summary = Classifier.summarize(signals)
    # All these patterns target HARNESS
    assert summary.get("Bucket.HARNESS", summary.get("harness", 0)) >= 3


def test_unmatched_lines_produce_no_signals():
    signals = classify_text("Just a regular log line that mentions nothing of interest.")
    assert signals == []


def test_signal_to_jsonable_roundtrip():
    signals = classify_text("MissingAPIKeyError")
    assert signals
    d = signals[0].to_jsonable()
    assert "bucket" in d
    assert "pattern_id" in d
    assert d["evidence"]


def test_classify_real_admiral_fire_codes():
    """Spot-check against admiral fire codes from real autonomous_review logs."""
    text = """
    Admiral: loop:bash with concurrent.futures benchmark, 6 fires 06:28-06:33 UTC
    Admiral: retry_after_error:search_replace cluster, 562 occurrences
    Admiral: empty_after_tool:ralph_file_summary causing stalls
    Admiral: search_replace was raising ToolError when file didn't exist
    """
    signals = classify_text(text)
    buckets = {s.bucket for s in signals}
    assert Bucket.HARNESS in buckets
    # Confirm at least 3 distinct harness patterns matched
    harness = [s for s in signals if s.bucket == Bucket.HARNESS]
    assert len({s.pattern_id for s in harness}) >= 3


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
