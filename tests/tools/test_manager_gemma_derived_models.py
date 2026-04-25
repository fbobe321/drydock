"""Regression tests for issue #10: navygpt (Gemma-derived) hits the
`todo(read)` loop because the original auto-disable check matched only
"gemma" in the model name.

Loop-prone tools (`todo`, `task_create/update/list`, `ask_user_question`,
`invoke_skill`, `tool_search`) need to stay disabled for any small-active-
parameter Gemma-class model regardless of the alias the operator gives
it on their vLLM instance.
"""
from __future__ import annotations

import pytest

from tests.conftest import build_test_drydock_config
from drydock.core.config import ModelConfig
from drydock.core.tools.manager import ToolManager


def _config_with_model(name: str, alias: str | None = None):
    return build_test_drydock_config(
        system_prompt_id="tests",
        include_project_context=False,
        models=[
            ModelConfig(
                name=name,
                provider="mistral",
                alias=alias if alias is not None else name,
            )
        ],
    )


class TestGemmaDerivedAutoDisable:
    @pytest.mark.parametrize("model_name", [
        "gemma4",
        "Gemma-4-26B-A4B-it-AWQ-4bit",
        "google/gemma-2-2b-it",
        "navygpt",
        "navygpt-7b",
        "Custom-NavyGPT-Tuned",
    ])
    def test_loop_prone_tools_disabled(self, model_name):
        config = _config_with_model(model_name)
        manager = ToolManager(lambda: config)
        tools = manager.available_tools

        # The set that misfires on small-active-param models.
        for blocked in ("todo", "task_create", "task_update", "task_list",
                        "ask_user_question", "invoke_skill", "tool_search"):
            assert blocked not in tools, (
                f"{blocked} should be auto-disabled for {model_name}"
            )

    @pytest.mark.parametrize("model_name", [
        "claude-sonnet-4-6",
        "mistral-vibe-cli-latest",
        "gpt-4o",
        "devstral-small-2",
    ])
    def test_full_size_models_keep_loop_prone_tools(self, model_name):
        config = _config_with_model(model_name)
        manager = ToolManager(lambda: config)
        tools = manager.available_tools
        # `todo` is the canonical canary — it should remain available
        # for full-size models that don't need the scaffolding.
        assert "todo" in tools

    def test_alias_matches_when_name_does_not(self):
        # Operator gives the model a non-Gemma name but the alias hints
        # at the family. Auto-disable should still fire on alias.
        config = _config_with_model(name="local-vllm-1", alias="navygpt")
        manager = ToolManager(lambda: config)
        tools = manager.available_tools
        assert "todo" not in tools
