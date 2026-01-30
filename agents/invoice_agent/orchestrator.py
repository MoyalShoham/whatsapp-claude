"""
Invoice Orchestrator - FSM Validator, Tool Executor, and Audit Boundary.

This module is NOT an LLM-based agent. It provides:
- FSM validation (state machine enforcement)
- Tool execution (business actions)
- Event system (audit triggers)

The orchestrator does NOT interpret user intent or talk to the LLM.
Intent interpretation is handled by ConversationalAgent.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from state_machine.invoice_state import InvoiceFSM, InvoiceState, TransitionError
from tools.base import InMemoryInvoiceStore

logger = logging.getLogger(__name__)


# ============================================================================
# Error Types
# ============================================================================


class StateError(Exception):
    """Raised when state transition is invalid."""

    def __init__(
        self,
        message: str,
        current_state: str,
        attempted_action: str,
        invoice_id: Optional[str] = None,
    ):
        self.current_state = current_state
        self.attempted_action = attempted_action
        self.invoice_id = invoice_id
        super().__init__(message)


class ToolError(Exception):
    """Raised when tool execution fails."""

    def __init__(
        self,
        message: str,
        tool_name: str,
        invoice_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.tool_name = tool_name
        self.invoice_id = invoice_id
        self.details = details or {}
        super().__init__(message)


# ============================================================================
# Events
# ============================================================================


@dataclass
class InvoiceEvent:
    """Event emitted when invoice state changes."""

    event_type: str
    invoice_id: str
    customer_id: Optional[str]
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "invoice_id": self.invoice_id,
            "customer_id": self.customer_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }


class EventSubscriber:
    """Protocol for event subscribers."""

    def on_event(self, event: InvoiceEvent) -> None:
        """Handle an event."""
        raise NotImplementedError


class EventBus:
    """Simple event bus for invoice events."""

    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []
        self._event_history: list[InvoiceEvent] = []
        self._fired_events: set[str] = set()  # For idempotency

    def subscribe(self, subscriber: EventSubscriber) -> None:
        """Add a subscriber."""
        self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        """Remove a subscriber."""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def publish(self, event: InvoiceEvent) -> None:
        """
        Publish an event to all subscribers.

        Events are idempotent - same event_id will only fire once.
        """
        # Idempotency check
        event_key = f"{event.event_type}:{event.invoice_id}:{event.event_id}"
        if event_key in self._fired_events:
            logger.debug(f"Event already fired, skipping: {event_key}")
            return

        self._fired_events.add(event_key)
        self._event_history.append(event)

        logger.info(f"Publishing event: {event.event_type} for {event.invoice_id}")

        for subscriber in self._subscribers:
            try:
                subscriber.on_event(event)
            except Exception as e:
                logger.error(f"Subscriber error handling {event.event_type}: {e}")

    def get_history(self) -> list[InvoiceEvent]:
        """Get all published events."""
        return self._event_history.copy()

    def clear_history(self) -> None:
        """Clear event history (for testing)."""
        self._event_history.clear()
        self._fired_events.clear()


# ============================================================================
# Tool Result
# ============================================================================


@dataclass
class ToolExecutionResult:
    """Result of executing a tool through the orchestrator."""

    success: bool
    message: str
    invoice_id: Optional[str] = None
    previous_state: Optional[str] = None
    current_state: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    events_fired: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "message": self.message,
            "invoice_id": self.invoice_id,
            "previous_state": self.previous_state,
            "current_state": self.current_state,
            "data": self.data,
            "error": self.error,
            "events_fired": self.events_fired,
        }


# ============================================================================
# State Transition Events Mapping
# ============================================================================


TRANSITION_EVENTS: dict[tuple[str, str], str] = {
    (InvoiceState.AWAITING_APPROVAL, InvoiceState.APPROVED): "invoice_approved",
    (InvoiceState.PAYMENT_PENDING, InvoiceState.PAID): "invoice_paid",
    (InvoiceState.PAID, InvoiceState.CLOSED): "invoice_closed",
    (InvoiceState.REJECTED, InvoiceState.CLOSED): "invoice_closed",
    (InvoiceState.APPROVED, InvoiceState.DISPUTED): "invoice_disputed",
    (InvoiceState.PAYMENT_PENDING, InvoiceState.DISPUTED): "invoice_disputed",
    (InvoiceState.PAID, InvoiceState.DISPUTED): "invoice_disputed",
}


# ============================================================================
# Orchestrator
# ============================================================================


class InvoiceOrchestrator:
    """
    FSM validator, tool executor, and audit boundary.

    This class does NOT interpret user intent or use LLM.
    It provides:
    1. Invoice CRUD operations
    2. FSM state validation
    3. Tool execution with state transition
    4. Event firing for audit

    The ConversationalAgent calls this orchestrator to execute tools.
    """

    def __init__(
        self,
        store: Optional[InMemoryInvoiceStore] = None,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            store: Invoice store. Creates new if not provided.
            event_bus: Event bus for publishing events. Creates new if not provided.
        """
        self.store = store or InMemoryInvoiceStore()
        self.event_bus = event_bus or EventBus()

    # ========== Invoice CRUD ==========

    def create_invoice(
        self,
        invoice_id: str,
        customer_id: Optional[str] = None,
    ) -> InvoiceFSM:
        """Create a new invoice."""
        fsm = self.store.create_invoice(invoice_id)
        logger.info(f"Created invoice {invoice_id}")

        # Fire creation event
        self._fire_event(
            event_type="invoice_created",
            invoice_id=invoice_id,
            customer_id=customer_id,
            payload={"state": fsm.current_state},
        )

        return fsm

    def get_invoice(self, invoice_id: str) -> Optional[InvoiceFSM]:
        """Get an invoice FSM."""
        return self.store.get_fsm(invoice_id)

    def get_invoice_state(self, invoice_id: str) -> Optional[str]:
        """Get current state of an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        return fsm.current_state if fsm else None

    def list_invoices(self, state_filter: Optional[str] = None) -> list[dict[str, Any]]:
        """List all invoices, optionally filtered by state."""
        all_ids = self.store.list_invoices()
        invoices = []

        for inv_id in all_ids:
            fsm = self.store.get_fsm(inv_id)
            if fsm:
                if state_filter and fsm.current_state != state_filter:
                    continue
                invoices.append({
                    "invoice_id": inv_id,
                    "state": fsm.current_state,
                    "is_terminal": fsm.is_terminal,
                    "available_triggers": fsm.get_available_triggers(),
                })

        return invoices

    # ========== FSM Operations ==========

    def can_execute(self, invoice_id: str, trigger: str) -> bool:
        """Check if a trigger can be executed on an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return False
        return fsm.can_trigger(trigger)

    def get_available_actions(self, invoice_id: str) -> list[str]:
        """Get available actions for an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return []
        return fsm.get_available_triggers()

    def execute_transition(
        self,
        invoice_id: str,
        trigger: str,
        customer_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> ToolExecutionResult:
        """
        Execute a state transition on an invoice.

        This is the core method for all state-changing operations.
        It validates the transition, executes it, and fires events.

        Args:
            invoice_id: The invoice to transition.
            trigger: The trigger name (approve, reject, etc.).
            customer_id: Customer ID for audit.
            reason: Optional reason (for reject, dispute).

        Returns:
            ToolExecutionResult with transition details.
        """
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return ToolExecutionResult(
                success=False,
                message=f"Invoice {invoice_id} not found",
                error=f"Invoice {invoice_id} not found",
            )

        previous_state = fsm.current_state

        # Validate transition is allowed
        if not fsm.can_trigger(trigger):
            available = fsm.get_available_triggers()
            return ToolExecutionResult(
                success=False,
                message=f"Cannot {trigger} - invoice is in '{previous_state}' state",
                invoice_id=invoice_id,
                current_state=previous_state,
                error=f"Invalid transition: {trigger} from {previous_state}. Available: {available}",
            )

        # Execute transition
        try:
            fsm.trigger(trigger)
            self.store.save_fsm(fsm)
        except TransitionError as e:
            return ToolExecutionResult(
                success=False,
                message=str(e),
                invoice_id=invoice_id,
                current_state=previous_state,
                error=str(e),
            )

        current_state = fsm.current_state
        events_fired = []

        # Fire state transition event
        event_type = self._maybe_fire_transition_event(
            invoice_id=invoice_id,
            previous_state=previous_state,
            current_state=current_state,
            customer_id=customer_id,
            reason=reason,
        )
        if event_type:
            events_fired.append(event_type)

        # Build success message
        trigger_messages = {
            "approve": f"Invoice {invoice_id} has been approved!",
            "reject": f"Invoice {invoice_id} has been rejected.",
            "confirm_payment": f"Payment confirmed for invoice {invoice_id}. Thank you!",
            "dispute": f"Dispute created for invoice {invoice_id}. We'll review it shortly.",
            "close": f"Invoice {invoice_id} has been closed.",
            "send_invoice": f"Invoice {invoice_id} has been sent.",
            "request_approval": f"Approval requested for invoice {invoice_id}.",
            "request_payment": f"Payment requested for invoice {invoice_id}.",
        }
        message = trigger_messages.get(trigger, f"Invoice {invoice_id} updated: {previous_state} -> {current_state}")

        return ToolExecutionResult(
            success=True,
            message=message,
            invoice_id=invoice_id,
            previous_state=previous_state,
            current_state=current_state,
            events_fired=events_fired,
        )

    # ========== Event Handling ==========

    def _fire_event(
        self,
        event_type: str,
        invoice_id: str,
        customer_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Fire an event."""
        event = InvoiceEvent(
            event_type=event_type,
            invoice_id=invoice_id,
            customer_id=customer_id,
            timestamp=datetime.utcnow(),
            payload=payload or {},
        )
        self.event_bus.publish(event)

    def _maybe_fire_transition_event(
        self,
        invoice_id: str,
        previous_state: str,
        current_state: str,
        customer_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Optional[str]:
        """Fire event if state transition warrants it."""
        event_type = TRANSITION_EVENTS.get((previous_state, current_state))

        if event_type:
            payload = {
                "previous_state": previous_state,
                "current_state": current_state,
            }
            if reason:
                payload["reason"] = reason

            self._fire_event(
                event_type=event_type,
                invoice_id=invoice_id,
                customer_id=customer_id,
                payload=payload,
            )
            return event_type

        return None

    def subscribe_to_events(self, subscriber: EventSubscriber) -> None:
        """Add an event subscriber."""
        self.event_bus.subscribe(subscriber)

    def get_event_history(self) -> list[InvoiceEvent]:
        """Get all fired events."""
        return self.event_bus.get_history()
