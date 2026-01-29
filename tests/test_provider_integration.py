"""Integration tests for LLM providers."""

import json
import pytest

from llm_router import (
    LLMRouter,
    MockLLMProvider,
    RouterIntent,
    RouterTool,
    Confidence,
    LLMError,
    LLMTimeoutError,
)


class TestMockProviderIntegration:
    """Test LLMRouter with MockLLMProvider for isolated testing."""

    def test_router_with_mock_provider_approval(self) -> None:
        """Test end-to-end routing with mocked approval response."""
        mock = MockLLMProvider()
        mock.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
            reasoning="User explicitly requested approval",
        )

        router = LLMRouter(llm_provider=mock)
        decision = router.route(
            message="Please approve invoice INV-001",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.tool == RouterTool.APPROVE_INVOICE
        assert decision.arguments.invoice_id == "INV-001"
        assert decision.confidence == Confidence.HIGH
        assert len(mock.prompts_received) == 1

    def test_router_with_mock_provider_payment(self) -> None:
        """Test end-to-end routing with mocked payment response."""
        mock = MockLLMProvider()
        mock.set_response(
            intent=RouterIntent.PAYMENT_CONFIRMATION,
            tool=RouterTool.CONFIRM_PAYMENT,
            arguments={"invoice_id": "INV-002"},
            confidence="high",
        )

        router = LLMRouter(llm_provider=mock)
        decision = router.route(
            message="I have paid invoice INV-002",
            state="payment_pending",
        )

        assert decision.intent == RouterIntent.PAYMENT_CONFIRMATION
        assert decision.tool == RouterTool.CONFIRM_PAYMENT

    def test_router_with_mock_provider_unknown(self) -> None:
        """Test routing falls back to unknown on ambiguous response."""
        mock = MockLLMProvider()
        mock.set_response(
            intent=RouterIntent.UNKNOWN,
            tool=RouterTool.NONE,
            confidence="low",
            requires_clarification=True,
            clarification_prompt="What would you like to do?",
        )

        router = LLMRouter(llm_provider=mock)
        decision = router.route(
            message="hmm",
            state="new",
        )

        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.tool == RouterTool.NONE
        assert decision.confidence == Confidence.LOW
        assert decision.requires_clarification

    def test_router_handles_timeout_gracefully(self) -> None:
        """Test router returns fallback decision on timeout."""
        mock = MockLLMProvider(
            error_on_call=0,
            error_type=LLMTimeoutError,
        )

        router = LLMRouter(llm_provider=mock)
        decision = router.route(
            message="Approve INV-001",
            state="awaiting_approval",
        )

        # Should return fallback decision, not crash
        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.confidence == Confidence.LOW
        assert decision.requires_clarification
        assert any("error" in w.lower() for w in decision.warnings)

    def test_router_handles_invalid_json(self) -> None:
        """Test router handles invalid JSON from provider."""
        mock = MockLLMProvider(responses=["This is not valid JSON at all!"])

        router = LLMRouter(llm_provider=mock)
        decision = router.route(
            message="Approve INV-001",
            state="awaiting_approval",
        )

        # Should return fallback
        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.requires_clarification

    def test_router_validates_state_tool_mismatch(self) -> None:
        """Test router adds warnings for state-tool mismatches."""
        mock = MockLLMProvider()
        mock.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
        )

        router = LLMRouter(llm_provider=mock)
        decision = router.route(
            message="Approve INV-001",
            state="new",  # Wrong state for approval
        )

        # Should have warning about state mismatch
        assert any("state" in w.lower() for w in decision.warnings)

    def test_mock_provider_tracks_calls(self) -> None:
        """Test mock provider properly tracks all calls."""
        mock = MockLLMProvider()
        mock.set_response(intent=RouterIntent.INVOICE_QUESTION, tool=RouterTool.GET_INVOICE_STATUS)
        mock.set_response(intent=RouterIntent.INVOICE_APPROVAL, tool=RouterTool.APPROVE_INVOICE)

        router = LLMRouter(llm_provider=mock)

        # First call
        router.route("What is the status?", state="new")
        assert mock.call_count == 1
        assert "What is the status?" in mock.prompts_received[0]

        # Second call
        router.route("Approve it", state="awaiting_approval")
        assert mock.call_count == 2
        assert "Approve it" in mock.prompts_received[1]

    def test_multiple_routing_decisions_isolated(self) -> None:
        """Test multiple routing calls are isolated."""
        mock = MockLLMProvider()
        mock.set_response(
            intent=RouterIntent.INVOICE_QUESTION,
            tool=RouterTool.GET_INVOICE_STATUS,
            arguments={"invoice_id": "INV-001"},
        )
        mock.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-002"},
        )

        router = LLMRouter(llm_provider=mock)

        # First decision
        decision1 = router.route("Status of INV-001?", state="new")
        assert decision1.arguments.invoice_id == "INV-001"
        assert decision1.intent == RouterIntent.INVOICE_QUESTION

        # Second decision - independent
        decision2 = router.route("Approve INV-002", state="awaiting_approval")
        assert decision2.arguments.invoice_id == "INV-002"
        assert decision2.intent == RouterIntent.INVOICE_APPROVAL


