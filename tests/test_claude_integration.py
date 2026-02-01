"""
Integration tests for Claude LLM Provider.

Tests are automatically skipped if ANTHROPIC_API_KEY is not set in .env file.
Environment loading happens in conftest.py BEFORE test collection.

Run with:
    pytest tests/test_claude_integration.py -v
    pytest tests/test_claude_integration.py -v -m "not slow"  # Skip slow tests
"""

import os
import pytest

from llm_router import ClaudeLLMProvider, LLMRouter
from llm_router.schemas import RouterIntent, RouterTool, RouterDecision
from llm_router.providers import LLMError


# Skip all tests in this module if API key is not set
# Note: load_dotenv() is called in conftest.py BEFORE test collection
pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set - skipping live API tests"
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def claude_provider():
    """Create a real Claude provider instance."""
    return ClaudeLLMProvider()


@pytest.fixture
def claude_router(claude_provider):
    """Create a router with real Claude provider."""
    return LLMRouter(llm_provider=claude_provider)


# ============================================================================
# Integration Tests - Real Claude API Calls
# ============================================================================


class TestClaudeProviderIntegration:
    """Integration tests using real Claude API."""

    def test_provider_initialization(self, claude_provider):
        """Test provider initializes correctly with API key."""
        assert claude_provider.api_key is not None
        assert claude_provider.model == ClaudeLLMProvider.DEFAULT_MODEL
        assert claude_provider.timeout == ClaudeLLMProvider.DEFAULT_TIMEOUT

    def test_simple_completion(self, claude_provider):
        """Test basic completion call to Claude API."""
        response = claude_provider.complete("Say 'Hello' and nothing else.")

        assert response is not None
        assert isinstance(response, str)
        assert len(response) > 0
        assert "hello" in response.lower()


class TestClaudeRouterIntegration:
    """Integration tests for router with real Claude API."""

    def test_route_invoice_approval(self, claude_router):
        """Test routing an invoice approval message."""
        decision = claude_router.route(
            message="I approve invoice INV-001",
            state="awaiting_approval"
        )

        assert isinstance(decision, RouterDecision)
        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.tool == RouterTool.APPROVE_INVOICE
        assert decision.arguments.invoice_id == "INV-001"
        assert decision.confidence.value in ["high", "medium"]
        assert not decision.requires_clarification

    def test_route_payment_confirmation(self, claude_router):
        """Test routing a payment confirmation message."""
        decision = claude_router.route(
            message="I have paid invoice INV-002",
            state="payment_pending"
        )

        assert decision.intent == RouterIntent.PAYMENT_CONFIRMATION
        assert decision.tool == RouterTool.CONFIRM_PAYMENT
        assert decision.arguments.invoice_id == "INV-002"

    def test_route_list_invoices(self, claude_router):
        """Test routing a list invoices request."""
        decision = claude_router.route(
            message="Show me all my invoices",
            state="new"
        )

        assert decision.intent == RouterIntent.LIST_INVOICES
        assert decision.tool == RouterTool.LIST_INVOICES
        assert decision.arguments.invoice_id is None

    def test_route_rejection_with_reason(self, claude_router):
        """Test routing invoice rejection with explicit reason."""
        decision = claude_router.route(
            message="I reject invoice INV-003 because the amount is incorrect",
            state="awaiting_approval"
        )

        assert decision.intent == RouterIntent.INVOICE_REJECTION
        assert decision.tool == RouterTool.REJECT_INVOICE
        assert decision.arguments.invoice_id == "INV-003"

    def test_route_ambiguous_message(self, claude_router):
        """Test routing an ambiguous message triggers clarification."""
        decision = claude_router.route(
            message="What about the invoice?",
            state="new"
        )

        # Ambiguous messages should either return unknown or request clarification
        assert (
            decision.intent == RouterIntent.UNKNOWN or
            decision.requires_clarification
        )


class TestClaudeJSONExtraction:
    """Test JSON extraction from Claude responses."""

    def test_json_extraction_with_code_block(self, claude_router):
        """Test that router correctly extracts JSON from markdown code blocks."""
        decision = claude_router.route(
            message="Approve INV-999",
            state="awaiting_approval"
        )

        # If this doesn't raise, JSON extraction worked
        assert isinstance(decision, RouterDecision)
        assert decision.intent is not None
        assert decision.tool is not None

    def test_invoice_id_extraction_variations(self, claude_router):
        """Test invoice ID extraction with different formats."""
        test_cases = [
            ("I approve INV-001", "001"),
            ("Approve invoice 001", "001"),
            ("I approve #001", "001"),
        ]

        for message, expected_id_part in test_cases:
            decision = claude_router.route(message, "awaiting_approval")

            # ID should be extracted and contain the numeric part
            assert decision.arguments.invoice_id is not None
            assert expected_id_part in decision.arguments.invoice_id


class TestClaudeErrorHandling:
    """Test error handling with real API."""

    def test_router_handles_errors_gracefully(self, claude_router):
        """Test that router handles errors gracefully."""
        # Should not crash, should return valid decision
        decision = claude_router.route("test message", "new")
        assert isinstance(decision, RouterDecision)


# ============================================================================
# Performance Tests (Optional - marked as slow)
# ============================================================================


@pytest.mark.slow
class TestClaudePerformance:
    """Performance tests for Claude API (marked as slow)."""

    def test_response_time_reasonable(self, claude_router):
        """Test that Claude API responds in reasonable time."""
        import time

        start = time.time()
        decision = claude_router.route("Approve INV-001", "awaiting_approval")
        duration = time.time() - start

        # Claude should respond within 10 seconds for simple queries
        assert duration < 10.0
        assert isinstance(decision, RouterDecision)

    def test_multiple_requests_in_sequence(self, claude_router):
        """Test multiple sequential requests work correctly."""
        messages = [
            "Approve INV-001",
            "I paid INV-002",
            "Show me my invoices",
        ]

        for message in messages:
            decision = claude_router.route(message, "new")
            assert isinstance(decision, RouterDecision)
