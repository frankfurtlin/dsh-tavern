"""Completion-adapter selection for the native tavern generation path.

The native app talks to providers synchronously through tavern.stream; there is
no background task queue anymore.
"""
from .config import settings
from .providers import ChatCompletionsAdapter, DshAgentAdapter, MockAdapter


def adapter_for(mode=None):
    """Pick the completion adapter for the configured AI mode."""
    mode = settings.ai_mode if mode is None else mode
    if mode == "mock":
        return MockAdapter()
    if mode == "dsh":
        return DshAgentAdapter()
    return ChatCompletionsAdapter()
