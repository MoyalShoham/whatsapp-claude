"""Tests for the invoice state machine."""

import pytest

from state_machine.invoice_state import InvoiceFSM, InvoiceState, TransitionError


class TestInvoiceFSM:
    """Test suite for InvoiceFSM."""

    def test_initial_state(self) -> None:
        """Test that FSM starts in 'new' state."""
        fsm = InvoiceFSM(invoice_id="INV-001")
        assert fsm.current_state == InvoiceState.NEW
        assert not fsm.is_terminal

    def test_custom_initial_state(self) -> None:
        """Test FSM can be initialized with custom state."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.AWAITING_APPROVAL,
        )
        assert fsm.current_state == InvoiceState.AWAITING_APPROVAL

    def test_invalid_initial_state(self) -> None:
        """Test that invalid initial state raises error."""
        with pytest.raises(ValueError, match="Invalid initial state"):
            InvoiceFSM(invoice_id="INV-001", initial_state="invalid")

    def test_happy_path_to_paid(self) -> None:
        """Test the normal flow from new to paid."""
        fsm = InvoiceFSM(invoice_id="INV-001")

        # new -> invoice_sent
        fsm.trigger("send_invoice")
        assert fsm.current_state == InvoiceState.INVOICE_SENT

        # invoice_sent -> awaiting_approval
        fsm.trigger("request_approval")
        assert fsm.current_state == InvoiceState.AWAITING_APPROVAL

        # awaiting_approval -> approved
        fsm.trigger("approve")
        assert fsm.current_state == InvoiceState.APPROVED

        # approved -> payment_pending
        fsm.trigger("request_payment")
        assert fsm.current_state == InvoiceState.PAYMENT_PENDING

        # payment_pending -> paid
        fsm.trigger("confirm_payment")
        assert fsm.current_state == InvoiceState.PAID

        # paid -> closed
        fsm.trigger("close")
        assert fsm.current_state == InvoiceState.CLOSED
        assert fsm.is_terminal

    def test_rejection_flow(self) -> None:
        """Test the rejection flow."""
        fsm = InvoiceFSM(invoice_id="INV-001")

        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        assert fsm.current_state == InvoiceState.AWAITING_APPROVAL

        # Reject instead of approve
        fsm.trigger("reject")
        assert fsm.current_state == InvoiceState.REJECTED

        # Can close from rejected
        fsm.trigger("close")
        assert fsm.current_state == InvoiceState.CLOSED

    def test_dispute_from_approved(self) -> None:
        """Test dispute can be raised from approved state."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.APPROVED,
        )

        fsm.trigger("dispute")
        assert fsm.current_state == InvoiceState.DISPUTED

    def test_dispute_from_payment_pending(self) -> None:
        """Test dispute can be raised from payment_pending state."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.PAYMENT_PENDING,
        )

        fsm.trigger("dispute")
        assert fsm.current_state == InvoiceState.DISPUTED

    def test_dispute_from_paid(self) -> None:
        """Test dispute can be raised even after payment."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.PAID,
        )

        fsm.trigger("dispute")
        assert fsm.current_state == InvoiceState.DISPUTED

    def test_dispute_resolution_reopens_flow(self) -> None:
        """Test that resolving a dispute reopens the approval flow."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.DISPUTED,
        )

        fsm.trigger("resolve_dispute")
        assert fsm.current_state == InvoiceState.AWAITING_APPROVAL

        # Can now approve again
        fsm.trigger("approve")
        assert fsm.current_state == InvoiceState.APPROVED

    def test_full_dispute_cycle(self) -> None:
        """Test complete cycle: approve -> dispute -> resolve -> approve -> pay."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.AWAITING_APPROVAL,
        )

        # First approval
        fsm.trigger("approve")
        assert fsm.current_state == InvoiceState.APPROVED

        # Customer disputes
        fsm.trigger("dispute")
        assert fsm.current_state == InvoiceState.DISPUTED

        # Dispute resolved
        fsm.trigger("resolve_dispute")
        assert fsm.current_state == InvoiceState.AWAITING_APPROVAL

        # Second approval
        fsm.trigger("approve")
        assert fsm.current_state == InvoiceState.APPROVED

        # Payment flow
        fsm.trigger("request_payment")
        fsm.trigger("confirm_payment")
        assert fsm.current_state == InvoiceState.PAID


