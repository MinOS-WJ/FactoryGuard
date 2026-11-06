"""Grounded operational assistant for the merchant console."""

from .context import CameraView, OperationalContext, build_operational_context
from .llm import LLMConfig, LLMError, OpenAICompatibleClient
from .service import AssistantReply, FactoryAssistant, PreparedQuestion

__all__ = [
    "AssistantReply",
    "CameraView",
    "FactoryAssistant",
    "LLMConfig",
    "LLMError",
    "OpenAICompatibleClient",
    "OperationalContext",
    "PreparedQuestion",
    "build_operational_context",
]
