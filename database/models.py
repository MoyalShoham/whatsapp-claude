"""
SQLAlchemy models for invoice automation system.

Tables:
- invoices: Invoice records with state
- invoice_history: State transition history
- customers: Customer information
- audit_log: System audit trail
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class CustomerModel(Base):
    """Customer table."""

    __tablename__ = "customers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    phone = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    invoices = relationship("InvoiceModel", back_populates="customer")

    def __repr__(self) -> str:
        return f"<Customer {self.phone}>"


class InvoiceModel(Base):
    """Invoice table."""

    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(String(50), unique=True, nullable=False, index=True)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True)

    # Invoice details
    amount = Column(Numeric(10, 2), nullable=True)
    currency = Column(String(3), default="USD", nullable=False)
    description = Column(Text, nullable=True)
    due_date = Column(DateTime, nullable=True)

    # State machine
    state = Column(String(50), default="new", nullable=False, index=True)
    is_terminal = Column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    # Extra metadata (renamed from 'metadata' to avoid SQLAlchemy reserved name)
    extra_metadata = Column(JSON, nullable=True)

    # Relationships
    customer = relationship("CustomerModel", back_populates="invoices")
    history = relationship(
        "InvoiceHistoryModel",
        back_populates="invoice",
        order_by="InvoiceHistoryModel.created_at",
    )

    # Indexes
    __table_args__ = (
        Index("ix_invoices_state_created", "state", "created_at"),
        Index("ix_invoices_customer_state", "customer_id", "state"),
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_id} state={self.state}>"


class InvoiceHistoryModel(Base):
    """Invoice state transition history."""

    __tablename__ = "invoice_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(String(50), ForeignKey("invoices.invoice_id"), nullable=False, index=True)

    # Transition details
    previous_state = Column(String(50), nullable=True)
    new_state = Column(String(50), nullable=False)
    trigger = Column(String(50), nullable=False)

    # Who/what triggered
    triggered_by = Column(String(255), nullable=True)  # customer_id, system, admin
    reason = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    invoice = relationship("InvoiceModel", back_populates="history")

    def __repr__(self) -> str:
        return f"<History {self.invoice_id}: {self.previous_state} -> {self.new_state}>"


class AuditLogModel(Base):
    """System audit log."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(String(36), unique=True, default=lambda: str(uuid4()), nullable=False)
    session_id = Column(String(36), nullable=True, index=True)

    # Action details
    action = Column(String(50), nullable=False, index=True)
    invoice_id = Column(String(50), nullable=True, index=True)
    customer_id = Column(String(36), nullable=True, index=True)

    # Payload
    details = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Indexes
    __table_args__ = (
        Index("ix_audit_action_created", "action", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Audit {self.action} invoice={self.invoice_id}>"


class ConversationModel(Base):
    """Conversation history for context tracking."""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    channel = Column(String(20), default="whatsapp", nullable=False)

    # Message
    role = Column(String(20), nullable=False)  # user, assistant
    content = Column(Text, nullable=False)
    message_id = Column(String(100), nullable=True)  # External message ID

    # Context
    invoice_id = Column(String(50), nullable=True)
    intent = Column(String(50), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Indexes
    __table_args__ = (
        Index("ix_conv_customer_created", "customer_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.customer_id} {self.role}>"
