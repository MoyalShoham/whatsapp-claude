"""Database-backed invoice store implementation."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import (
    AuditLogModel,
    ConversationModel,
    CustomerModel,
    InvoiceHistoryModel,
    InvoiceModel,
)
from database.session import get_session, session_scope
from state_machine.invoice_state import InvoiceFSM, InvoiceState

logger = logging.getLogger(__name__)


class DatabaseInvoiceStore:
    """
    Production invoice store using SQLAlchemy.

    Implements the InvoiceStore protocol for persistent storage.
    """

    def __init__(self, session: Optional[Session] = None):
        """
        Initialize the database store.

        Args:
            session: Optional SQLAlchemy session. If not provided,
                    creates new sessions for each operation.
        """
        self._session = session

    def _get_session(self) -> Session:
        """Get or create a session."""
        if self._session:
            return self._session
        return get_session()

    def get_fsm(self, invoice_id: str) -> Optional[InvoiceFSM]:
        """
        Get state machine for invoice from database.

        Args:
            invoice_id: The invoice identifier.

        Returns:
            InvoiceFSM instance or None if not found.
        """
        with session_scope() as session:
            invoice = (
                session.query(InvoiceModel)
                .filter(InvoiceModel.invoice_id == invoice_id)
                .first()
            )

            if not invoice:
                return None

            # Restore FSM from database state
            fsm = InvoiceFSM(
                invoice_id=invoice.invoice_id,
                initial_state=invoice.state,
            )

            # Restore history from database
            history_records = (
                session.query(InvoiceHistoryModel)
                .filter(InvoiceHistoryModel.invoice_id == invoice_id)
                .order_by(InvoiceHistoryModel.created_at)
                .all()
            )

            # Replace FSM history with database history
            fsm._history = [
                {
                    "timestamp": record.created_at.isoformat(),
                    "source": record.previous_state,
                    "dest": record.new_state,
                    "trigger": record.trigger,
                    "triggered_by": record.triggered_by,
                    "reason": record.reason,
                }
                for record in history_records
            ]

            return fsm

    def save_fsm(self, fsm: InvoiceFSM) -> None:
        """
        Save state machine to database.

        Args:
            fsm: The InvoiceFSM instance to save.
        """
        with session_scope() as session:
            # Get or create invoice record
            invoice = (
                session.query(InvoiceModel)
                .filter(InvoiceModel.invoice_id == fsm.invoice_id)
                .first()
            )

            if invoice:
                # Update existing
                invoice.state = fsm.current_state
                invoice.is_terminal = fsm.is_terminal
                invoice.updated_at = datetime.utcnow()
                if fsm.is_terminal:
                    invoice.closed_at = datetime.utcnow()
            else:
                # Create new
                invoice = InvoiceModel(
                    invoice_id=fsm.invoice_id,
                    state=fsm.current_state,
                    is_terminal=fsm.is_terminal,
                )
                session.add(invoice)

            # Save latest history entry if exists
            if fsm._history:
                latest = fsm._history[-1]
                # Check if this history entry already exists
                existing = (
                    session.query(InvoiceHistoryModel)
                    .filter(
                        InvoiceHistoryModel.invoice_id == fsm.invoice_id,
                        InvoiceHistoryModel.trigger == latest["trigger"],
                        InvoiceHistoryModel.new_state == latest["dest"],
                    )
                    .first()
                )

                if not existing:
                    history = InvoiceHistoryModel(
                        invoice_id=fsm.invoice_id,
                        previous_state=latest.get("source"),
                        new_state=latest["dest"],
                        trigger=latest["trigger"],
                        triggered_by=latest.get("triggered_by"),
                        reason=latest.get("reason"),
                    )
                    session.add(history)

            logger.debug(f"Saved invoice {fsm.invoice_id} with state {fsm.current_state}")

    def create_invoice(
        self,
        invoice_id: str,
        customer_id: Optional[str] = None,
        amount: Optional[Decimal] = None,
        currency: str = "USD",
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InvoiceFSM:
        """
        Create a new invoice in the database.

        Args:
            invoice_id: Unique invoice identifier.
            customer_id: Optional customer ID.
            amount: Invoice amount.
            currency: Currency code (default USD).
            description: Invoice description.
            due_date: Payment due date.
            metadata: Additional metadata.

        Returns:
            New InvoiceFSM instance.
        """
        with session_scope() as session:
            # Check if invoice already exists
            existing = (
                session.query(InvoiceModel)
                .filter(InvoiceModel.invoice_id == invoice_id)
                .first()
            )
            if existing:
                raise ValueError(f"Invoice {invoice_id} already exists")

            # Create invoice record
            invoice = InvoiceModel(
                invoice_id=invoice_id,
                customer_id=customer_id,
                amount=amount,
                currency=currency,
                description=description,
                due_date=due_date,
                state=InvoiceState.NEW,
                is_terminal=False,
                metadata=metadata,
            )
            session.add(invoice)

            # Create initial history
            history = InvoiceHistoryModel(
                invoice_id=invoice_id,
                previous_state=None,
                new_state=InvoiceState.NEW,
                trigger="created",
                triggered_by="system",
            )
            session.add(history)

        # Return FSM
        return InvoiceFSM(invoice_id=invoice_id)

    def list_invoices(
        self,
        state: Optional[str] = None,
        customer_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List invoices with optional filtering.

        Args:
            state: Filter by state.
            customer_id: Filter by customer.
            limit: Maximum results.
            offset: Skip first N results.

        Returns:
            List of invoice dictionaries.
        """
        with session_scope() as session:
            query = session.query(InvoiceModel)

            if state:
                query = query.filter(InvoiceModel.state == state)
            if customer_id:
                query = query.filter(InvoiceModel.customer_id == customer_id)

            query = query.order_by(InvoiceModel.created_at.desc())
            query = query.limit(limit).offset(offset)

            invoices = query.all()

            return [
                {
                    "invoice_id": inv.invoice_id,
                    "customer_id": inv.customer_id,
                    "amount": str(inv.amount) if inv.amount else None,
                    "currency": inv.currency,
                    "description": inv.description,
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                    "state": inv.state,
                    "is_terminal": inv.is_terminal,
                    "created_at": inv.created_at.isoformat(),
                    "updated_at": inv.updated_at.isoformat(),
                }
                for inv in invoices
            ]

    def get_invoice(self, invoice_id: str) -> Optional[dict[str, Any]]:
        """
        Get invoice details.

        Args:
            invoice_id: The invoice identifier.

        Returns:
            Invoice dictionary or None.
        """
        with session_scope() as session:
            invoice = (
                session.query(InvoiceModel)
                .filter(InvoiceModel.invoice_id == invoice_id)
                .first()
            )

            if not invoice:
                return None

            return {
                "invoice_id": invoice.invoice_id,
                "customer_id": invoice.customer_id,
                "amount": str(invoice.amount) if invoice.amount else None,
                "currency": invoice.currency,
                "description": invoice.description,
                "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                "state": invoice.state,
                "is_terminal": invoice.is_terminal,
                "created_at": invoice.created_at.isoformat(),
                "updated_at": invoice.updated_at.isoformat(),
                "closed_at": invoice.closed_at.isoformat() if invoice.closed_at else None,
                "metadata": invoice.extra_metadata,
            }

    def update_invoice(
        self,
        invoice_id: str,
        amount: Optional[Decimal] = None,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Update invoice details (not state).

        Args:
            invoice_id: The invoice identifier.
            amount: New amount.
            description: New description.
            due_date: New due date.
            metadata: New/merged metadata.

        Returns:
            True if updated, False if not found.
        """
        with session_scope() as session:
            invoice = (
                session.query(InvoiceModel)
                .filter(InvoiceModel.invoice_id == invoice_id)
                .first()
            )

            if not invoice:
                return False

            if amount is not None:
                invoice.amount = amount
            if description is not None:
                invoice.description = description
            if due_date is not None:
                invoice.due_date = due_date
            if metadata is not None:
                if invoice.extra_metadata:
                    invoice.extra_metadata.update(metadata)
                else:
                    invoice.extra_metadata = metadata

            return True

    # Customer operations

    def get_or_create_customer(
        self,
        phone: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get or create a customer by phone number.

        Args:
            phone: Customer phone number.
            name: Customer name.
            email: Customer email.

        Returns:
            Customer dictionary.
        """
        with session_scope() as session:
            customer = (
                session.query(CustomerModel)
                .filter(CustomerModel.phone == phone)
                .first()
            )

            if not customer:
                customer = CustomerModel(
                    phone=phone,
                    name=name,
                    email=email,
                )
                session.add(customer)
                session.flush()

            return {
                "id": customer.id,
                "phone": customer.phone,
                "name": customer.name,
                "email": customer.email,
                "created_at": customer.created_at.isoformat(),
            }

    def get_customer_invoices(
        self,
        customer_id: str,
        include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Get all invoices for a customer.

        Args:
            customer_id: The customer ID.
            include_closed: Whether to include closed invoices.

        Returns:
            List of invoice dictionaries.
        """
        with session_scope() as session:
            query = session.query(InvoiceModel).filter(
                InvoiceModel.customer_id == customer_id
            )

            if not include_closed:
                query = query.filter(InvoiceModel.is_terminal == False)

            query = query.order_by(InvoiceModel.created_at.desc())

            return [
                {
                    "invoice_id": inv.invoice_id,
                    "amount": str(inv.amount) if inv.amount else None,
                    "currency": inv.currency,
                    "state": inv.state,
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                }
                for inv in query.all()
            ]

    # Conversation operations

    def save_conversation(
        self,
        customer_id: str,
        role: str,
        content: str,
        message_id: Optional[str] = None,
        invoice_id: Optional[str] = None,
        intent: Optional[str] = None,
        channel: str = "whatsapp",
    ) -> None:
        """
        Save a conversation message.

        Args:
            customer_id: The customer ID.
            role: Message role (user/assistant).
            content: Message content.
            message_id: External message ID.
            invoice_id: Related invoice ID.
            intent: Detected intent.
            channel: Communication channel.
        """
        with session_scope() as session:
            conversation = ConversationModel(
                customer_id=customer_id,
                channel=channel,
                role=role,
                content=content,
                message_id=message_id,
                invoice_id=invoice_id,
                intent=intent,
            )
            session.add(conversation)

    def get_conversation_history(
        self,
        customer_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get recent conversation history for a customer.

        Args:
            customer_id: The customer ID.
            limit: Maximum messages to return.

        Returns:
            List of conversation messages.
        """
        with session_scope() as session:
            messages = (
                session.query(ConversationModel)
                .filter(ConversationModel.customer_id == customer_id)
                .order_by(ConversationModel.created_at.desc())
                .limit(limit)
                .all()
            )

            # Return in chronological order
            return [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "invoice_id": msg.invoice_id,
                    "intent": msg.intent,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in reversed(messages)
            ]

    # Audit operations

    def log_audit(
        self,
        action: str,
        invoice_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        session_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Log an audit entry.

        Args:
            action: The action being logged.
            invoice_id: Related invoice ID.
            customer_id: Related customer ID.
            session_id: Session identifier.
            details: Additional details.
        """
        with session_scope() as session:
            audit = AuditLogModel(
                action=action,
                invoice_id=invoice_id,
                customer_id=customer_id,
                session_id=session_id,
                details=details,
            )
            session.add(audit)

    def get_audit_log(
        self,
        invoice_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query audit log.

        Args:
            invoice_id: Filter by invoice.
            action: Filter by action.
            limit: Maximum entries.

        Returns:
            List of audit entries.
        """
        with session_scope() as session:
            query = session.query(AuditLogModel)

            if invoice_id:
                query = query.filter(AuditLogModel.invoice_id == invoice_id)
            if action:
                query = query.filter(AuditLogModel.action == action)

            query = query.order_by(AuditLogModel.created_at.desc()).limit(limit)

            return [
                {
                    "entry_id": entry.entry_id,
                    "action": entry.action,
                    "invoice_id": entry.invoice_id,
                    "customer_id": entry.customer_id,
                    "details": entry.details,
                    "created_at": entry.created_at.isoformat(),
                }
                for entry in query.all()
            ]

    # Statistics

    def get_stats(self) -> dict[str, Any]:
        """
        Get invoice statistics.

        Returns:
            Statistics dictionary.
        """
        with session_scope() as session:
            total = session.query(InvoiceModel).count()
            by_state = {}

            for state in InvoiceState.all_states():
                count = (
                    session.query(InvoiceModel)
                    .filter(InvoiceModel.state == state)
                    .count()
                )
                if count > 0:
                    by_state[state] = count

            open_count = (
                session.query(InvoiceModel)
                .filter(InvoiceModel.is_terminal == False)
                .count()
            )

            return {
                "total_invoices": total,
                "open_invoices": open_count,
                "closed_invoices": total - open_count,
                "by_state": by_state,
            }
