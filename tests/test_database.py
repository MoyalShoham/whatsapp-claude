"""Tests for database storage."""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from database import init_db, reset_engine, DatabaseInvoiceStore
from database.session import get_engine
from state_machine.invoice_state import InvoiceState


@pytest.fixture
def db_store():
    """Create a fresh database store for each test."""
    # Reset any existing engine
    reset_engine()

    # Initialize in-memory SQLite database
    init_db("sqlite:///:memory:")

    store = DatabaseInvoiceStore()
    yield store

    # Cleanup
    reset_engine()


class TestDatabaseInvoiceStore:
    """Test DatabaseInvoiceStore operations."""

    def test_create_invoice(self, db_store):
        """Test creating a new invoice."""
        fsm = db_store.create_invoice(
            invoice_id="INV-001",
            amount=Decimal("100.00"),
            currency="USD",
            description="Test invoice",
        )

        assert fsm.invoice_id == "INV-001"
        assert fsm.current_state == InvoiceState.NEW

    def test_create_duplicate_invoice_fails(self, db_store):
        """Test that duplicate invoice creation fails."""
        db_store.create_invoice(invoice_id="INV-002")

        with pytest.raises(ValueError, match="already exists"):
            db_store.create_invoice(invoice_id="INV-002")

    def test_get_fsm(self, db_store):
        """Test retrieving FSM from database."""
        db_store.create_invoice(invoice_id="INV-003")

        fsm = db_store.get_fsm("INV-003")

        assert fsm is not None
        assert fsm.invoice_id == "INV-003"
        assert fsm.current_state == InvoiceState.NEW

    def test_get_nonexistent_fsm(self, db_store):
        """Test retrieving non-existent invoice returns None."""
        fsm = db_store.get_fsm("INV-NONEXISTENT")

        assert fsm is None

    def test_save_fsm_updates_state(self, db_store):
        """Test that saving FSM persists state changes."""
        fsm = db_store.create_invoice(invoice_id="INV-004")

        # Transition to new state
        fsm.trigger("send_invoice")
        db_store.save_fsm(fsm)

        # Retrieve and verify
        loaded_fsm = db_store.get_fsm("INV-004")
        assert loaded_fsm.current_state == InvoiceState.INVOICE_SENT

    def test_list_invoices(self, db_store):
        """Test listing invoices."""
        db_store.create_invoice(invoice_id="INV-005")
        db_store.create_invoice(invoice_id="INV-006")

        invoices = db_store.list_invoices()

        assert len(invoices) >= 2
        invoice_ids = [inv["invoice_id"] for inv in invoices]
        assert "INV-005" in invoice_ids
        assert "INV-006" in invoice_ids

    def test_list_invoices_filter_by_state(self, db_store):
        """Test listing invoices filtered by state."""
        fsm1 = db_store.create_invoice(invoice_id="INV-007")
        db_store.create_invoice(invoice_id="INV-008")

        # Transition one invoice
        fsm1.trigger("send_invoice")
        db_store.save_fsm(fsm1)

        # Filter by state
        new_invoices = db_store.list_invoices(state=InvoiceState.NEW)
        sent_invoices = db_store.list_invoices(state=InvoiceState.INVOICE_SENT)

        new_ids = [inv["invoice_id"] for inv in new_invoices]
        sent_ids = [inv["invoice_id"] for inv in sent_invoices]

        assert "INV-008" in new_ids
        assert "INV-007" in sent_ids

    def test_get_invoice_details(self, db_store):
        """Test getting invoice details."""
        db_store.create_invoice(
            invoice_id="INV-009",
            amount=Decimal("250.00"),
            currency="EUR",
            description="Test invoice with details",
        )

        invoice = db_store.get_invoice("INV-009")

        assert invoice is not None
        assert invoice["invoice_id"] == "INV-009"
        assert invoice["amount"] == "250.00"
        assert invoice["currency"] == "EUR"
        assert invoice["description"] == "Test invoice with details"
        assert invoice["state"] == InvoiceState.NEW

    def test_update_invoice(self, db_store):
        """Test updating invoice details."""
        db_store.create_invoice(
            invoice_id="INV-010",
            amount=Decimal("100.00"),
        )

        result = db_store.update_invoice(
            invoice_id="INV-010",
            amount=Decimal("150.00"),
            description="Updated description",
        )

        assert result is True

        invoice = db_store.get_invoice("INV-010")
        assert invoice["amount"] == "150.00"
        assert invoice["description"] == "Updated description"

    def test_update_nonexistent_invoice(self, db_store):
        """Test updating non-existent invoice returns False."""
        result = db_store.update_invoice(
            invoice_id="INV-NONEXISTENT",
            amount=Decimal("100.00"),
        )

        assert result is False


