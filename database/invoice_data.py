"""Enhanced invoice data models with line items and PDF generation."""

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class LineItem(BaseModel):
    """Individual line item in an invoice."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    quantity: Decimal = Field(default=Decimal("1"))
    unit_price: Decimal
    tax_rate: Decimal = Field(default=Decimal("0"))  # Percentage

    @property
    def subtotal(self) -> Decimal:
        """Calculate line item subtotal (quantity * unit_price)."""
        return self.quantity * self.unit_price

    @property
    def tax_amount(self) -> Decimal:
        """Calculate tax amount."""
        return self.subtotal * (self.tax_rate / Decimal("100"))

    @property
    def total(self) -> Decimal:
        """Calculate total with tax."""
        return self.subtotal + self.tax_amount

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "description": self.description,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "tax_rate": str(self.tax_rate),
            "subtotal": str(self.subtotal),
            "tax_amount": str(self.tax_amount),
            "total": str(self.total),
        }


class InvoiceAddress(BaseModel):
    """Address information for invoice."""

    name: str
    company: Optional[str] = None
    street: str
    city: str
    state: Optional[str] = None
    postal_code: str
    country: str = "US"
    phone: Optional[str] = None
    email: Optional[str] = None

    def format_multiline(self) -> str:
        """Format address as multiline string."""
        lines = [self.name]
        if self.company:
            lines.append(self.company)
        lines.append(self.street)
        city_line = f"{self.city}"
        if self.state:
            city_line += f", {self.state}"
        city_line += f" {self.postal_code}"
        lines.append(city_line)
        lines.append(self.country)
        return "\n".join(lines)


class PaymentTerms(BaseModel):
    """Payment terms configuration."""

    due_days: int = 30  # Days until payment is due
    late_fee_percent: Decimal = Field(default=Decimal("0"))  # Late fee percentage
    early_discount_percent: Decimal = Field(default=Decimal("0"))  # Early payment discount
    early_discount_days: int = 0  # Days for early payment discount

    @property
    def description(self) -> str:
        """Generate terms description."""
        terms = f"Net {self.due_days}"
        if self.early_discount_percent > 0:
            terms = f"{self.early_discount_percent}% {self.early_discount_days}, {terms}"
        return terms


class InvoiceData(BaseModel):
    """Complete invoice data with all details."""

    # Identifiers
    invoice_id: str
    invoice_number: Optional[str] = None  # Human-readable number
    purchase_order: Optional[str] = None

    # Parties
    bill_from: Optional[InvoiceAddress] = None
    bill_to: Optional[InvoiceAddress] = None

    # Dates
    issue_date: datetime = Field(default_factory=datetime.utcnow)
    due_date: Optional[datetime] = None

    # Line items
    line_items: list[LineItem] = Field(default_factory=list)

    # Currency
    currency: str = "USD"
    currency_symbol: str = "$"

    # Amounts (calculated from line items if not set)
    subtotal: Optional[Decimal] = None
    tax_total: Optional[Decimal] = None
    discount: Decimal = Field(default=Decimal("0"))
    total: Optional[Decimal] = None

    # Payment
    payment_terms: PaymentTerms = Field(default_factory=PaymentTerms)
    amount_paid: Decimal = Field(default=Decimal("0"))

    # Notes
    notes: Optional[str] = None
    terms_and_conditions: Optional[str] = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Calculate totals after initialization."""
        self._calculate_totals()
        if self.due_date is None:
            self.due_date = self.issue_date + timedelta(days=self.payment_terms.due_days)

    def _calculate_totals(self) -> None:
        """Calculate subtotal, tax, and total from line items."""
        if self.line_items:
            self.subtotal = sum(item.subtotal for item in self.line_items)
            self.tax_total = sum(item.tax_amount for item in self.line_items)
            self.total = self.subtotal + self.tax_total - self.discount

    @property
    def balance_due(self) -> Decimal:
        """Calculate remaining balance."""
        return (self.total or Decimal("0")) - self.amount_paid

    @property
    def is_paid(self) -> bool:
        """Check if invoice is fully paid."""
        return self.balance_due <= Decimal("0")

    @property
    def is_overdue(self) -> bool:
        """Check if invoice is past due date."""
        if self.due_date is None:
            return False
        return datetime.utcnow() > self.due_date and not self.is_paid

    @property
    def days_overdue(self) -> int:
        """Calculate days overdue."""
        if not self.is_overdue or self.due_date is None:
            return 0
        return (datetime.utcnow() - self.due_date).days

    def add_line_item(
        self,
        description: str,
        quantity: Decimal = Decimal("1"),
        unit_price: Decimal = Decimal("0"),
        tax_rate: Decimal = Decimal("0"),
    ) -> LineItem:
        """Add a line item and recalculate totals."""
        item = LineItem(
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            tax_rate=tax_rate,
        )
        self.line_items.append(item)
        self._calculate_totals()
        return item

    def remove_line_item(self, item_id: str) -> bool:
        """Remove a line item by ID."""
        original_count = len(self.line_items)
        self.line_items = [item for item in self.line_items if item.id != item_id]
        if len(self.line_items) < original_count:
            self._calculate_totals()
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage/API."""
        return {
            "invoice_id": self.invoice_id,
            "invoice_number": self.invoice_number,
            "purchase_order": self.purchase_order,
            "bill_from": self.bill_from.model_dump() if self.bill_from else None,
            "bill_to": self.bill_to.model_dump() if self.bill_to else None,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "line_items": [item.to_dict() for item in self.line_items],
            "currency": self.currency,
            "subtotal": str(self.subtotal) if self.subtotal else None,
            "tax_total": str(self.tax_total) if self.tax_total else None,
            "discount": str(self.discount),
            "total": str(self.total) if self.total else None,
            "amount_paid": str(self.amount_paid),
            "balance_due": str(self.balance_due),
            "is_paid": self.is_paid,
            "is_overdue": self.is_overdue,
            "days_overdue": self.days_overdue,
            "payment_terms": self.payment_terms.model_dump(),
            "notes": self.notes,
            "terms_and_conditions": self.terms_and_conditions,
            "metadata": self.metadata,
        }


class InvoicePDFGenerator:
    """Generate PDF invoices."""

    def __init__(self, company_logo_path: Optional[str] = None):
        """
        Initialize PDF generator.

        Args:
            company_logo_path: Optional path to company logo image.
        """
        self.company_logo_path = company_logo_path

    def generate(self, invoice: InvoiceData) -> bytes:
        """
        Generate PDF from invoice data.

        Args:
            invoice: The invoice data.

        Returns:
            PDF file as bytes.

        Note:
            Requires reportlab library for full PDF generation.
            Falls back to simple text-based PDF if not available.
        """
        try:
            return self._generate_with_reportlab(invoice)
        except ImportError:
            logger.warning("reportlab not installed, using simple PDF format")
            return self._generate_simple(invoice)

    def _generate_with_reportlab(self, invoice: InvoiceData) -> bytes:
        """Generate PDF using reportlab library."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        elements = []

        # Title
        title_style = ParagraphStyle(
            "Title",
            parent=styles["Title"],
            fontSize=24,
            spaceAfter=30,
        )
        elements.append(Paragraph("INVOICE", title_style))

        # Invoice details
        details = [
            ["Invoice Number:", invoice.invoice_number or invoice.invoice_id],
            ["Issue Date:", invoice.issue_date.strftime("%B %d, %Y")],
            ["Due Date:", invoice.due_date.strftime("%B %d, %Y") if invoice.due_date else "N/A"],
        ]
        if invoice.purchase_order:
            details.append(["PO Number:", invoice.purchase_order])

        details_table = Table(details, colWidths=[1.5 * inch, 3 * inch])
        details_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(details_table)
        elements.append(Spacer(1, 20))

        # Addresses
        if invoice.bill_from or invoice.bill_to:
            address_data = [["Bill From:", "Bill To:"]]
            from_addr = invoice.bill_from.format_multiline() if invoice.bill_from else ""
            to_addr = invoice.bill_to.format_multiline() if invoice.bill_to else ""
            address_data.append([from_addr, to_addr])

            address_table = Table(address_data, colWidths=[3 * inch, 3 * inch])
            address_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(address_table)
            elements.append(Spacer(1, 20))

        # Line items
        items_data = [["Description", "Qty", "Unit Price", "Tax", "Total"]]
        for item in invoice.line_items:
            items_data.append([
                item.description,
                str(item.quantity),
                f"{invoice.currency_symbol}{item.unit_price:.2f}",
                f"{item.tax_rate}%",
                f"{invoice.currency_symbol}{item.total:.2f}",
            ])

        items_table = Table(
            items_data,
            colWidths=[3 * inch, 0.75 * inch, 1 * inch, 0.75 * inch, 1 * inch],
        )
        items_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 20))

        # Totals
        totals_data = []
        if invoice.subtotal:
            totals_data.append(["Subtotal:", f"{invoice.currency_symbol}{invoice.subtotal:.2f}"])
        if invoice.tax_total and invoice.tax_total > 0:
            totals_data.append(["Tax:", f"{invoice.currency_symbol}{invoice.tax_total:.2f}"])
        if invoice.discount > 0:
            totals_data.append(["Discount:", f"-{invoice.currency_symbol}{invoice.discount:.2f}"])
        if invoice.total:
            totals_data.append(["Total:", f"{invoice.currency_symbol}{invoice.total:.2f}"])
        if invoice.amount_paid > 0:
            totals_data.append(["Paid:", f"-{invoice.currency_symbol}{invoice.amount_paid:.2f}"])
        totals_data.append(["Balance Due:", f"{invoice.currency_symbol}{invoice.balance_due:.2f}"])

        totals_table = Table(totals_data, colWidths=[5 * inch, 1.5 * inch])
        totals_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ]))
        elements.append(totals_table)
        elements.append(Spacer(1, 30))

        # Payment terms
        if invoice.payment_terms:
            elements.append(Paragraph(
                f"<b>Payment Terms:</b> {invoice.payment_terms.description}",
                styles["Normal"],
            ))
            elements.append(Spacer(1, 10))

        # Notes
        if invoice.notes:
            elements.append(Paragraph("<b>Notes:</b>", styles["Normal"]))
            elements.append(Paragraph(invoice.notes, styles["Normal"]))
            elements.append(Spacer(1, 10))

        # Terms and conditions
        if invoice.terms_and_conditions:
            elements.append(Paragraph("<b>Terms & Conditions:</b>", styles["Normal"]))
            elements.append(Paragraph(invoice.terms_and_conditions, styles["Normal"]))

        # Build PDF
        doc.build(elements)
        return buffer.getvalue()

    def _generate_simple(self, invoice: InvoiceData) -> bytes:
        """Generate simple text-based representation when reportlab is not available."""
        lines = []
        lines.append("=" * 60)
        lines.append("INVOICE")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Invoice Number: {invoice.invoice_number or invoice.invoice_id}")
        lines.append(f"Issue Date: {invoice.issue_date.strftime('%B %d, %Y')}")
        if invoice.due_date:
            lines.append(f"Due Date: {invoice.due_date.strftime('%B %d, %Y')}")
        lines.append("")

        if invoice.bill_to:
            lines.append("Bill To:")
            lines.append(invoice.bill_to.format_multiline())
            lines.append("")

        lines.append("-" * 60)
        lines.append(f"{'Description':<30} {'Qty':>5} {'Price':>10} {'Total':>10}")
        lines.append("-" * 60)

        for item in invoice.line_items:
            lines.append(
                f"{item.description:<30} {str(item.quantity):>5} "
                f"{invoice.currency_symbol}{item.unit_price:>9.2f} "
                f"{invoice.currency_symbol}{item.total:>9.2f}"
            )

        lines.append("-" * 60)

        if invoice.subtotal:
            lines.append(f"{'Subtotal:':>45} {invoice.currency_symbol}{invoice.subtotal:>9.2f}")
        if invoice.tax_total and invoice.tax_total > 0:
            lines.append(f"{'Tax:':>45} {invoice.currency_symbol}{invoice.tax_total:>9.2f}")
        if invoice.total:
            lines.append(f"{'Total:':>45} {invoice.currency_symbol}{invoice.total:>9.2f}")
        lines.append(f"{'Balance Due:':>45} {invoice.currency_symbol}{invoice.balance_due:>9.2f}")

        lines.append("")
        lines.append("=" * 60)

        if invoice.notes:
            lines.append("")
            lines.append("Notes:")
            lines.append(invoice.notes)

        return "\n".join(lines).encode("utf-8")


