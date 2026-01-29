"""Tests for the LLM Router."""

import json
import pytest

from llm_router import (
    LLMRouter,
    RouterDecision,
    RouterIntent,
    RouterTool,
    StubLLMProvider,
    Confidence,
    is_tool_valid_for_state,
)


@pytest.fixture
def router() -> LLMRouter:
    """Create a router with stub provider."""
    return LLMRouter()


class TestHappyPathRouting:
    """Test successful routing scenarios."""

    def test_route_approval_with_invoice_id(self, router: LLMRouter) -> None:
        """Test routing a clear approval request."""
        decision = router.route(
            message="I approve invoice INV-001",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.tool == RouterTool.APPROVE_INVOICE
        assert decision.arguments.invoice_id == "INV-001"
        assert decision.confidence in [Confidence.HIGH, Confidence.MEDIUM]
        assert not decision.requires_clarification

    def test_route_rejection_with_reason(self, router: LLMRouter) -> None:
        """Test routing a rejection with reason."""
        decision = router.route(
            message="I want to reject invoice INV-002 because the amount is wrong",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_REJECTION
        assert decision.tool == RouterTool.REJECT_INVOICE
        assert decision.arguments.invoice_id == "INV-002"

    def test_route_payment_confirmation(self, router: LLMRouter) -> None:
        """Test routing a payment confirmation."""
        decision = router.route(
            message="I have paid invoice INV-003",
            state="payment_pending",
        )

        assert decision.intent == RouterIntent.PAYMENT_CONFIRMATION
        assert decision.tool == RouterTool.CONFIRM_PAYMENT
        assert decision.arguments.invoice_id == "INV-003"

    def test_route_status_question(self, router: LLMRouter) -> None:
        """Test routing a status question."""
        decision = router.route(
            message="What is the status of invoice INV-004?",
            state="invoice_sent",
        )

        assert decision.intent == RouterIntent.INVOICE_QUESTION
        assert decision.tool == RouterTool.GET_INVOICE_STATUS
        assert decision.arguments.invoice_id == "INV-004"

    def test_route_dispute_request(self, router: LLMRouter) -> None:
        """Test routing a dispute request."""
        decision = router.route(
            message="I want to dispute invoice INV-005, the charges are incorrect",
            state="approved",
        )

        assert decision.intent == RouterIntent.INVOICE_DISPUTE
        assert decision.tool == RouterTool.CREATE_DISPUTE
        assert decision.arguments.invoice_id == "INV-005"

    def test_route_resend_request(self, router: LLMRouter) -> None:
        """Test routing an invoice resend request."""
        decision = router.route(
            message="Please resend invoice INV-006",
            state="invoice_sent",
        )

        assert decision.intent == RouterIntent.REQUEST_INVOICE_COPY
        assert decision.tool == RouterTool.RESEND_INVOICE
        assert decision.arguments.invoice_id == "INV-006"


class TestAmbiguousMessages:
    """Test handling of ambiguous or unclear messages."""

    def test_ambiguous_message_returns_unknown(self, router: LLMRouter) -> None:
        """Test that ambiguous messages return unknown intent."""
        decision = router.route(
            message="hmm",
            state="new",
        )

        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.tool == RouterTool.NONE
        assert decision.confidence == Confidence.LOW
        assert decision.requires_clarification

    def test_vague_message_requests_clarification(self, router: LLMRouter) -> None:
        """Test that vague messages request clarification."""
        decision = router.route(
            message="What about it?",
            state="invoice_sent",
        )

        assert decision.requires_clarification
        assert decision.clarification_prompt is not None

    def test_missing_invoice_id_requests_clarification(self, router: LLMRouter) -> None:
        """Test that missing invoice ID triggers clarification."""
        decision = router.route(
            message="I approve the invoice",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.requires_clarification
        assert "invoice" in decision.clarification_prompt.lower()

    def test_rejection_without_reason_requests_clarification(self, router: LLMRouter) -> None:
        """Test that rejection without reason requests clarification."""
        decision = router.route(
            message="Reject INV-001",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_REJECTION
        assert decision.requires_clarification
        assert "reason" in decision.clarification_prompt.lower()


class TestHallucinationPrevention:
    """Test that the router prevents hallucinations."""

    def test_does_not_invent_invoice_id(self, router: LLMRouter) -> None:
        """Test that router doesn't invent invoice IDs."""
        decision = router.route(
            message="Approve my invoice",
            state="awaiting_approval",
        )

        # Should not have made up an invoice ID
        assert decision.arguments.invoice_id is None
        # Should request clarification instead
        assert decision.requires_clarification

    def test_future_payment_not_confirmation(self, router: LLMRouter) -> None:
        """Test that future payment intent is not treated as confirmation."""
        decision = router.route(
            message="I will pay tomorrow",
            state="payment_pending",
        )

        # Should NOT be payment confirmation
        assert decision.intent != RouterIntent.PAYMENT_CONFIRMATION
        # Should have warning about future payment
        assert any("future" in w.lower() or "will pay" in w.lower() for w in decision.warnings)

    def test_planning_to_pay_not_confirmation(self, router: LLMRouter) -> None:
        """Test 'planning to pay' is not a confirmation."""
        decision = router.route(
            message="I'm going to pay invoice INV-001 next week",
            state="payment_pending",
        )

        assert decision.intent != RouterIntent.PAYMENT_CONFIRMATION

    def test_does_not_assume_payment_success(self, router: LLMRouter) -> None:
        """Test that router doesn't assume payment was successful."""
        decision = router.route(
            message="I tried to pay but I'm not sure if it went through",
            state="payment_pending",
        )

        # Should not confirm payment on uncertainty
        assert decision.tool != RouterTool.CONFIRM_PAYMENT or decision.requires_clarification


class TestToolMismatch:
    """Test handling of tool-state mismatches."""

    def test_approval_wrong_state_has_warning(self, router: LLMRouter) -> None:
        """Test that approval from wrong state includes warning."""
        decision = router.route(
            message="I approve invoice INV-001",
            state="new",  # Wrong state
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        # Should have warning about state mismatch
        assert len(decision.warnings) > 0
        assert any("state" in w.lower() for w in decision.warnings)

    def test_payment_wrong_state_has_warning(self, router: LLMRouter) -> None:
        """Test that payment confirmation from wrong state includes warning."""
        decision = router.route(
            message="I have paid invoice INV-001",
            state="awaiting_approval",  # Wrong state
        )

        assert any("state" in w.lower() for w in decision.warnings)

    def test_dispute_wrong_state_has_warning(self, router: LLMRouter) -> None:
        """Test that dispute from wrong state includes warning."""
        decision = router.route(
            message="I want to dispute invoice INV-001",
            state="new",  # Wrong state
        )

        assert any("state" in w.lower() for w in decision.warnings)


class TestInvalidJSONRecovery:
    """Test recovery from invalid JSON responses."""

    def test_recover_from_malformed_json(self, router: LLMRouter) -> None:
        """Test recovery when LLM returns malformed JSON."""
        # Create a provider that returns bad JSON
        class BadJSONProvider:
            def complete(self, prompt: str) -> str:
                return "This is not valid JSON at all!"

        router = LLMRouter(llm_provider=BadJSONProvider())
        decision = router.route(
            message="Approve INV-001",
            state="awaiting_approval",
        )

        # Should return fallback decision
        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.confidence == Confidence.LOW
        assert decision.requires_clarification
        assert len(decision.warnings) > 0

    def test_recover_from_partial_json(self, router: LLMRouter) -> None:
        """Test recovery from incomplete JSON."""
        class PartialJSONProvider:
            def complete(self, prompt: str) -> str:
                return '{"intent": "invoice_approval", "tool":'  # Incomplete

        router = LLMRouter(llm_provider=PartialJSONProvider())
        decision = router.route(
            message="Approve INV-001",
            state="awaiting_approval",
        )

        # Should return fallback
        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.requires_clarification

    def test_recover_from_wrong_schema(self, router: LLMRouter) -> None:
        """Test recovery when JSON doesn't match schema."""
        class WrongSchemaProvider:
            def complete(self, prompt: str) -> str:
                return json.dumps({
                    "intent": "invalid_intent",  # Invalid enum value
                    "tool": "approve_invoice",
                    "arguments": {},
                })

        router = LLMRouter(llm_provider=WrongSchemaProvider())
        decision = router.route(
            message="Approve INV-001",
            state="awaiting_approval",
        )

        # Should return fallback
        assert decision.intent == RouterIntent.UNKNOWN

    def test_extract_json_from_markdown(self, router: LLMRouter) -> None:
        """Test JSON extraction from markdown code blocks."""
        class MarkdownProvider:
            def complete(self, prompt: str) -> str:
                return """Here's my analysis:

```json
{
  "intent": "invoice_approval",
  "tool": "approve_invoice",
  "arguments": {"invoice_id": "INV-001"},
  "confidence": "high",
  "reasoning": "Clear approval request",
  "requires_clarification": false,
  "clarification_prompt": null,
  "warnings": []
}
```

That's my recommendation."""

        router = LLMRouter(llm_provider=MarkdownProvider())
        decision = router.route(
            message="Approve INV-001",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.arguments.invoice_id == "INV-001"


class TestRouterDecisionMethods:
    """Test RouterDecision helper methods."""

    def test_is_actionable_true(self) -> None:
        """Test is_actionable returns True for valid decisions."""
        decision = RouterDecision(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            confidence=Confidence.HIGH,
            requires_clarification=False,
        )

        assert decision.is_actionable()

    def test_is_actionable_false_unknown(self) -> None:
        """Test is_actionable returns False for unknown intent."""
        decision = RouterDecision(
            intent=RouterIntent.UNKNOWN,
            tool=RouterTool.NONE,
            confidence=Confidence.LOW,
        )

        assert not decision.is_actionable()

    def test_is_actionable_false_clarification_needed(self) -> None:
        """Test is_actionable returns False when clarification needed."""
        decision = RouterDecision(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            confidence=Confidence.HIGH,
            requires_clarification=True,
        )

        assert not decision.is_actionable()

    def test_is_actionable_false_low_confidence(self) -> None:
        """Test is_actionable returns False for low confidence."""
        decision = RouterDecision(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            confidence=Confidence.LOW,
        )

        assert not decision.is_actionable()

    def test_to_execution_dict(self) -> None:
        """Test conversion to execution dictionary."""
        decision = RouterDecision(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-001"},
            confidence=Confidence.HIGH,
        )

        exec_dict = decision.to_execution_dict()

        assert exec_dict["tool"] == "approve_invoice"
        assert exec_dict["arguments"]["invoice_id"] == "INV-001"


class TestStateToolValidation:
    """Test state-tool validation functions."""

    def test_approve_valid_from_awaiting_approval(self) -> None:
        """Test approve is valid from awaiting_approval."""
        assert is_tool_valid_for_state(RouterTool.APPROVE_INVOICE, "awaiting_approval")

    def test_approve_invalid_from_new(self) -> None:
        """Test approve is invalid from new state."""
        assert not is_tool_valid_for_state(RouterTool.APPROVE_INVOICE, "new")

    def test_get_status_valid_from_any(self) -> None:
        """Test get_status is valid from any state."""
        states = ["new", "invoice_sent", "awaiting_approval", "approved", "paid", "closed"]
        for state in states:
            assert is_tool_valid_for_state(RouterTool.GET_INVOICE_STATUS, state)

    def test_confirm_payment_only_from_payment_pending(self) -> None:
        """Test confirm_payment only valid from payment_pending."""
        assert is_tool_valid_for_state(RouterTool.CONFIRM_PAYMENT, "payment_pending")
        assert not is_tool_valid_for_state(RouterTool.CONFIRM_PAYMENT, "approved")
        assert not is_tool_valid_for_state(RouterTool.CONFIRM_PAYMENT, "awaiting_approval")


class TestInvoiceIDExtraction:
    """Test invoice ID extraction from various formats."""

    def test_extract_inv_format(self, router: LLMRouter) -> None:
        """Test extraction of INV-XXX format."""
        decision = router.route("Approve INV-001", state="awaiting_approval")
        assert decision.arguments.invoice_id == "INV-001"

    def test_extract_numeric_format(self, router: LLMRouter) -> None:
        """Test extraction of numeric invoice ID."""
        decision = router.route("Approve invoice 12345", state="awaiting_approval")
        assert decision.arguments.invoice_id == "INV-12345"

    def test_extract_hash_format(self, router: LLMRouter) -> None:
        """Test extraction of #XXX format."""
        decision = router.route("Approve #001", state="awaiting_approval")
        assert decision.arguments.invoice_id == "INV-001"

    def test_no_invoice_id_in_message(self, router: LLMRouter) -> None:
        """Test when no invoice ID is present."""
        decision = router.route("Approve the invoice", state="awaiting_approval")
        assert decision.arguments.invoice_id is None
        assert decision.requires_clarification


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_message(self, router: LLMRouter) -> None:
        """Test handling of empty message."""
        decision = router.route(message="", state="new")

        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.requires_clarification

    def test_very_long_message(self, router: LLMRouter) -> None:
        """Test handling of very long message."""
        long_message = "I want to approve invoice INV-001. " * 100
        decision = router.route(message=long_message, state="awaiting_approval")

        # Should still detect intent correctly
        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.arguments.invoice_id == "INV-001"

    def test_special_characters_in_message(self, router: LLMRouter) -> None:
        """Test handling of special characters."""
        decision = router.route(
            message="Approve INV-001!!! @#$%^&*()",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.arguments.invoice_id == "INV-001"

    def test_mixed_case_message(self, router: LLMRouter) -> None:
        """Test handling of mixed case."""
        decision = router.route(
            message="APPROVE invoice inv-001",
            state="awaiting_approval",
        )

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.arguments.invoice_id == "INV-001"

    def test_context_is_preserved(self, router: LLMRouter) -> None:
        """Test that context is passed through."""
        decision = router.route(
            message="What's the status?",
            state="invoice_sent",
            context={
                "invoice_id": "INV-001",
                "conversation_history": [
                    {"role": "user", "content": "Hello"},
                ],
            },
        )

        # Router should work with context
        assert decision is not None
        assert isinstance(decision, RouterDecision)
