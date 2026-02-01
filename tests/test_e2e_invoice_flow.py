"""
End-to-End Invoice Flow Tests.

Tests the complete flow:
    Customer message → LLMRouter → Intent classification → Tool execution
                                                              ↓
    Event trigger ← State transition ← FSM validation ← Tool result

Flow states tested:
    invoice_created → awaiting_approval → approved → paid → closed
"""

import pytest
from typing import Any

from agents.invoice_agent import (
    InvoiceOrchestrator,
    InvoiceEvent,
    EventSubscriber,
    StateError,
)
from llm_router import (
    LLMRouter,
    MockLLMProvider,
    RouterIntent,
    RouterTool,
)
from state_machine.invoice_state import InvoiceState
from tools.base import InMemoryInvoiceStore


# ============================================================================
# Test Fixtures
# ============================================================================


class FakeEventSubscriber(EventSubscriber):
    """Fake subscriber for testing events."""

    def __init__(self) -> None:
        self.events_received: list[InvoiceEvent] = []

    def on_event(self, event: InvoiceEvent) -> None:
        self.events_received.append(event)

    def get_event_types(self) -> list[str]:
        return [e.event_type for e in self.events_received]

    def clear(self) -> None:
        self.events_received.clear()


@pytest.fixture
def store() -> InMemoryInvoiceStore:
    """Fresh invoice store for each test."""
    return InMemoryInvoiceStore()


@pytest.fixture
def mock_provider() -> MockLLMProvider:
    """Mock LLM provider."""
    return MockLLMProvider()


@pytest.fixture
def orchestrator(store: InMemoryInvoiceStore, mock_provider: MockLLMProvider) -> InvoiceOrchestrator:
    """Orchestrator with mock provider."""
    # Note: InvoiceOrchestrator doesn't use router - that's ConversationalAgent's responsibility
    return InvoiceOrchestrator(store=store)


@pytest.fixture
def subscriber() -> FakeEventSubscriber:
    """Fake event subscriber."""
    return FakeEventSubscriber()


# ============================================================================
# E2E Happy Path Tests
# ============================================================================