def create_sample_invoice() -> InvoiceData:
    """Create a sample invoice for testing."""
    invoice = InvoiceData(
        invoice_id="INV-2024-001",
        invoice_number="2024-001",
        bill_from=InvoiceAddress(
            name="Acme Corporation",
            company="Acme Corp",
            street="123 Business Ave",
            city="New York",
            state="NY",
            postal_code="10001",
            country="USA",
            email="billing@acme.com",
        ),
        bill_to=InvoiceAddress(
            name="John Doe",
            company="Client Inc",
            street="456 Customer St",
            city="Los Angeles",
            state="CA",
            postal_code="90001",
            country="USA",
            email="john@client.com",
        ),
        notes="Thank you for your business!",
        terms_and_conditions="Payment is due within 30 days of invoice date.",
    )

    # Add line items
    invoice.add_line_item(
        description="Web Development Services",
        quantity=Decimal("40"),
        unit_price=Decimal("150.00"),
        tax_rate=Decimal("8.5"),
    )
    invoice.add_line_item(
        description="Hosting (Annual)",
        quantity=Decimal("1"),
        unit_price=Decimal("499.00"),
        tax_rate=Decimal("8.5"),
    )
    invoice.add_line_item(
        description="SSL Certificate",
        quantity=Decimal("1"),
        unit_price=Decimal("99.00"),
        tax_rate=Decimal("0"),
    )

    return invoice
