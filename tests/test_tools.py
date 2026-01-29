"""Tests for the invoice tools."""

import pytest

from state_machine.invoice_state import InvoiceState
from tools.base import InMemoryInvoiceStore
from tools.invoice_tools import (
    ApproveInvoiceTool,
    CloseInvoiceTool,
    ConfirmPaymentTool,
    CreateDisputeTool,
    GetInvoiceStatusTool,
    RejectInvoiceTool,
    ResendInvoiceTool,
    ResolveDisputeTool,
)


@pytest.fixture
def store() -> InMemoryInvoiceStore:
    """Create a fresh store for each test."""
    return InMemoryInvoiceStore()


class TestGetInvoiceStatusTool:
    """Tests for GetInvoiceStatusTool."""

    def test_get_status_existing_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test getting status of an existing invoice."""
        store.create_invoice("INV-001")
        tool = GetInvoiceStatusTool(store)

        result = tool.run("INV-001")

        assert result["success"] is True
        assert result["data"]["current_state"] == "new"
        assert "send_invoice" in result["data"]["available_actions"]

    def test_get_status_nonexistent_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test getting status of a nonexistent invoice."""
        tool = GetInvoiceStatusTool(store)

        result = tool.run("INV-999")

        assert result["success"] is False
        assert result["error"]["code"] == "INVOICE_NOT_FOUND"


class TestApproveInvoiceTool:
    """Tests for ApproveInvoiceTool."""

    def test_approve_awaiting_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test approving an invoice awaiting approval."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        store.save_fsm(fsm)

        tool = ApproveInvoiceTool(store)
        result = tool.run("INV-001", approver_id="user-123", reason="Looks good")

        assert result["success"] is True
        assert result["data"]["current_state"] == "approved"
        assert result["data"]["approver_id"] == "user-123"

    def test_cannot_approve_new_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot approve invoice in 'new' state."""
        store.create_invoice("INV-001")
        tool = ApproveInvoiceTool(store)

        result = tool.run("INV-001")

        assert result["success"] is False
        assert result["error"]["code"] == "INVALID_STATE"
        assert result["error"]["current_state"] == "new"

    def test_approve_nonexistent_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test approving nonexistent invoice."""
        tool = ApproveInvoiceTool(store)

        result = tool.run("INV-999")

        assert result["success"] is False
        assert result["error"]["code"] == "INVOICE_NOT_FOUND"


class TestRejectInvoiceTool:
    """Tests for RejectInvoiceTool."""

    def test_reject_with_reason(self, store: InMemoryInvoiceStore) -> None:
        """Test rejecting with a reason."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        store.save_fsm(fsm)

        tool = RejectInvoiceTool(store)
        result = tool.run("INV-001", reason="Incorrect amount")

        assert result["success"] is True
        assert result["data"]["current_state"] == "rejected"
        assert result["data"]["reason"] == "Incorrect amount"

    def test_reject_without_reason(self, store: InMemoryInvoiceStore) -> None:
        """Test rejection requires a reason."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        store.save_fsm(fsm)

        tool = RejectInvoiceTool(store)
        result = tool.run("INV-001")  # No reason

        assert result["success"] is False
        assert result["error"]["code"] == "MISSING_REASON"