class TestProviderSwappability:
    """Test that providers are swappable without code changes."""

    def test_router_accepts_any_provider_with_complete_method(self) -> None:
        """Test router works with any object that has complete() method."""

        class CustomProvider:
            def complete(self, prompt: str) -> str:
                return json.dumps({
                    "intent": "invoice_question",
                    "tool": "get_invoice_status",
                    "arguments": {"invoice_id": "CUSTOM-001"},
                    "confidence": "high",
                    "reasoning": "Custom provider response",
                    "requires_clarification": False,
                    "clarification_prompt": None,
                    "warnings": [],
                })

        router = LLMRouter(llm_provider=CustomProvider())
        decision = router.route("Check status", state="new")

        assert decision.arguments.invoice_id == "CUSTOM-001"
        assert decision.reasoning == "Custom provider response"

    def test_provider_can_be_replaced_at_runtime(self) -> None:
        """Test provider can be swapped after router creation."""
        mock1 = MockLLMProvider()
        mock1.set_response(intent=RouterIntent.UNKNOWN, tool=RouterTool.NONE)

        router = LLMRouter(llm_provider=mock1)
        decision1 = router.route("test", state="new")
        assert decision1.intent == RouterIntent.UNKNOWN

        # Swap provider
        mock2 = MockLLMProvider()
        mock2.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
        )
        router.llm_provider = mock2

        decision2 = router.route("test", state="new")
        assert decision2.intent == RouterIntent.INVOICE_APPROVAL


class TestDeterministicOutput:
    """Test that routing produces deterministic results."""

    def test_same_input_same_output(self) -> None:
        """Test same input produces same output with mock."""
        response = json.dumps({
            "intent": "invoice_approval",
            "tool": "approve_invoice",
            "arguments": {"invoice_id": "INV-001"},
            "confidence": "high",
            "reasoning": "Deterministic test",
            "requires_clarification": False,
            "clarification_prompt": None,
            "warnings": [],
        })

        # Create two routers with identical mocks
        mock1 = MockLLMProvider(responses=[response])
        mock2 = MockLLMProvider(responses=[response])

        router1 = LLMRouter(llm_provider=mock1)
        router2 = LLMRouter(llm_provider=mock2)

        decision1 = router1.route("Approve INV-001", state="awaiting_approval")
        decision2 = router2.route("Approve INV-001", state="awaiting_approval")

        # Should be identical
        assert decision1.intent == decision2.intent
        assert decision1.tool == decision2.tool
        assert decision1.arguments.invoice_id == decision2.arguments.invoice_id
        assert decision1.confidence == decision2.confidence
