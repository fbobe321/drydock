"""context_limit must be a config.toml setting (not hardcoded), so users whose
model server runs a smaller window can lower it — otherwise drydock overflows
the real context before compaction fires. Reported from real use: OOM at 32k
while the hardcoded limit was 65536, and /compact found 'nothing to compact'."""
from __future__ import annotations

from drydock import config as cfgmod
from drydock.agent import AgentState
from drydock.compaction import estimate_tokens
from drydock import cli


def test_context_limit_is_a_default_and_written_to_file(tmp_path):
    path = tmp_path / "config.toml"
    cfg = cfgmod.resolve({}, path)
    assert "context_limit" in cfg                      # present in resolved config
    assert "context_limit" in path.read_text()         # persisted to the file


def test_config_file_value_is_respected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gemma4"\ncontext_limit = 32768\n')
    cfg = cfgmod.resolve({}, path)
    assert cfg["context_limit"] == 32768


def test_cli_flag_overrides_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('context_limit = 32768\n')
    cfg = cfgmod.resolve({"context_limit": 16384}, path)
    assert cfg["context_limit"] == 16384


def test_compact_escalates_when_bloat_is_not_tool_results(capsys):
    # History dominated by big ASSISTANT text — normal compact() barely helps,
    # so an explicit /compact must escalate (emergency_compact) and free space.
    state = AgentState()
    state.messages = [{"role": "user", "content": "start"}]
    for i in range(8):
        state.messages.append({"role": "assistant", "content": f"answer {i} " + "x" * 4000})
    before = estimate_tokens(state.messages)
    cli.handle_command("/compact", state, {"context_limit": 16384})
    after = estimate_tokens(state.messages)
    assert after < before * 0.7      # escalation actually reclaimed space
