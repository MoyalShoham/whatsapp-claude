"""
Production infrastructure components.

This module contains:
- Audit logging (append-only)
- Enhanced event system
- Structured error handling
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TextIO
from uuid import uuid4

logger = logging.getLogger(__name__)


# ============================================================================
# Audit Log
# ============================================================================


class AuditAction(str, Enum):
    """Types of auditable actions."""

    MESSAGE_RECEIVED = "message_received"
    ROUTING_DECISION = "routing_decision"
    TOOL_EXECUTED = "tool_executed"
    STATE_TRANSITION = "state_transition"
    EVENT_FIRED = "event_fired"
    ERROR_OCCURRED = "error_occurred"
    BLOCKED_ACTION = "blocked_action"


@dataclass
class AuditEntry:
    """A single audit log entry."""

    action: AuditAction
    timestamp: datetime
    invoice_id: Optional[str]
    customer_id: Optional[str]
    details: dict[str, Any]
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entry_id": self.entry_id,
            "action": self.action.value,
            "timestamp": self.timestamp.isoformat(),
            "invoice_id": self.invoice_id,
            "customer_id": self.customer_id,
            "session_id": self.session_id,
            "details": self.details,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class AuditLog:
    """
    Append-only audit log for tracking all system actions.

    Features:
    - In-memory storage (default)
    - Optional file persistence
    - Append-only (no modifications or deletions)
    - Structured entries with timestamps
    """

    def __init__(
        self,
        file_path: Optional[Path] = None,
        session_id: Optional[str] = None,
    ):
        """
        Initialize the audit log.

        Args:
            file_path: Optional path for file persistence.
            session_id: Optional session identifier for grouping entries.
        """
        self._entries: list[AuditEntry] = []
        self._file_path = file_path
        self._file_handle: Optional[TextIO] = None
        self.session_id = session_id or str(uuid4())

        if file_path:
            self._file_handle = open(file_path, "a", encoding="utf-8")

    def log(
        self,
        action: AuditAction,
        invoice_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        **details: Any,
    ) -> AuditEntry:
        """
        Log an action.

        Args:
            action: Type of action.
            invoice_id: Invoice ID if applicable.
            customer_id: Customer ID if applicable.
            **details: Additional details to log.

        Returns:
            The created audit entry.
        """
        entry = AuditEntry(
            action=action,
            timestamp=datetime.utcnow(),
            invoice_id=invoice_id,
            customer_id=customer_id,
            details=details,
            session_id=self.session_id,
        )

        self._entries.append(entry)

        if self._file_handle:
            self._file_handle.write(entry.to_json() + "\n")
            self._file_handle.flush()

        logger.debug(f"Audit: {action.value} - {invoice_id or 'N/A'}")

        return entry

    def log_message_received(
        self,
        message: str,
        invoice_id: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> AuditEntry:
        """Log a received message."""
        return self.log(
            AuditAction.MESSAGE_RECEIVED,
            invoice_id=invoice_id,
            customer_id=customer_id,
            message=message[:500],  # Truncate long messages
        )

    def log_routing_decision(
        self,
        intent: str,
        tool: str,
        confidence: str,
        invoice_id: Optional[str] = None,
        warnings: Optional[list[str]] = None,
    ) -> AuditEntry:
        """Log a routing decision."""
        return self.log(
            AuditAction.ROUTING_DECISION,
            invoice_id=invoice_id,
            intent=intent,
            tool=tool,
            confidence=confidence,
            warnings=warnings or [],
        )

    def log_tool_executed(
        self,
        tool_name: str,
        success: bool,
        invoice_id: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log a tool execution."""
        return self.log(
            AuditAction.TOOL_EXECUTED,
            invoice_id=invoice_id,
            tool_name=tool_name,
            success=success,
            result=result,
        )

    def log_state_transition(
        self,
        previous_state: str,
        current_state: str,
        trigger: str,
        invoice_id: str,
    ) -> AuditEntry:
        """Log a state transition."""
        return self.log(
            AuditAction.STATE_TRANSITION,
            invoice_id=invoice_id,
            previous_state=previous_state,
            current_state=current_state,
            trigger=trigger,
        )

    def log_event_fired(
        self,
        event_type: str,
        invoice_id: str,
        customer_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log an event firing."""
        return self.log(
            AuditAction.EVENT_FIRED,
            invoice_id=invoice_id,
            customer_id=customer_id,
            event_type=event_type,
            payload=payload or {},
        )

    def log_error(
        self,
        error_type: str,
        error_message: str,
        invoice_id: Optional[str] = None,
        **context: Any,
    ) -> AuditEntry:
        """Log an error."""
        return self.log(
            AuditAction.ERROR_OCCURRED,
            invoice_id=invoice_id,
            error_type=error_type,
            error_message=error_message,
            **context,
        )

    def log_blocked_action(
        self,
        action: str,
        reason: str,
        invoice_id: Optional[str] = None,
        current_state: Optional[str] = None,
    ) -> AuditEntry:
        """Log a blocked action."""
        return self.log(
            AuditAction.BLOCKED_ACTION,
            invoice_id=invoice_id,
            blocked_action=action,
            reason=reason,
            current_state=current_state,
        )

    def get_entries(
        self,
        action: Optional[AuditAction] = None,
        invoice_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[AuditEntry]:
        """
        Get audit entries with optional filtering.

        Args:
            action: Filter by action type.
            invoice_id: Filter by invoice ID.
            since: Filter entries after this timestamp.

        Returns:
            List of matching audit entries.
        """
        entries = self._entries

        if action:
            entries = [e for e in entries if e.action == action]

        if invoice_id:
            entries = [e for e in entries if e.invoice_id == invoice_id]

        if since:
            entries = [e for e in entries if e.timestamp >= since]

        return entries

    def get_all_entries(self) -> list[AuditEntry]:
        """Get all audit entries."""
        return self._entries.copy()

    def close(self) -> None:
        """Close file handle if open."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None


# ============================================================================
# Enhanced Event Types
# ============================================================================


class EventType(str, Enum):
    """All supported event types."""

    # Invoice lifecycle events
    INVOICE_CREATED = "invoice_created"
    INVOICE_SENT = "invoice_sent"
    INVOICE_APPROVED = "invoice_approved"
    INVOICE_REJECTED = "invoice_rejected"
    INVOICE_PAID = "invoice_paid"
    INVOICE_CLOSED = "invoice_closed"
    INVOICE_DISPUTED = "invoice_disputed"
    INVOICE_DISPUTE_RESOLVED = "invoice_dispute_resolved"

    # Reminder events
    INVOICE_OVERDUE = "invoice_overdue"
    PAYMENT_REMINDER = "payment_reminder"

    # Error events
    ACTION_BLOCKED = "action_blocked"
    ERROR_OCCURRED = "error_occurred"


@dataclass
class EnhancedInvoiceEvent:
    """
    Enhanced event with full payload.

    Features:
    - Unique event ID
    - Full timestamp
    - Idempotency key
    - Structured payload
    """

    event_type: EventType
    invoice_id: str
    customer_id: Optional[str]
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    idempotency_key: Optional[str] = None

    def __post_init__(self) -> None:
        """Generate idempotency key if not provided."""
        if not self.idempotency_key:
            # Idempotency key based on event type, invoice, and payload hash
            self.idempotency_key = f"{self.event_type.value}:{self.invoice_id}:{hash(frozenset(self.payload.items()))}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "invoice_id": self.invoice_id,
            "customer_id": self.customer_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
        }


