"""Tests for invoice data models."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.invoice_data import (
    InvoiceAddress,
    InvoiceData,
    InvoicePDFGenerator,
    LineItem,
    PaymentTerms,
    create_sample_invoice,
)


class TestLineItem:
    """Test LineItem model."""

    def test_create_line_item(self):
        """Test creating a line item."""
        item = LineItem(
            description="Web Development",
            quantity=Decimal("10"),
            unit_price=Decimal("100.00"),
        )

        assert item.description == "Web Development"
        assert item.quantity == Decimal("10")
        assert item.unit_price == Decimal("100.00")

    def test_subtotal_calculation(self):
        """Test subtotal is calculated correctly."""
        item = LineItem(
            description="Service",
            quantity=Decimal("5"),
            unit_price=Decimal("50.00"),
        )

        assert item.subtotal == Decimal("250.00")

    def test_tax_calculation(self):
        """Test tax is calculated correctly."""
        item = LineItem(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            tax_rate=Decimal("10"),
        )

        assert item.tax_amount == Decimal("10.00")
        assert item.total == Decimal("110.00")

    def test_to_dict(self):
        """Test converting to dictionary."""
        item = LineItem(
            description="Service",
            quantity=Decimal("2"),
            unit_price=Decimal("75.00"),
            tax_rate=Decimal("8.5"),
        )

        data = item.to_dict()

        assert data["description"] == "Service"
        assert data["quantity"] == "2"
        assert data["unit_price"] == "75.00"
        assert "subtotal" in data
        assert "total" in data


class TestInvoiceAddress:
    """Test InvoiceAddress model."""

    def test_create_address(self):
        """Test creating an address."""
        address = InvoiceAddress(
            name="John Doe",
            company="Acme Inc",
            street="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="USA",
        )

        assert address.name == "John Doe"
        assert address.company == "Acme Inc"

    def test_format_multiline(self):
        """Test formatting address as multiline string."""
        address = InvoiceAddress(
            name="John Doe",
            company="Acme Inc",
            street="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="USA",
        )

        formatted = address.format_multiline()

        assert "John Doe" in formatted
        assert "Acme Inc" in formatted
        assert "New York, NY 10001" in formatted


class TestPaymentTerms:
    """Test PaymentTerms model."""

    def test_default_terms(self):
        """Test default payment terms."""
        terms = PaymentTerms()

        assert terms.due_days == 30
        assert terms.description == "Net 30"

    def test_custom_terms(self):
        """Test custom payment terms."""
        terms = PaymentTerms(
            due_days=60,
            late_fee_percent=Decimal("1.5"),
        )

        assert terms.due_days == 60
        assert terms.description == "Net 60"

    def test_early_discount_terms(self):
        """Test terms with early payment discount."""
        terms = PaymentTerms(
            due_days=30,
            early_discount_percent=Decimal("2"),
            early_discount_days=10,
        )

        assert "2% 10, Net 30" in terms.description


class TestInvoiceData:
    """Test InvoiceData model."""

    def test_create_invoice(self):
        """Test creating an invoice."""
        invoice = InvoiceData(
            invoice_id="INV-001",
            invoice_number="2024-001",
        )

        assert invoice.invoice_id == "INV-001"
        assert invoice.currency == "USD"
        assert invoice.amount_paid == Decimal("0")

    def test_add_line_item(self):
        """Test adding a line item."""
        invoice = InvoiceData(invoice_id="INV-001")

        item = invoice.add_line_item(
            description="Service",
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
        )

        assert len(invoice.line_items) == 1
        assert invoice.subtotal == Decimal("200.00")
        assert invoice.total == Decimal("200.00")

    def test_multiple_line_items(self):
        """Test adding multiple line items."""
        invoice = InvoiceData(invoice_id="INV-001")

        invoice.add_line_item(
            description="Service A",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )
        invoice.add_line_item(
            description="Service B",
            quantity=Decimal("2"),
            unit_price=Decimal("50.00"),
        )

        assert len(invoice.line_items) == 2
        assert invoice.subtotal == Decimal("200.00")

    def test_remove_line_item(self):
        """Test removing a line item."""
        invoice = InvoiceData(invoice_id="INV-001")

        item = invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        result = invoice.remove_line_item(item.id)

        assert result is True
        assert len(invoice.line_items) == 0
        assert invoice.subtotal == Decimal("0")

    def test_balance_due(self):
        """Test balance due calculation."""
        invoice = InvoiceData(invoice_id="INV-001")
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        assert invoice.balance_due == Decimal("100.00")

        invoice.amount_paid = Decimal("50.00")
        assert invoice.balance_due == Decimal("50.00")

    def test_is_paid(self):
        """Test is_paid property."""
        invoice = InvoiceData(invoice_id="INV-001")
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        assert invoice.is_paid is False

        invoice.amount_paid = Decimal("100.00")
        assert invoice.is_paid is True

    def test_is_overdue(self):
        """Test is_overdue property."""
        past_date = datetime.utcnow() - timedelta(days=5)
        invoice = InvoiceData(
            invoice_id="INV-001",
            due_date=past_date,
        )
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        assert invoice.is_overdue is True

    def test_not_overdue_when_paid(self):
        """Test that paid invoices are not overdue."""
        past_date = datetime.utcnow() - timedelta(days=5)
        invoice = InvoiceData(
            invoice_id="INV-001",
            due_date=past_date,
        )
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )
        invoice.amount_paid = Decimal("100.00")

        assert invoice.is_overdue is False

    def test_days_overdue(self):
        """Test days_overdue calculation."""
        past_date = datetime.utcnow() - timedelta(days=10)
        invoice = InvoiceData(
            invoice_id="INV-001",
            due_date=past_date,
        )
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        assert invoice.days_overdue == 10

    def test_discount(self):
        """Test discount applied to total."""
        invoice = InvoiceData(
            invoice_id="INV-001",
            discount=Decimal("20.00"),
        )
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        assert invoice.total == Decimal("80.00")

    def test_tax_calculation(self):
        """Test tax is included in total."""
        invoice = InvoiceData(invoice_id="INV-001")
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            tax_rate=Decimal("10"),
        )

        assert invoice.subtotal == Decimal("100.00")
        assert invoice.tax_total == Decimal("10.00")
        assert invoice.total == Decimal("110.00")

    def test_to_dict(self):
        """Test converting invoice to dictionary."""
        invoice = InvoiceData(
            invoice_id="INV-001",
            invoice_number="2024-001",
        )
        invoice.add_line_item(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
        )

        data = invoice.to_dict()

        assert data["invoice_id"] == "INV-001"
        assert data["invoice_number"] == "2024-001"
        assert len(data["line_items"]) == 1
        assert data["total"] == "100.00"


class TestInvoicePDFGenerator:
    """Test InvoicePDFGenerator."""

    def test_generate_simple_pdf(self):
        """Test generating a simple PDF (text fallback)."""
        invoice = create_sample_invoice()
        generator = InvoicePDFGenerator()

        pdf_bytes = generator._generate_simple(invoice)

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
        assert b"INVOICE" in pdf_bytes

    def test_generate_includes_line_items(self):
        """Test that generated PDF includes line items."""
        invoice = create_sample_invoice()
        generator = InvoicePDFGenerator()

        pdf_bytes = generator._generate_simple(invoice)

        assert b"Web Development" in pdf_bytes
        assert b"Hosting" in pdf_bytes


class TestCreateSampleInvoice:
    """Test the sample invoice helper."""

    def test_create_sample_invoice(self):
        """Test creating a sample invoice."""
        invoice = create_sample_invoice()

        assert invoice.invoice_id == "INV-2024-001"
        assert len(invoice.line_items) == 3
        assert invoice.bill_from is not None
        assert invoice.bill_to is not None
        assert invoice.total is not None
        assert invoice.total > Decimal("0")
