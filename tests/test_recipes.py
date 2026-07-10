"""Recipe retrieval + injection: the right technique recipe reaches the model for a
task, nothing is injected for irrelevant chat, and it's gated by config `recipes`."""
from __future__ import annotations

from drydock import agent
from drydock.providers import AssistantTurn
from drydock.recipes import recipe_context, retrieve_recipes


def _titles(text, k=2):
    return [t for t, _ in retrieve_recipes(text, k)]


def test_matches_expected_recipe():
    assert "Recover" in _titles("recover the deleted password forensic launchcode")[0]
    assert "git" in _titles("remove the secret from git history sanitize")[0].lower()
    assert any("NumPy" in t for t in _titles("fix numpy 2.0 compatibility cython build"))
    assert "openssl" in _titles("self-signed certificate openssl x509")[0].lower()


def test_no_injection_for_chat():
    assert retrieve_recipes("hello, how are you today?") == []
    assert recipe_context("just say hi") == ""


def test_recipe_context_wraps_hits():
    ctx = recipe_context("recover deleted password from binary file")
    assert "TECHNIQUE RECIPES" in ctx and "strings" in ctx


def _run_capture(cfg, msg):
    seen = {}
    def fake_stream(model, system, messages, tool_schemas, config):
        seen["system"] = system
        yield AssistantTurn("done", [], 5, 5)
    import drydock.agent as A
    orig = A.stream
    A.stream = fake_stream
    try:
        list(agent.run(msg, agent.AgentState(), cfg, "BASE-PROMPT"))
    finally:
        A.stream = orig
    return seen["system"]


def test_injected_when_enabled():
    cfg = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536, "recipes": True}
    sysp = _run_capture(cfg, "recover the deleted password from launchcode.txt forensic")
    assert "TECHNIQUE RECIPES" in sysp and "strings" in sysp


def test_not_injected_when_disabled():
    cfg = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536, "recipes": False}
    sysp = _run_capture(cfg, "recover the deleted password from launchcode.txt forensic")
    assert "TECHNIQUE RECIPES" not in sysp


def test_ml_skills_bundled():
    from drydock.skills import load_skills
    sk = load_skills(".")
    for name in ("ml-train", "ml-metrics", "ml-finetune", "ml-debug", "ml-rl", "ml-data"):
        assert name in sk, f"bundled ML skill /{name} missing"
    assert "MCC" in sk["ml-metrics"].body or "mcc" in sk["ml-metrics"].body.lower()


def test_retrieve_handles_non_string():
    from drydock.recipes import retrieve_recipes, recipe_context
    for v in (b"train a cnn", ["train", "cnn"], 5, None):
        assert isinstance(retrieve_recipes(v), list)   # never raises
    assert isinstance(recipe_context(123), str)
