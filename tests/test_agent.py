"""Tests for the InvoiceAgent."""

import pytest

from agents.invoice_agent import InvoiceAgent
from state_machine.invoice_state import InvoiceState
from state_machine.models import Intent
from tools.base import InMemoryInvoiceStore


@pytest.fixture
def store() -> InMemoryInvoiceStore:
    """Create a fresh store for each test."""
    return InMemoryInvoiceStore()


@pytest.fixture
def agent(store: InMemoryInvoiceStore) -> InvoiceAgent:
    """Create an agent with a fresh store."""
    return InvoiceAgent(store=store)


class TestIntentClassification:
    """Test intent classification within the agent."""

    def test_approval_intent(self, agent: InvoiceAgent) -> None:
        """Test approval intent is detected."""
        response = agent.process_message("I want to approve invoice INV-001")
        assert response.intent == Intent.INVOICE_APPROVAL
        assert response.invoice_id == "INV-001"

    def test_rejection_intent(self, agent: InvoiceAgent) -> None:
        """Test rejection intent is detected."""
        response = agent.process_message("Please reject INV-002")
        assert response.intent == Intent.INVOICE_REJECTION

    def test_payment_intent(self, agent: InvoiceAgent) -> None:
        """Test payment confirmation intent is detected."""
        response = agent.process_message("I've paid for invoice #003")
        assert response.intent == Intent.PAYMENT_CONFIRMATION

    def test_dispute_intent(self, agent: InvoiceAgent) -> None:
        """Test dispute intent is detected."""
        response = agent.process_message("I want to dispute the amount on INV-004")
        assert response.intent == Intent.INVOICE_DISPUTE

    def test_resend_intent(self, agent: InvoiceAgent) -> None:
        """Test resend request intent is detected."""
        response = agent.process_message("Can you resend invoice 005?")
        assert response.intent == Intent.REQUEST_INVOICE_COPY

    def test_question_intent(self, agent: InvoiceAgent) -> None:
        """Test question intent is detected."""
        response = agent.process_message("What is the status of INV-006?")
        assert response.intent == Intent.INVOICE_QUESTION