class EnhancedEventSubscriber:
    """
    Protocol for enhanced event subscribers.

    Subscribers must implement on_event and can optionally filter events.
    """

    def get_subscribed_events(self) -> list[EventType]:
        """
        Return list of event types this subscriber wants.

        Override to filter events. Default returns all events.
        """
        return list(EventType)

    def on_event(self, event: EnhancedInvoiceEvent) -> None:
        """Handle an event."""
        raise NotImplementedError


class EnhancedEventBus:
    """
    Enhanced event bus with filtering and idempotency.

    Features:
    - Event type filtering per subscriber
    - Idempotency (same event fires once)
    - Event history
    - Audit log integration
    """

    def __init__(self, audit_log: Optional[AuditLog] = None):
        """
        Initialize the event bus.

        Args:
            audit_log: Optional audit log for logging events.
        """
        self._subscribers: list[EnhancedEventSubscriber] = []
        self._event_history: list[EnhancedInvoiceEvent] = []
        self._fired_keys: set[str] = set()
        self._audit_log = audit_log

    def subscribe(self, subscriber: EnhancedEventSubscriber) -> None:
        """Add a subscriber."""
        self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: EnhancedEventSubscriber) -> None:
        """Remove a subscriber."""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def publish(self, event: EnhancedInvoiceEvent) -> bool:
        """
        Publish an event to all interested subscribers.

        Args:
            event: Event to publish.

        Returns:
            True if event was published, False if already fired (idempotent).
        """
        # Idempotency check
        if event.idempotency_key in self._fired_keys:
            logger.debug(f"Event already fired (idempotent): {event.idempotency_key}")
            return False

        self._fired_keys.add(event.idempotency_key)
        self._event_history.append(event)

        # Log to audit
        if self._audit_log:
            self._audit_log.log_event_fired(
                event_type=event.event_type.value,
                invoice_id=event.invoice_id,
                customer_id=event.customer_id,
                payload=event.payload,
            )

        logger.info(f"Publishing event: {event.event_type.value} for {event.invoice_id}")

        # Notify subscribers
        for subscriber in self._subscribers:
            subscribed_events = subscriber.get_subscribed_events()
            if event.event_type in subscribed_events:
                try:
                    subscriber.on_event(event)
                except Exception as e:
                    logger.error(
                        f"Subscriber error handling {event.event_type.value}: {e}"
                    )

        return True

    def create_and_publish(
        self,
        event_type: EventType,
        invoice_id: str,
        customer_id: Optional[str] = None,
        **payload: Any,
    ) -> EnhancedInvoiceEvent:
        """
        Create and publish an event.

        Args:
            event_type: Type of event.
            invoice_id: Invoice ID.
            customer_id: Optional customer ID.
            **payload: Event payload.

        Returns:
            The created event.
        """
        event = EnhancedInvoiceEvent(
            event_type=event_type,
            invoice_id=invoice_id,
            customer_id=customer_id,
            timestamp=datetime.utcnow(),
            payload=payload,
        )
        self.publish(event)
        return event

    def get_history(
        self,
        event_type: Optional[EventType] = None,
        invoice_id: Optional[str] = None,
    ) -> list[EnhancedInvoiceEvent]:
        """Get event history with optional filtering."""
        events = self._event_history

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        if invoice_id:
            events = [e for e in events if e.invoice_id == invoice_id]

        return events

    def clear_history(self) -> None:
        """Clear event history (for testing)."""
        self._event_history.clear()
        self._fired_keys.clear()