class TestCustomerOperations:
    """Test customer-related operations."""

    def test_get_or_create_customer_creates(self, db_store):
        """Test creating a new customer."""
        customer = db_store.get_or_create_customer(
            phone="+1234567890",
            name="John Doe",
            email="john@example.com",
        )

        assert customer["phone"] == "+1234567890"
        assert customer["name"] == "John Doe"
        assert customer["email"] == "john@example.com"
        assert "id" in customer

    def test_get_or_create_customer_gets_existing(self, db_store):
        """Test getting an existing customer."""
        # Create first
        customer1 = db_store.get_or_create_customer(
            phone="+1234567891",
            name="Jane Doe",
        )

        # Get existing
        customer2 = db_store.get_or_create_customer(
            phone="+1234567891",
        )

        assert customer1["id"] == customer2["id"]
        assert customer2["name"] == "Jane Doe"

    def test_get_customer_invoices(self, db_store):
        """Test getting invoices for a customer."""
        customer = db_store.get_or_create_customer(phone="+1234567892")
        customer_id = customer["id"]

        db_store.create_invoice(
            invoice_id="INV-011",
            customer_id=customer_id,
        )
        db_store.create_invoice(
            invoice_id="INV-012",
            customer_id=customer_id,
        )

        invoices = db_store.get_customer_invoices(customer_id)

        assert len(invoices) == 2
        invoice_ids = [inv["invoice_id"] for inv in invoices]
        assert "INV-011" in invoice_ids
        assert "INV-012" in invoice_ids


class TestConversationOperations:
    """Test conversation-related operations."""

    def test_save_conversation(self, db_store):
        """Test saving a conversation message."""
        customer = db_store.get_or_create_customer(phone="+1234567893")

        db_store.save_conversation(
            customer_id=customer["id"],
            role="user",
            content="What is the status of my invoice?",
            intent="check_status",
        )

        history = db_store.get_conversation_history(customer["id"])

        assert len(history) == 1
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "What is the status of my invoice?"
        assert history[0]["intent"] == "check_status"

    def test_get_conversation_history_order(self, db_store):
        """Test conversation history is in chronological order."""
        customer = db_store.get_or_create_customer(phone="+1234567894")

        db_store.save_conversation(
            customer_id=customer["id"],
            role="user",
            content="First message",
        )
        db_store.save_conversation(
            customer_id=customer["id"],
            role="assistant",
            content="First response",
        )
        db_store.save_conversation(
            customer_id=customer["id"],
            role="user",
            content="Second message",
        )

        history = db_store.get_conversation_history(customer["id"])

        assert len(history) == 3
        assert history[0]["content"] == "First message"
        assert history[1]["content"] == "First response"
        assert history[2]["content"] == "Second message"


class TestAuditOperations:
    """Test audit log operations."""

    def test_log_audit(self, db_store):
        """Test logging an audit entry."""
        db_store.log_audit(
            action="invoice_created",
            invoice_id="INV-AUDIT-001",
            details={"source": "api"},
        )

        logs = db_store.get_audit_log(invoice_id="INV-AUDIT-001")

        assert len(logs) == 1
        assert logs[0]["action"] == "invoice_created"
        assert logs[0]["invoice_id"] == "INV-AUDIT-001"
        assert logs[0]["details"]["source"] == "api"

    def test_get_audit_log_filter_by_action(self, db_store):
        """Test filtering audit log by action."""
        db_store.log_audit(action="invoice_created", invoice_id="INV-A1")
        db_store.log_audit(action="invoice_updated", invoice_id="INV-A2")
        db_store.log_audit(action="invoice_created", invoice_id="INV-A3")

        logs = db_store.get_audit_log(action="invoice_created")

        assert len(logs) == 2
        actions = [log["action"] for log in logs]
        assert all(a == "invoice_created" for a in actions)


class TestStatistics:
    """Test statistics operations."""

    def test_get_stats(self, db_store):
        """Test getting invoice statistics."""
        db_store.create_invoice(invoice_id="INV-STAT-001")
        db_store.create_invoice(invoice_id="INV-STAT-002")

        fsm = db_store.create_invoice(invoice_id="INV-STAT-003")
        fsm.trigger("send_invoice")
        db_store.save_fsm(fsm)

        stats = db_store.get_stats()

        assert stats["total_invoices"] >= 3
        assert stats["open_invoices"] >= 3
        assert "by_state" in stats
        assert stats["by_state"].get(InvoiceState.NEW, 0) >= 2
        assert stats["by_state"].get(InvoiceState.INVOICE_SENT, 0) >= 1


class TestHistoryPersistence:
    """Test that transition history is persisted correctly."""

    def test_history_persisted_through_transitions(self, db_store):
        """Test that full transition history is saved."""
        fsm = db_store.create_invoice(invoice_id="INV-HIST-001")

        # Perform multiple transitions
        fsm.trigger("send_invoice")
        db_store.save_fsm(fsm)

        fsm.trigger("request_approval")
        db_store.save_fsm(fsm)

        fsm.trigger("approve")
        db_store.save_fsm(fsm)

        # Reload and check history
        loaded_fsm = db_store.get_fsm("INV-HIST-001")

        assert loaded_fsm.current_state == InvoiceState.APPROVED
        assert len(loaded_fsm.history) >= 3

        # Verify history contains correct transitions
        triggers = [h["trigger"] for h in loaded_fsm.history]
        assert "send_invoice" in triggers
        assert "request_approval" in triggers
        assert "approve" in triggers