class TestApprovalFlow:
    """Test the approval flow through the agent."""

    def test_approve_requires_invoice_id(self, agent: InvoiceAgent) -> None:
        """Test approval requests without invoice ID."""
        response = agent.process_message("I want to approve the invoice")
        assert "which invoice" in response.message.lower()

    def test_approve_nonexistent_invoice(self, agent: InvoiceAgent) -> None:
        """Test approving nonexistent invoice."""
        response = agent.process_message("Approve INV-999")
        assert "not found" in response.message.lower()

    def test_approve_wrong_state(self, agent: InvoiceAgent) -> None:
        """Test approving invoice in wrong state."""
        agent.create_invoice("INV-001")  # Creates in 'new' state

        response = agent.process_message("Approve INV-001")
        assert "cannot be approved" in response.message.lower()
        assert response.current_state == "new"

    def test_successful_approval(self, agent: InvoiceAgent) -> None:
        """Test successful approval."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        agent.store.save_fsm(fsm)

        response = agent.process_message("I approve invoice INV-001")

        assert response.action_taken == "approve"
        assert response.current_state == "approved"
        assert "approved" in response.message.lower()


class TestRejectionFlow:
    """Test the rejection flow through the agent."""

    def test_reject_requires_reason(self, agent: InvoiceAgent) -> None:
        """Test rejection requires a reason."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        agent.store.save_fsm(fsm)

        response = agent.process_message("Reject INV-001")
        assert "reason" in response.message.lower()

    def test_successful_rejection(self, agent: InvoiceAgent) -> None:
        """Test successful rejection with reason."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        agent.store.save_fsm(fsm)

        response = agent.process_message(
            "Reject INV-001",
            reason="Amount is incorrect",
        )

        assert response.action_taken == "reject"
        assert response.current_state == "rejected"


class TestPaymentFlow:
    """Test the payment confirmation flow."""

    def test_payment_requires_approval_first(self, agent: InvoiceAgent) -> None:
        """Test payment requires prior approval."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        agent.store.save_fsm(fsm)

        response = agent.process_message("I've paid INV-001")
        assert "approved first" in response.message.lower()

    def test_successful_payment_confirmation(self, agent: InvoiceAgent) -> None:
        """Test successful payment confirmation."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        fsm.trigger("request_payment")
        agent.store.save_fsm(fsm)

        response = agent.process_message("I've made the payment for INV-001")

        assert response.action_taken == "confirm_payment"
        assert response.current_state == "paid"


class TestDisputeFlow:
    """Test the dispute flow through the agent."""

    def test_dispute_requires_reason(self, agent: InvoiceAgent) -> None:
        """Test dispute requires a reason."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        agent.store.save_fsm(fsm)

        response = agent.process_message("I want to dispute INV-001")
        assert "describe" in response.message.lower() or "issue" in response.message.lower()

    def test_successful_dispute(self, agent: InvoiceAgent) -> None:
        """Test successful dispute creation."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        agent.store.save_fsm(fsm)

        response = agent.process_message(
            "I dispute INV-001",
            reason="Wrong amount",
        )

        assert response.action_taken == "dispute"
        assert response.current_state == "disputed"


class TestInvoiceQuestionFlow:
    """Test the question/status flow."""

    def test_question_requires_invoice_id(self, agent: InvoiceAgent) -> None:
        """Test questions without invoice ID."""
        response = agent.process_message("What is the status?")
        assert "invoice number" in response.message.lower()

    def test_status_query(self, agent: InvoiceAgent) -> None:
        """Test status query for existing invoice."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        agent.store.save_fsm(fsm)

        response = agent.process_message("What is the status of INV-001?")

        assert response.intent == Intent.INVOICE_QUESTION
        assert response.current_state == "invoice_sent"


class TestResendFlow:
    """Test the resend/copy request flow."""

    def test_resend_requires_invoice_id(self, agent: InvoiceAgent) -> None:
        """Test resend without invoice ID."""
        response = agent.process_message("Can you resend the invoice?")
        assert "which invoice" in response.message.lower()

    def test_successful_resend(self, agent: InvoiceAgent) -> None:
        """Test successful resend."""
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        agent.store.save_fsm(fsm)

        response = agent.process_message("Please resend INV-001")

        assert response.action_taken == "resend"
        assert "resent" in response.message.lower()


class TestEndToEndFlows:
    """Test complete end-to-end flows."""

    def test_full_happy_path(self, agent: InvoiceAgent) -> None:
        """Test complete happy path from creation to close."""
        # Create invoice
        fsm = agent.create_invoice("INV-001")

        # Send invoice
        fsm.trigger("send_invoice")
        agent.store.save_fsm(fsm)

        # Request approval
        fsm.trigger("request_approval")
        agent.store.save_fsm(fsm)

        # Approve via agent
        response = agent.process_message("I approve invoice INV-001")
        assert response.current_state == "approved"

        # Request payment
        agent.advance_state("INV-001", "request_payment")

        # Confirm payment via agent
        response = agent.process_message("Payment sent for INV-001")
        assert response.current_state == "paid"

        # Close
        agent.advance_state("INV-001", "close")
        state = agent.get_invoice_state("INV-001")
        assert state["current_state"] == "closed"

    def test_dispute_and_resolution_flow(self, agent: InvoiceAgent) -> None:
        """Test dispute creation and resolution."""
        # Setup: create and approve invoice
        fsm = agent.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        agent.store.save_fsm(fsm)

        # Dispute via agent
        response = agent.process_message(
            "I want to dispute INV-001",
            reason="Wrong amount",
        )
        assert response.current_state == "disputed"

        # Resolve dispute
        agent.advance_state("INV-001", "resolve_dispute")

        # Can approve again
        response = agent.process_message("I approve invoice INV-001")
        assert response.current_state == "approved"
