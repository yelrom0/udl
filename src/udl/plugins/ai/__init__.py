"""
---------------
- ai (kind) -
---------------

udl.ai: an AI provider that answers chats (a local model server, a hosted API), with its models,
its key and what it used. The kind's interface is udl.plugins.ai.base.
"""

# Local Imports
from udl.plugins.ai.base import (
    AIProvider,
    AIProviderConnection,
    ChatChunk,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderOptions,
)

__all__ = [
    "AIProvider",
    "AIProviderConnection",
    "ChatChunk",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ProviderOptions",
]
