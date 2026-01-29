"""Core domain models for the invoice automation system."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class InvoiceStatus(str, Enum):
    """Possible invoice statuses matching state machine states."""

    NEW = "new"
    INVOICE_SENT = "invoice_sent"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    DISPUTED = "disputed"
    CLOSED = "closed"


class ApprovalDecision(str, Enum):
    """Approval decision types."""

    APPROVED = "approved"
    REJECTED = "rejected"


class Intent(str, Enum):
    """Supported message intents."""

    INVOICE_QUESTION = "invoice_question"
    INVOICE_APPROVAL = "invoice_approval"
    INVOICE_REJECTION = "invoice_rejection"
    PAYMENT_CONFIRMATION = "payment_confirmation"
    INVOICE_DISPUTE = "invoice_dispute"
    REQUEST_INVOICE_COPY = "request_invoice_copy"
    GENERAL_QUESTION = "general_question"


class Customer(BaseModel):
    """Customer entity."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    email: str
    phone: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        frozen = True


class Invoice(BaseModel):
    """Invoice entity."""

    id: str = Field(..., description="Invoice identifier (e.g., INV-001)")
    customer_id: UUID
    amount: Decimal = Field(..., ge=0, description="Invoice amount")
    currency: str = Field(default="USD", max_length=3)
    status: InvoiceStatus = Field(default=InvoiceStatus.NEW)
    due_date: datetime
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: object) -> None:
        """Update the updated_at timestamp when model changes."""
        object.__setattr__(self, "updated_at", datetime.utcnow())


class Approval(BaseModel):
    """Approval record for an invoice."""

    id: UUID = Field(default_factory=uuid4)
    invoice_id: str
    decision: ApprovalDecision
    approver_id: UUID
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        frozen = True


class Payment(BaseModel):
    """Payment record for an invoice."""

    id: UUID = Field(default_factory=uuid4)
    invoice_id: str
    amount: Decimal = Field(..., ge=0)
    currency: str = Field(default="USD", max_length=3)
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        frozen = True


class ConversationMessage(BaseModel):
    """A single message in a conversation."""

    id: UUID = Field(default_factory=uuid4)
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str
    intent: Optional[Intent] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(BaseModel):
    """Conversation context for invoice interactions."""

    id: UUID = Field(default_factory=uuid4)
    invoice_id: Optional[str] = None
    customer_id: Optional[UUID] = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def add_message(
        self, role: str, content: str, intent: Optional[Intent] = None
    ) -> ConversationMessage:
        """Add a message to the conversation."""
        message = ConversationMessage(role=role, content=content, intent=intent)
        self.messages.append(message)
        object.__setattr__(self, "updated_at", datetime.utcnow())
        return message
