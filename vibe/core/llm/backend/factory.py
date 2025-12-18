from __future__ import annotations

from vibe.core.config import Backend
from vibe.core.llm.backend.generic import GenericBackend

BACKEND_FACTORY = {Backend.GENERIC: GenericBackend}
