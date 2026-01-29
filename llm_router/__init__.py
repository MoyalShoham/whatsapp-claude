"""LLM Router module for intent classification and tool routing."""

from llm_router.router import LLMRouter, LLMProvider, StubLLMProvider
from llm_router.schemas import (
    RouterDecision,
    RouterIntent,
    RouterTool,
    ToolArguments,
    Confidence,
    INTENT_TOOL_MAPPING,
    TOOL_VALID_STATES,
    is_tool_valid_for_state,
)
from llm_router.providers import (
    ClaudeLLMProvider,
    MockLLMProvider,
    LLMError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMResponseError,
    create_provider,
    get_default_provider,
)

__all__ = [
    # Router
    "LLMRouter",
    "LLMProvider",
    "StubLLMProvider",
    # Providers
    "ClaudeLLMProvider",
    "MockLLMProvider",
    "create_provider",
    "get_default_provider",
    # Errors
    "LLMError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMResponseError",
    # Schemas
    "RouterDecision",
    "RouterIntent",
    "RouterTool",
    "ToolArguments",
    "Confidence",
    "INTENT_TOOL_MAPPING",
    "TOOL_VALID_STATES",
    "is_tool_valid_for_state",
]