# ============================================================================
# Overdue Invoice Checker
# ============================================================================


class OverdueInvoiceChecker:
    """
    Checks for overdue invoices and fires events.

    This would typically be run by a scheduler/cron job.
    """

    def __init__(
        self,
        event_bus: EnhancedEventBus,
        get_invoices: Callable[[], list[dict[str, Any]]],
    ):
        """
        Initialize the checker.

        Args:
            event_bus: Event bus for publishing overdue events.
            get_invoices: Callback to get list of invoices with due dates.
        """
        self.event_bus = event_bus
        self.get_invoices = get_invoices

    def check_overdue(self) -> list[str]:
        """
        Check for overdue invoices and fire events.

        Returns:
            List of invoice IDs that are overdue.
        """
        overdue_ids = []
        now = datetime.utcnow()

        for invoice in self.get_invoices():
            due_date = invoice.get("due_date")
            if not due_date:
                continue

            # Parse due date if string
            if isinstance(due_date, str):
                due_date = datetime.fromisoformat(due_date)

            # Check if overdue and in payment_pending state
            if due_date < now and invoice.get("state") == "payment_pending":
                invoice_id = invoice["invoice_id"]
                overdue_ids.append(invoice_id)

                self.event_bus.create_and_publish(
                    event_type=EventType.INVOICE_OVERDUE,
                    invoice_id=invoice_id,
                    customer_id=invoice.get("customer_id"),
                    due_date=due_date.isoformat(),
                    days_overdue=(now - due_date).days,
                )

        return overdue_ids
