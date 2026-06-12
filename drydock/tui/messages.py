"""Thread-safe messages posted from the agent worker to the UI.

The agent loop is a synchronous generator that makes blocking network calls,
so it runs in a worker thread. It communicates with the Textual app only by
posting these messages (Textual's post_message is thread-safe).
"""
from __future__ import annotations

from textual.message import Message


class AgentText(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class AgentToolStart(Message):
    def __init__(self, name: str, inputs: dict) -> None:
        self.name = name
        self.inputs = inputs
        super().__init__()


class AgentToolEnd(Message):
    def __init__(self, name: str, result: str) -> None:
        self.name = name
        self.result = result
        super().__init__()


class AgentTurnDone(Message):
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        super().__init__()


class AgentFinished(Message):
    """The whole run (all turns) completed."""


class AgentError(Message):
    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__()