class TestForbiddenTransitions:
    """Test that invalid transitions are blocked."""

    def test_cannot_approve_from_new(self) -> None:
        """Cannot approve an invoice that hasn't been sent."""
        fsm = InvoiceFSM(invoice_id="INV-001")

        with pytest.raises(TransitionError) as exc_info:
            fsm.trigger("approve")

        assert "awaiting_approval" in str(exc_info.value).lower() or \
               "Cannot execute" in str(exc_info.value)

    def test_cannot_pay_before_approval(self) -> None:
        """Cannot confirm payment before approval."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.AWAITING_APPROVAL,
        )

        with pytest.raises(TransitionError):
            fsm.trigger("confirm_payment")

    def test_cannot_pay_directly_after_approval(self) -> None:
        """Payment must be requested before confirmation."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.APPROVED,
        )

        with pytest.raises(TransitionError):
            fsm.trigger("confirm_payment")

    def test_cannot_dispute_from_new(self) -> None:
        """Cannot dispute an invoice that hasn't been processed."""
        fsm = InvoiceFSM(invoice_id="INV-001")

        with pytest.raises(TransitionError):
            fsm.trigger("dispute")

    def test_cannot_transition_from_closed(self) -> None:
        """Cannot make any transition from closed state."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.CLOSED,
        )

        with pytest.raises(TransitionError, match="terminal state"):
            fsm.trigger("send_invoice")

    def test_cannot_close_from_new(self) -> None:
        """Cannot close an invoice that's just created."""
        fsm = InvoiceFSM(invoice_id="INV-001")

        with pytest.raises(TransitionError):
            fsm.trigger("close")

    def test_cannot_close_from_approved(self) -> None:
        """Cannot close without payment or rejection."""
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.APPROVED,
        )

        with pytest.raises(TransitionError):
            fsm.trigger("close")


class TestStateIntrospection:
    """Test state machine introspection methods."""

    def test_can_trigger(self) -> None:
        """Test can_trigger method."""
        fsm = InvoiceFSM(invoice_id="INV-001")

        assert fsm.can_trigger("send_invoice")
        assert not fsm.can_trigger("approve")
        assert not fsm.can_trigger("close")

    def test_get_available_triggers(self) -> None:
        """Test available triggers are correct for each state."""
        # New state
        fsm = InvoiceFSM(invoice_id="INV-001")
        assert fsm.get_available_triggers() == ["send_invoice"]

        # Awaiting approval state
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.AWAITING_APPROVAL,
        )
        triggers = fsm.get_available_triggers()
        assert "approve" in triggers
        assert "reject" in triggers

        # Approved state
        fsm = InvoiceFSM(
            invoice_id="INV-001",
            initial_state=InvoiceState.APPROVED,
        )
        triggers = fsm.get_available_triggers()
        assert "request_payment" in triggers
        assert "dispute" in triggers

    def test_history_tracking(self) -> None:
        """Test that transition history is recorded."""
        fsm = InvoiceFSM(invoice_id="INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")

        history = fsm.history
        assert len(history) == 3  # initial + 2 transitions

        # Check first entry (initialization)
        assert history[0]["trigger"] == "initialized"
        assert history[0]["dest"] == "new"

        # Check transitions
        assert history[1]["trigger"] == "send_invoice"
        assert history[1]["source"] == "new"
        assert history[1]["dest"] == "invoice_sent"

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        fsm = InvoiceFSM(invoice_id="INV-001")
        fsm.trigger("send_invoice")

        data = fsm.to_dict()
        assert data["invoice_id"] == "INV-001"
        assert data["current_state"] == "invoice_sent"
        assert data["is_terminal"] is False
        assert "request_approval" in data["available_triggers"]

    def test_from_dict(self) -> None:
        """Test restoration from dictionary."""
        original = InvoiceFSM(invoice_id="INV-001")
        original.trigger("send_invoice")
        original.trigger("request_approval")

        data = original.to_dict()
        restored = InvoiceFSM.from_dict(data)

        assert restored.invoice_id == original.invoice_id
        assert restored.current_state == original.current_state


class TestTransitionCallback:
    """Test transition callback functionality."""

    def test_callback_is_called(self) -> None:
        """Test that callback is invoked on transitions."""
        transitions_logged: list[tuple[str, str, str]] = []

        def log_transition(invoice_id: str, source: str, dest: str) -> None:
            transitions_logged.append((invoice_id, source, dest))

        fsm = InvoiceFSM(
            invoice_id="INV-001",
            on_transition=log_transition,
        )

        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")

        assert len(transitions_logged) == 2
        assert transitions_logged[0] == ("INV-001", "new", "invoice_sent")
        assert transitions_logged[1] == ("INV-001", "invoice_sent", "awaiting_approval")
