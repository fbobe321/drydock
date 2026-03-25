from __future__ import annotations

from drydock.core.config import Backend
from drydock.core.llm.backend.generic import GenericBackend
from drydock.core.llm.backend.mistral import MistralBackend

BACKEND_FACTORY = {Backend.MISTRAL: MistralBackend, Backend.GENERIC: GenericBackend}
