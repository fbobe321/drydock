"""The system prompt must teach the model Drydock's own slash commands so it can
answer 'how do I add documents / make a skill' when a user asks."""
from __future__ import annotations

from drydock.tuning import system_prompt_for_model


def test_prompt_documents_graphrag_and_skills():
    for model in ("gemma4", "some-other-model"):
        p = system_prompt_for_model(model)
        assert "/graphrag build" in p and "/graphrag add" in p
        assert "/skills new" in p
        assert "Knowledge" in p
        # framed so the model explains, not runs, the command
        assert "you do" in p.lower() and "not run" in p.lower()
