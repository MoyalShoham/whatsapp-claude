"""
LLM Providers for the invoice automation router.

This module contains provider implementations that can be injected into LLMRouter.
All providers implement the LLMProvider protocol.
"""

import json
import logging
import os
import time
from typing import Any, Optional

from llm_router.schemas import (
    Confidence,
    RouterDecision,
    RouterIntent,
    RouterTool,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Error Types
# ============================================================================


class LLMError(Exception):
    """Base error for LLM-related failures."""

    def __init__(self, message: str, provider: str, retryable: bool = False):
        self.provider = provider
        self.retryable = retryable
        super().__init__(message)


class LLMTimeoutError(LLMError):
    """Raised when LLM call times out."""

    def __init__(self, message: str, provider: str, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        super().__init__(message, provider, retryable=True)


class LLMRateLimitError(LLMError):
    """Raised when rate limited by LLM provider."""

    def __init__(self, message: str, provider: str, retry_after: Optional[float] = None):
        self.retry_after = retry_after
        super().__init__(message, provider, retryable=True)


class LLMResponseError(LLMError):
    """Raised when LLM returns invalid response."""

    def __init__(self, message: str, provider: str, raw_response: Optional[str] = None):
        self.raw_response = raw_response
        super().__init__(message, provider, retryable=False)


# ============================================================================
# Claude Provider
# ============================================================================


class ClaudeLLMProvider:
    """
    Production Claude LLM provider using the Anthropic API.

    Features:
    - Loads API key from ANTHROPIC_API_KEY environment variable
    - Temperature = 0 for deterministic output
    - Timeout protection
    - Retry with exponential backoff (max 2 retries)
    - Structured JSON output enforcement
    """

    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    DEFAULT_TIMEOUT = 30.0  # seconds
    DEFAULT_MAX_RETRIES = 2
    DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        """
        Initialize the Claude provider.

        Args:
            api_key: Anthropic API key. If not provided, reads from ANTHROPIC_API_KEY env var.
            model: Model to use. Defaults to claude-sonnet-4-20250514.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retries on transient failures.
            max_tokens: Maximum tokens in response.

        Raises:
            ValueError: If no API key is available.
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable not set and no api_key provided"
            )

        self.model = model or self.DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens

        # Lazy import to avoid dependency issues in tests
        self._client: Optional[Any] = None

    @property
    def client(self) -> Any:
        """Lazy-load the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(
                    api_key=self.api_key,
                    timeout=self.timeout,
                )
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
        return self._client

    def complete(self, prompt: str) -> str:
        """
        Send prompt to Claude and return response.

        Args:
            prompt: The prompt to send to Claude.

        Returns:
            The model's response text.

        Raises:
            LLMTimeoutError: If request times out.
            LLMRateLimitError: If rate limited.
            LLMResponseError: If response is invalid.
            LLMError: For other failures.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                return self._make_request(prompt)
            except LLMError as e:
                last_error = e
                if not e.retryable or attempt >= self.max_retries:
                    raise

                # Exponential backoff: 1s, 2s, 4s...
                backoff = 2 ** attempt
                logger.warning(
                    f"Claude API call failed (attempt {attempt + 1}/{self.max_retries + 1}), "
                    f"retrying in {backoff}s: {e}"
                )
                time.sleep(backoff)

        # Should not reach here, but just in case
        raise last_error or LLMError("Unknown error", provider="claude")

    def _make_request(self, prompt: str) -> str:
        """Make a single request to Claude API."""
        import anthropic

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0,  # Deterministic output
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            # Extract text from response
            if not response.content:
                raise LLMResponseError(
                    "Empty response from Claude",
                    provider="claude",
                    raw_response=str(response),
                )

            text_content = response.content[0]
            if hasattr(text_content, 'text'):
                return text_content.text
            else:
                raise LLMResponseError(
                    "Unexpected response format from Claude",
                    provider="claude",
                    raw_response=str(response),
                )

        except anthropic.APITimeoutError as e:
            raise LLMTimeoutError(
                f"Claude API timed out after {self.timeout}s",
                provider="claude",
                timeout_seconds=self.timeout,
            ) from e

        except anthropic.RateLimitError as e:
            retry_after = None
            if hasattr(e, 'response') and e.response:
                retry_after_header = e.response.headers.get('retry-after')
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except ValueError:
                        pass

            raise LLMRateLimitError(
                "Claude API rate limited",
                provider="claude",
                retry_after=retry_after,
            ) from e

        except anthropic.APIConnectionError as e:
            raise LLMError(
                f"Failed to connect to Claude API: {e}",
                provider="claude",
                retryable=True,
            ) from e

        except anthropic.APIStatusError as e:
            raise LLMError(
                f"Claude API error: {e}",
                provider="claude",
                retryable=e.status_code >= 500,
            ) from e


# ============================================================================
# Mock Provider for Testing
# ============================================================================


class MockLLMProvider:
    """
    Mock LLM provider for testing.

    Can be configured to return specific responses or raise errors.
    """

    def __init__(
        self,
        responses: Optional[list[str]] = None,
        error_on_call: Optional[int] = None,
        error_type: type[Exception] = LLMError,
    ):
        """
        Initialize mock provider.

        Args:
            responses: List of responses to return in order.
            error_on_call: Call number (0-indexed) on which to raise error.
            error_type: Type of error to raise.
        """
        self.responses = responses or []
        self.error_on_call = error_on_call
        self.error_type = error_type
        self.call_count = 0
        self.prompts_received: list[str] = []

    def complete(self, prompt: str) -> str:
        """Return mocked response."""
        self.prompts_received.append(prompt)

        if self.error_on_call is not None and self.call_count == self.error_on_call:
            self.call_count += 1
            if issubclass(self.error_type, LLMTimeoutError):
                raise LLMTimeoutError("Mock timeout", provider="mock", timeout_seconds=30)
            elif issubclass(self.error_type, LLMRateLimitError):
                raise LLMRateLimitError("Mock rate limit", provider="mock")
            else:
                raise self.error_type("Mock error", provider="mock")

        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
        else:
            # Default response
            response = json.dumps({
                "intent": "unknown",
                "tool": "none",
                "arguments": {},
                "confidence": "low",
                "reasoning": "Mock response",
                "requires_clarification": True,
                "clarification_prompt": "Please clarify",
                "warnings": [],
            })

        self.call_count += 1
        return response

    def set_response(self, intent: RouterIntent, tool: RouterTool, **kwargs: Any) -> None:
        """Helper to set a structured response."""
        response = {
            "intent": intent.value if hasattr(intent, 'value') else intent,
            "tool": tool.value if hasattr(tool, 'value') else tool,
            "arguments": kwargs.get("arguments", {}),
            "confidence": kwargs.get("confidence", "high"),
            "reasoning": kwargs.get("reasoning", "Mock response"),
            "requires_clarification": kwargs.get("requires_clarification", False),
            "clarification_prompt": kwargs.get("clarification_prompt"),
            "warnings": kwargs.get("warnings", []),
        }
        self.responses.append(json.dumps(response))


# ============================================================================
# Provider Factory
# ============================================================================


def create_provider(
    provider_type: str = "stub",
    **kwargs: Any,
) -> Any:
    """
    Factory function to create LLM providers.

    Args:
        provider_type: Type of provider ("claude", "stub", "mock").
        **kwargs: Additional arguments for the provider.

    Returns:
        LLM provider instance.

    Raises:
        ValueError: If provider_type is unknown.
    """
    from llm_router.router import StubLLMProvider

    providers = {
        "claude": ClaudeLLMProvider,
        "stub": StubLLMProvider,
        "mock": MockLLMProvider,
    }

    if provider_type not in providers:
        raise ValueError(f"Unknown provider type: {provider_type}. Available: {list(providers.keys())}")

    return providers[provider_type](**kwargs)


def get_default_provider() -> Any:
    """
    Get the default provider based on environment.

    Returns ClaudeLLMProvider if ANTHROPIC_API_KEY is set, otherwise StubLLMProvider.
    """
    from llm_router.router import StubLLMProvider

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        logger.info("Using Claude LLM provider")
        return ClaudeLLMProvider(api_key=api_key)
    else:
        logger.warning("ANTHROPIC_API_KEY not set, using stub provider")
        return StubLLMProvider()
