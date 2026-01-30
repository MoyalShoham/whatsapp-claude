"""
Invoice Agent Orchestrator - End-to-End Flow Controller.

This module connects:
- LLM Router (intent classification)
- Tools (business actions)
- State Machine (transition validation)
- Event System (triggers)

Flow:
    Customer Message → LLMRouter → Intent Classification → Tool Execution
                                                              ↓
    Event Trigger ← State Transition ← FSM Validation ← Tool Result
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import uuid4

from llm_router import (
    LLMRouter,
    RouterDecision,
    RouterIntent,
    RouterTool,
    Confidence,
    get_default_provider,
)
from state_machine.invoice_state import InvoiceFSM, InvoiceState, TransitionError
from tools.base import InMemoryInvoiceStore, ToolResult
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
# Orchestration Result
# ============================================================================


@dataclass
class OrchestrationResult:
    """Result of processing a customer message."""

    success: bool
    message: str
    intent: Optional[RouterIntent] = None
    tool_used: Optional[str] = None
    previous_state: Optional[str] = None
    current_state: Optional[str] = None
    invoice_id: Optional[str] = None
    requires_clarification: bool = False
    clarification_prompt: Optional[str] = None
    events_fired: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_decision: Optional[RouterDecision] = None
    raw_tool_result: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "message": self.message,
            "intent": self.intent.value if self.intent else None,
            "tool_used": self.tool_used,
            "previous_state": self.previous_state,
            "current_state": self.current_state,
            "invoice_id": self.invoice_id,
            "requires_clarification": self.requires_clarification,
            "clarification_prompt": self.clarification_prompt,
            "events_fired": self.events_fired,
            "warnings": self.warnings,
        }


# ============================================================================
# Orchestrator
# ============================================================================


# Mapping from RouterTool to state machine triggers
TOOL_TO_TRIGGER: dict[RouterTool, str] = {
    RouterTool.APPROVE_INVOICE: "approve",
    RouterTool.REJECT_INVOICE: "reject",
    RouterTool.CONFIRM_PAYMENT: "confirm_payment",
    RouterTool.CREATE_DISPUTE: "dispute",
    RouterTool.RESOLVE_DISPUTE: "resolve_dispute",
    RouterTool.CLOSE_INVOICE: "close",
}

# Mapping from state transitions to event types
TRANSITION_EVENTS: dict[tuple[str, str], str] = {
    (InvoiceState.AWAITING_APPROVAL, InvoiceState.APPROVED): "invoice_approved",
    (InvoiceState.PAYMENT_PENDING, InvoiceState.PAID): "invoice_paid",
    (InvoiceState.PAID, InvoiceState.CLOSED): "invoice_closed",
    (InvoiceState.REJECTED, InvoiceState.CLOSED): "invoice_closed",
    (InvoiceState.APPROVED, InvoiceState.DISPUTED): "invoice_disputed",
    (InvoiceState.PAYMENT_PENDING, InvoiceState.DISPUTED): "invoice_disputed",
    (InvoiceState.PAID, InvoiceState.DISPUTED): "invoice_disputed",
}


class InvoiceOrchestrator:
    """
    Orchestrates the complete invoice processing flow.

    Responsibilities:
    1. Route incoming messages through LLMRouter
    2. Validate routing decisions against current state
    3. Execute appropriate tools
    4. Manage state transitions
    5. Fire events on state changes

    The orchestrator enforces:
    - No business action without valid state transition
    - State machine is source of truth
    - All errors are explicit and logged
    """

    def __init__(
        self,
        store: Optional[InMemoryInvoiceStore] = None,
        router: Optional[LLMRouter] = None,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            store: Invoice store. Creates new if not provided.
            router: LLM router. Creates with default provider if not provided.
            event_bus: Event bus for publishing events. Creates new if not provided.
        """
        self.store = store or InMemoryInvoiceStore()
        self.event_bus = event_bus or EventBus()

        # Initialize router with default provider if not provided
        if router is None:
            self.router = LLMRouter(llm_provider=get_default_provider())
        else:
            self.router = router

        # Initialize tools
        self._tools = {
            RouterTool.GET_INVOICE_STATUS: GetInvoiceStatusTool(self.store),
            RouterTool.APPROVE_INVOICE: ApproveInvoiceTool(self.store),
            RouterTool.REJECT_INVOICE: RejectInvoiceTool(self.store),
            RouterTool.CONFIRM_PAYMENT: ConfirmPaymentTool(self.store),
            RouterTool.RESEND_INVOICE: ResendInvoiceTool(self.store),
            RouterTool.CREATE_DISPUTE: CreateDisputeTool(self.store),
            RouterTool.RESOLVE_DISPUTE: ResolveDisputeTool(self.store),
            RouterTool.CLOSE_INVOICE: CloseInvoiceTool(self.store),
        }

    def create_invoice(
        self,
        invoice_id: str,
        customer_id: Optional[str] = None,
    ) -> InvoiceFSM:
        """Create a new invoice."""
        fsm = self.store.create_invoice(invoice_id)
        logger.info(f"Created invoice {invoice_id}")
        return fsm

    def get_invoice_state(self, invoice_id: str) -> Optional[str]:
        """Get current state of an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        return fsm.current_state if fsm else None

    def advance_invoice(
        self,
        invoice_id: str,
        trigger: str,
        customer_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Manually advance an invoice through a state transition.

        Used for administrative actions or testing.
        """
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            raise StateError(
                f"Invoice {invoice_id} not found",
                current_state="unknown",
                attempted_action=trigger,
                invoice_id=invoice_id,
            )

        previous_state = fsm.current_state

        try:
            result = fsm.trigger(trigger)
            self.store.save_fsm(fsm)
        except TransitionError as e:
            raise StateError(
                str(e),
                current_state=e.current_state,
                attempted_action=trigger,
                invoice_id=invoice_id,
            ) from e

        # Fire event if applicable
        self._maybe_fire_event(
            invoice_id=invoice_id,
            previous_state=previous_state,
            current_state=fsm.current_state,
            customer_id=customer_id,
        )

        return result

    def process_message(
        self,
        message: str,
        invoice_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> OrchestrationResult:
        """
        Process a customer message through the full pipeline.

        Flow:
        1. Get current state (if invoice_id provided)
        2. Route message through LLM
        3. Validate routing decision
        4. Execute tool (if actionable)
        5. Fire events (if state changed)

        Args:
            message: Customer message to process.
            invoice_id: Invoice ID if known.
            customer_id: Customer ID for event payloads.
            context: Additional context for routing.

        Returns:
            OrchestrationResult with full processing details.
        """
        context = context or {}
        warnings: list[str] = []
        events_fired: list[str] = []

        # Step 1: Get current state
        current_state = "unknown"
        if invoice_id:
            fsm = self.store.get_fsm(invoice_id)
            if fsm:
                current_state = fsm.current_state
            else:
                warnings.append(f"Invoice {invoice_id} not found")

        logger.info(f"Processing message for invoice={invoice_id}, state={current_state}")

        # Step 2: Route through LLM
        try:
            decision = self.router.route(
                message=message,
                state=current_state,
                context={
                    "invoice_id": invoice_id,
                    **context,
                },
            )
        except Exception as e:
            logger.error(f"Routing failed: {e}")
            return OrchestrationResult(
                success=False,
                message=f"Failed to process message: {str(e)}",
                warnings=[str(e)],
            )

        # Step 3: Check if clarification is needed
        if decision.requires_clarification:
            return OrchestrationResult(
                success=True,
                message="Clarification needed",
                intent=decision.intent,
                requires_clarification=True,
                clarification_prompt=decision.clarification_prompt,
                warnings=list(decision.warnings),
                raw_decision=decision,
            )

        # Step 4: Check if decision is actionable
        if not decision.is_actionable():
            return OrchestrationResult(
                success=True,
                message=decision.reasoning or "No action required",
                intent=decision.intent,
                current_state=current_state,
                invoice_id=invoice_id,
                warnings=list(decision.warnings),
                raw_decision=decision,
            )

        # Step 5: Get invoice ID from decision if not provided
        effective_invoice_id = invoice_id or decision.arguments.invoice_id
        if not effective_invoice_id and decision.tool != RouterTool.NONE:
            return OrchestrationResult(
                success=False,
                message="Invoice ID is required for this action",
                intent=decision.intent,
                requires_clarification=True,
                clarification_prompt="Which invoice would you like me to help with?",
                warnings=warnings,
                raw_decision=decision,
            )

        # Step 6: Execute tool
        if decision.tool == RouterTool.NONE:
            return OrchestrationResult(
                success=True,
                message=decision.reasoning or "No action needed",
                intent=decision.intent,
                current_state=current_state,
                invoice_id=effective_invoice_id,
                raw_decision=decision,
            )

        # Get FSM for state tracking
        fsm = self.store.get_fsm(effective_invoice_id) if effective_invoice_id else None
        previous_state = fsm.current_state if fsm else None

        # Execute the tool
        tool = self._tools.get(RouterTool(decision.tool))
        if not tool:
            return OrchestrationResult(
                success=False,
                message=f"Unknown tool: {decision.tool}",
                intent=decision.intent,
                invoice_id=effective_invoice_id,
                warnings=[f"Tool {decision.tool} not found"],
                raw_decision=decision,
            )

        try:
            tool_args = decision.arguments.model_dump(exclude_none=True)
            # Remove invoice_id from args since it's passed separately
            tool_args.pop("invoice_id", None)
            tool_result = tool.run(effective_invoice_id, **tool_args)
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            raise ToolError(
                str(e),
                tool_name=decision.tool,
                invoice_id=effective_invoice_id,
            ) from e

        # Step 7: Check tool result and fire events
        fsm = self.store.get_fsm(effective_invoice_id) if effective_invoice_id else None
        new_state = fsm.current_state if fsm else None

        if tool_result.get("success") and previous_state and new_state:
            event_type = self._maybe_fire_event(
                invoice_id=effective_invoice_id,
                previous_state=previous_state,
                current_state=new_state,
                customer_id=customer_id,
            )
            if event_type:
                events_fired.append(event_type)

        # Build response message
        if tool_result.get("success"):
            response_message = tool_result.get("message", f"Action completed: {decision.tool}")
        else:
            response_message = tool_result.get("message", "Action failed")

        return OrchestrationResult(
            success=tool_result.get("success", False),
            message=response_message,
            intent=decision.intent,
            tool_used=decision.tool,
            previous_state=previous_state,
            current_state=new_state,
            invoice_id=effective_invoice_id,
            events_fired=events_fired,
            warnings=warnings + list(decision.warnings),
            raw_decision=decision,
            raw_tool_result=tool_result,
        )

    def _maybe_fire_event(
        self,
        invoice_id: str,
        previous_state: str,
        current_state: str,
        customer_id: Optional[str] = None,
    ) -> Optional[str]:
        """Fire event if state transition warrants it."""
        event_type = TRANSITION_EVENTS.get((previous_state, current_state))

        if event_type:
            event = InvoiceEvent(
                event_type=event_type,
                invoice_id=invoice_id,
                customer_id=customer_id,
                timestamp=datetime.utcnow(),
                payload={
                    "previous_state": previous_state,
                    "current_state": current_state,
                },
            )
            self.event_bus.publish(event)
            return event_type

        return None

    def subscribe_to_events(self, subscriber: EventSubscriber) -> None:
        """Add an event subscriber."""
        self.event_bus.subscribe(subscriber)

    def get_event_history(self) -> list[InvoiceEvent]:
        """Get all fired events."""
        return self.event_bus.get_history()