class TestConfirmPaymentTool:
    """Tests for ConfirmPaymentTool."""

    def test_confirm_payment_pending(self, store: InMemoryInvoiceStore) -> None:
        """Test confirming payment when pending."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        fsm.trigger("request_payment")
        store.save_fsm(fsm)

        tool = ConfirmPaymentTool(store)
        result = tool.run("INV-001", payment_reference="PAY-123")

        assert result["success"] is True
        assert result["data"]["current_state"] == "paid"

    def test_cannot_confirm_without_approval(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot confirm payment without approval."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        store.save_fsm(fsm)

        tool = ConfirmPaymentTool(store)
        result = tool.run("INV-001")

        assert result["success"] is False
        assert "approved first" in result["message"]

    def test_cannot_double_confirm(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot confirm payment twice."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        fsm.trigger("request_payment")
        fsm.trigger("confirm_payment")
        store.save_fsm(fsm)

        tool = ConfirmPaymentTool(store)
        result = tool.run("INV-001")

        assert result["success"] is False
        assert "already been confirmed" in result["message"]


class TestCreateDisputeTool:
    """Tests for CreateDisputeTool."""

    def test_create_dispute_approved(self, store: InMemoryInvoiceStore) -> None:
        """Test creating dispute for approved invoice."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        store.save_fsm(fsm)

        tool = CreateDisputeTool(store)
        result = tool.run("INV-001", reason="Wrong amount charged")

        assert result["success"] is True
        assert result["data"]["current_state"] == "disputed"

    def test_dispute_without_reason(self, store: InMemoryInvoiceStore) -> None:
        """Test dispute requires a reason."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        store.save_fsm(fsm)

        tool = CreateDisputeTool(store)
        result = tool.run("INV-001")

        assert result["success"] is False
        assert result["error"]["code"] == "MISSING_REASON"

    def test_cannot_dispute_new_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot dispute new invoice."""
        store.create_invoice("INV-001")
        tool = CreateDisputeTool(store)

        result = tool.run("INV-001", reason="Test")

        assert result["success"] is False


class TestResolveDisputeTool:
    """Tests for ResolveDisputeTool."""

    def test_resolve_dispute(self, store: InMemoryInvoiceStore) -> None:
        """Test resolving a dispute."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        fsm.trigger("dispute")
        store.save_fsm(fsm)

        tool = ResolveDisputeTool(store)
        result = tool.run("INV-001", resolution="Adjusted amount")

        assert result["success"] is True
        assert result["data"]["current_state"] == "awaiting_approval"

    def test_resolve_without_resolution(self, store: InMemoryInvoiceStore) -> None:
        """Test resolution requires details."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        fsm.trigger("dispute")
        store.save_fsm(fsm)

        tool = ResolveDisputeTool(store)
        result = tool.run("INV-001")

        assert result["success"] is False
        assert result["error"]["code"] == "MISSING_RESOLUTION"


class TestCloseInvoiceTool:
    """Tests for CloseInvoiceTool."""

    def test_close_paid_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test closing a paid invoice."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("approve")
        fsm.trigger("request_payment")
        fsm.trigger("confirm_payment")
        store.save_fsm(fsm)

        tool = CloseInvoiceTool(store)
        result = tool.run("INV-001")

        assert result["success"] is True
        assert result["data"]["current_state"] == "closed"

    def test_close_rejected_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test closing a rejected invoice."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("reject")
        store.save_fsm(fsm)

        tool = CloseInvoiceTool(store)
        result = tool.run("INV-001")

        assert result["success"] is True
        assert result["data"]["current_state"] == "closed"

    def test_cannot_close_new_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot close new invoice."""
        store.create_invoice("INV-001")
        tool = CloseInvoiceTool(store)

        result = tool.run("INV-001")

        assert result["success"] is False


class TestResendInvoiceTool:
    """Tests for ResendInvoiceTool."""

    def test_resend_sent_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test resending a sent invoice."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        store.save_fsm(fsm)

        tool = ResendInvoiceTool(store)
        result = tool.run("INV-001")

        assert result["success"] is True
        assert result["data"]["action"] == "resend"

    def test_cannot_resend_new_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot resend invoice that hasn't been sent."""
        store.create_invoice("INV-001")
        tool = ResendInvoiceTool(store)

        result = tool.run("INV-001")

        assert result["success"] is False

    def test_cannot_resend_closed_invoice(self, store: InMemoryInvoiceStore) -> None:
        """Test cannot resend closed invoice."""
        fsm = store.create_invoice("INV-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("reject")
        fsm.trigger("close")
        store.save_fsm(fsm)

        tool = ResendInvoiceTool(store)
        result = tool.run("INV-001")

        assert result["success"] is False
