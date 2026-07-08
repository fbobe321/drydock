"""Auto-retry on server stall (config stall_retry_secs, default 0=off): a hung
model call (server produces nothing for stall_secs) raises StallRetry so the
agent abandons the wedged request and re-issues it. Plus the self-verify /
run-test-script / act prompt guidance."""
from __future__ import annotations

import time

from drydock.providers import StallRetry, _create_abortable
from drydock import config
from drydock.tuning import system_prompt_for_model


class _HangingClient:
    class chat:
        class completions:
            @staticmethod
            def create(**kw):
                time.sleep(60)  # simulate a wedged server


def test_stall_retry_raised_on_hang():
    t0 = time.monotonic()
    raised = False
    try:
        _create_abortable(_HangingClient(), {}, "url", "vllm", None,
                          timeout_s=600, stall_secs=1)
    except StallRetry:
        raised = True
    assert raised and (time.monotonic() - t0) < 4


def test_stall_retry_disabled_by_default():
    assert config.DEFAULTS.get("stall_retry_secs") == 0


def test_no_stall_when_secs_zero_returns_quickly():
    # stall_secs=0 → never raises StallRetry; a fast create returns normally
    class Fast:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return "ok"
    assert _create_abortable(Fast(), {}, "u", "vllm", None, stall_secs=0) == "ok"


def test_prompts_have_verify_and_act_guidance():
    for m in ("gemma4", "some-other-model"):
        p = system_prompt_for_model(m)
        assert "RE-READ the task" in p
        assert "eval.py" in p and "test.sh" in p        # run-the-test-script
        assert "Prefer acting" in p                      # act, don't over-plan
