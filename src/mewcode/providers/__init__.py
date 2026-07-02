from .base import ChatMessage, ChatRequest, LLMProvider, StreamEvent

__all__ = [
    "ChatMessage",
    "ChatRequest",
    "LLMProvider",
    "StreamEvent",
    "create_provider",
]


def __getattr__(name: str):
    if name == "create_provider":
        from .factory import create_provider

        return create_provider
    raise AttributeError(name)