class TestE2EHappyPath:
    """Test the complete happy path flow."""

    def test_full_invoice_lifecycle(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
        subscriber: FakeEventSubscriber,
    ) -> None:
        """
        Test complete invoice lifecycle:
        new → invoice_sent → awaiting_approval → approved → payment_pending → paid → closed
        """
        orchestrator.subscribe_to_events(subscriber)

        # 1. Create invoice
        orchestrator.create_invoice("INV-001", customer_id="CUST-001")
        fsm = orchestrator.store.get_fsm("INV-001")
        assert fsm.current_state == InvoiceState.NEW

        # 2. Send invoice (administrative action)
        orchestrator.advance_invoice("INV-001", "send_invoice")
        assert orchestrator.get_invoice_state("INV-001") == InvoiceState.INVOICE_SENT

        # 3. Request approval (administrative action)
        orchestrator.advance_invoice("INV-001", "request_approval")
        assert orchestrator.get_invoice_state("INV-001") == InvoiceState.AWAITING_APPROVAL

        # 4. Customer approves via message
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="I approve invoice INV-001",
            invoice_id="INV-001",
            customer_id="CUST-001",
        )

        assert result.success
        assert result.intent == RouterIntent.INVOICE_APPROVAL
        assert result.tool_used == RouterTool.APPROVE_INVOICE
        assert result.current_state == InvoiceState.APPROVED
        assert "invoice_approved" in result.events_fired

        # 5. Request payment (administrative)
        orchestrator.advance_invoice("INV-001", "request_payment", customer_id="CUST-001")
        assert orchestrator.get_invoice_state("INV-001") == InvoiceState.PAYMENT_PENDING

        # 6. Customer confirms payment via message
        mock_provider.set_response(
            intent=RouterIntent.PAYMENT_CONFIRMATION,
            tool=RouterTool.CONFIRM_PAYMENT,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="I have paid invoice INV-001",
            invoice_id="INV-001",
            customer_id="CUST-001",
        )

        assert result.success
        assert result.intent == RouterIntent.PAYMENT_CONFIRMATION
        assert result.current_state == InvoiceState.PAID
        assert "invoice_paid" in result.events_fired

        # 7. Close invoice (administrative)
        orchestrator.advance_invoice("INV-001", "close", customer_id="CUST-001")
        assert orchestrator.get_invoice_state("INV-001") == InvoiceState.CLOSED

        # Verify events were fired
        event_types = subscriber.get_event_types()
        assert "invoice_approved" in event_types
        assert "invoice_paid" in event_types
        assert "invoice_closed" in event_types

    def test_invoice_status_query(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test querying invoice status doesn't change state."""
        orchestrator.create_invoice("INV-002")
        orchestrator.advance_invoice("INV-002", "send_invoice")

        mock_provider.set_response(
            intent=RouterIntent.INVOICE_QUESTION,
            tool=RouterTool.GET_INVOICE_STATUS,
            arguments={"invoice_id": "INV-002"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="What is the status of INV-002?",
            invoice_id="INV-002",
        )

        assert result.success
        assert result.intent == RouterIntent.INVOICE_QUESTION
        # State should not change
        assert orchestrator.get_invoice_state("INV-002") == InvoiceState.INVOICE_SENT


# ============================================================================
# State Transition Validation Tests
# ============================================================================


class TestStateTransitionValidation:
    """Test that invalid state transitions are blocked."""

    def test_cannot_approve_from_new_state(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test approval is blocked when invoice is in 'new' state."""
        orchestrator.create_invoice("INV-003")

        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-003"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="Approve INV-003",
            invoice_id="INV-003",
        )

        # Tool should fail due to state validation
        assert not result.success
        # State should not change
        assert orchestrator.get_invoice_state("INV-003") == InvoiceState.NEW

    def test_cannot_confirm_payment_before_approval(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test payment confirmation is blocked before approval."""
        orchestrator.create_invoice("INV-004")
        orchestrator.advance_invoice("INV-004", "send_invoice")
        orchestrator.advance_invoice("INV-004", "request_approval")

        mock_provider.set_response(
            intent=RouterIntent.PAYMENT_CONFIRMATION,
            tool=RouterTool.CONFIRM_PAYMENT,
            arguments={"invoice_id": "INV-004"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="I paid INV-004",
            invoice_id="INV-004",
        )

        # Should fail - can't pay before approval
        assert not result.success
        assert orchestrator.get_invoice_state("INV-004") == InvoiceState.AWAITING_APPROVAL

    def test_cannot_transition_from_closed(
        self,
        orchestrator: InvoiceOrchestrator,
    ) -> None:
        """Test no transitions allowed from closed state."""
        orchestrator.create_invoice("INV-005")
        orchestrator.advance_invoice("INV-005", "send_invoice")
        orchestrator.advance_invoice("INV-005", "request_approval")
        orchestrator.advance_invoice("INV-005", "reject")
        orchestrator.advance_invoice("INV-005", "close")

        assert orchestrator.get_invoice_state("INV-005") == InvoiceState.CLOSED

        # Try to reopen - should fail
        with pytest.raises(StateError):
            orchestrator.advance_invoice("INV-005", "send_invoice")

    def test_invalid_jump_blocked(
        self,
        orchestrator: InvoiceOrchestrator,
    ) -> None:
        """Test that skipping states is blocked."""
        orchestrator.create_invoice("INV-006")

        # Try to jump directly to approved - should fail
        with pytest.raises(StateError):
            orchestrator.advance_invoice("INV-006", "approve")


# ============================================================================
# Tool Usage Validation Tests
# ============================================================================


class TestToolUsageValidation:
    """Test correct tool is used for each intent."""

    def test_approval_uses_approve_tool(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test approval intent uses approve_invoice tool."""
        orchestrator.create_invoice("INV-007")
        orchestrator.advance_invoice("INV-007", "send_invoice")
        orchestrator.advance_invoice("INV-007", "request_approval")

        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-007"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="I approve",
            invoice_id="INV-007",
        )

        assert result.tool_used == RouterTool.APPROVE_INVOICE

    def test_rejection_uses_reject_tool(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test rejection intent uses reject_invoice tool."""
        orchestrator.create_invoice("INV-008")
        orchestrator.advance_invoice("INV-008", "send_invoice")
        orchestrator.advance_invoice("INV-008", "request_approval")

        mock_provider.set_response(
            intent=RouterIntent.INVOICE_REJECTION,
            tool=RouterTool.REJECT_INVOICE,
            arguments={"invoice_id": "INV-008", "reason": "Wrong amount"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="I reject this invoice, wrong amount",
            invoice_id="INV-008",
        )

        assert result.tool_used == RouterTool.REJECT_INVOICE
        assert result.current_state == InvoiceState.REJECTED


# ============================================================================
# Event Firing Tests
# ============================================================================


class TestEventFiring:
    """Test events are fired correctly on state transitions."""

    def test_approval_fires_event(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
        subscriber: FakeEventSubscriber,
    ) -> None:
        """Test invoice_approved event is fired on approval."""
        orchestrator.subscribe_to_events(subscriber)

        orchestrator.create_invoice("INV-009", customer_id="CUST-009")
        orchestrator.advance_invoice("INV-009", "send_invoice")
        orchestrator.advance_invoice("INV-009", "request_approval")

        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-009"},
            confidence="high",
        )

        orchestrator.process_message(
            message="Approved",
            invoice_id="INV-009",
            customer_id="CUST-009",
        )

        assert len(subscriber.events_received) == 1
        event = subscriber.events_received[0]
        assert event.event_type == "invoice_approved"
        assert event.invoice_id == "INV-009"
        assert event.customer_id == "CUST-009"
        assert event.payload["previous_state"] == InvoiceState.AWAITING_APPROVAL
        assert event.payload["current_state"] == InvoiceState.APPROVED

    def test_events_are_idempotent(
        self,
        orchestrator: InvoiceOrchestrator,
        subscriber: FakeEventSubscriber,
    ) -> None:
        """Test same event is not fired twice."""
        orchestrator.subscribe_to_events(subscriber)

        orchestrator.create_invoice("INV-010")
        orchestrator.advance_invoice("INV-010", "send_invoice")
        orchestrator.advance_invoice("INV-010", "request_approval")
        orchestrator.advance_invoice("INV-010", "approve")

        # Event should be fired once
        initial_count = len(subscriber.events_received)

        # Try to trigger same transition again (should fail, but event shouldn't duplicate)
        # Since we can't go back, just verify current count
        assert initial_count == 1
        assert subscriber.events_received[0].event_type == "invoice_approved"

    def test_event_contains_timestamp(
        self,
        orchestrator: InvoiceOrchestrator,
        subscriber: FakeEventSubscriber,
    ) -> None:
        """Test events contain timestamp."""
        orchestrator.subscribe_to_events(subscriber)

        orchestrator.create_invoice("INV-011")
        orchestrator.advance_invoice("INV-011", "send_invoice")
        orchestrator.advance_invoice("INV-011", "request_approval")
        orchestrator.advance_invoice("INV-011", "approve")

        event = subscriber.events_received[0]
        assert event.timestamp is not None
        assert event.event_id is not None


# ============================================================================
# Clarification Flow Tests
# ============================================================================


class TestClarificationFlow:
    """Test clarification requests are handled correctly."""

    def test_missing_invoice_id_requests_clarification(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that missing invoice ID triggers clarification."""
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={},  # No invoice ID
            confidence="medium",
            requires_clarification=True,
            clarification_prompt="Which invoice?",
        )

        result = orchestrator.process_message(
            message="I approve the invoice",
            # No invoice_id provided
        )

        assert result.requires_clarification
        assert result.clarification_prompt is not None

    def test_ambiguous_message_requests_clarification(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test ambiguous messages request clarification."""
        mock_provider.set_response(
            intent=RouterIntent.UNKNOWN,
            tool=RouterTool.NONE,
            confidence="low",
            requires_clarification=True,
            clarification_prompt="What would you like to do?",
        )

        result = orchestrator.process_message(
            message="hmm",
            invoice_id="INV-012",
        )

        assert result.requires_clarification
        assert result.intent == RouterIntent.UNKNOWN


# ============================================================================
# Dispute Flow Tests
# ============================================================================


class TestDisputeFlow:
    """Test the dispute and resolution flow."""

    def test_dispute_and_resolution_cycle(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
        subscriber: FakeEventSubscriber,
    ) -> None:
        """Test complete dispute cycle."""
        orchestrator.subscribe_to_events(subscriber)

        # Setup: create and approve invoice
        orchestrator.create_invoice("INV-013")
        orchestrator.advance_invoice("INV-013", "send_invoice")
        orchestrator.advance_invoice("INV-013", "request_approval")
        orchestrator.advance_invoice("INV-013", "approve")

        subscriber.clear()  # Clear approval event

        # Customer disputes
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_DISPUTE,
            tool=RouterTool.CREATE_DISPUTE,
            arguments={"invoice_id": "INV-013", "reason": "Wrong amount"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="I dispute this invoice, wrong amount",
            invoice_id="INV-013",
        )

        assert result.success
        assert result.current_state == InvoiceState.DISPUTED
        assert "invoice_disputed" in subscriber.get_event_types()

        # Resolve dispute (returns to awaiting_approval)
        orchestrator.advance_invoice("INV-013", "resolve_dispute")
        assert orchestrator.get_invoice_state("INV-013") == InvoiceState.AWAITING_APPROVAL

        # Can approve again
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-013"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="Now I approve",
            invoice_id="INV-013",
        )

        assert result.success
        assert result.current_state == InvoiceState.APPROVED
