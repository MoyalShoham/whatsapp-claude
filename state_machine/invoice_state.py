"""Invoice state machine implementation using the transitions library."""

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from transitions import Machine, MachineError

from state_machine.models import InvoiceStatus

logger = logging.getLogger(__name__)


class InvoiceState(str):
    """Invoice state constants matching InvoiceStatus enum."""

    NEW = "new"
    INVOICE_SENT = "invoice_sent"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    DISPUTED = "disputed"
    CLOSED = "closed"

    @classmethod
    def all_states(cls) -> list[str]:
        """Return all valid states."""
        return [
            cls.NEW,
            cls.INVOICE_SENT,
            cls.AWAITING_APPROVAL,
            cls.APPROVED,
            cls.REJECTED,
            cls.PAYMENT_PENDING,
            cls.PAID,
            cls.DISPUTED,
            cls.CLOSED,
        ]

    @classmethod
    def terminal_states(cls) -> list[str]:
        """Return terminal (closed) states."""
        return [cls.CLOSED]

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        """Check if a state is terminal."""
        return state in cls.terminal_states()


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        message: str,
        current_state: str,
        attempted_trigger: str,
        invoice_id: Optional[str] = None,
    ):
        self.current_state = current_state
        self.attempted_trigger = attempted_trigger
        self.invoice_id = invoice_id
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for JSON serialization."""
        return {
            "error": "TransitionError",
            "message": str(self),
            "current_state": self.current_state,
            "attempted_trigger": self.attempted_trigger,
            "invoice_id": self.invoice_id,
        }


class InvoiceFSM:
    """
    Finite State Machine for invoice lifecycle management.

    States:
        - new: Initial state when invoice is created
        - invoice_sent: Invoice has been sent to customer
        - awaiting_approval: Waiting for customer approval
        - approved: Invoice has been approved
        - rejected: Invoice has been rejected
        - payment_pending: Waiting for payment
        - paid: Payment received
        - disputed: Invoice is under dispute
        - closed: Terminal state

    Transitions:
        - send_invoice: new -> invoice_sent
        - request_approval: invoice_sent -> awaiting_approval
        - approve: awaiting_approval -> approved
        - reject: awaiting_approval -> rejected
        - request_payment: approved -> payment_pending
        - confirm_payment: payment_pending -> paid
        - close: paid -> closed, rejected -> closed
        - dispute: approved/payment_pending/paid -> disputed
        - resolve_dispute: disputed -> awaiting_approval (reopens flow)
    """

    # Define all valid transitions
    TRANSITIONS = [
        # Normal flow
        {
            "trigger": "send_invoice",
            "source": InvoiceState.NEW,
            "dest": InvoiceState.INVOICE_SENT,
        },
        {
            "trigger": "request_approval",
            "source": InvoiceState.INVOICE_SENT,
            "dest": InvoiceState.AWAITING_APPROVAL,
        },
        {
            "trigger": "approve",
            "source": InvoiceState.AWAITING_APPROVAL,
            "dest": InvoiceState.APPROVED,
        },
        {
            "trigger": "reject",
            "source": InvoiceState.AWAITING_APPROVAL,
            "dest": InvoiceState.REJECTED,
        },
        {
            "trigger": "request_payment",
            "source": InvoiceState.APPROVED,
            "dest": InvoiceState.PAYMENT_PENDING,
        },
        {
            "trigger": "confirm_payment",
            "source": InvoiceState.PAYMENT_PENDING,
            "dest": InvoiceState.PAID,
        },
        # Closing
        {
            "trigger": "close",
            "source": InvoiceState.PAID,
            "dest": InvoiceState.CLOSED,
        },
        {
            "trigger": "close",
            "source": InvoiceState.REJECTED,
            "dest": InvoiceState.CLOSED,
        },
        # Dispute flow - can dispute from multiple states
        {
            "trigger": "dispute",
            "source": InvoiceState.APPROVED,
            "dest": InvoiceState.DISPUTED,
        },
        {
            "trigger": "dispute",
            "source": InvoiceState.PAYMENT_PENDING,
            "dest": InvoiceState.DISPUTED,
        },
        {
            "trigger": "dispute",
            "source": InvoiceState.PAID,
            "dest": InvoiceState.DISPUTED,
        },
        # Resolve dispute - reopens the approval flow
        {
            "trigger": "resolve_dispute",
            "source": InvoiceState.DISPUTED,
            "dest": InvoiceState.AWAITING_APPROVAL,
        },
    ]

    def __init__(
        self,
        invoice_id: str,
        initial_state: str = InvoiceState.NEW,
        on_transition: Optional[Callable[[str, str, str], None]] = None,
    ):
        """
        Initialize the invoice state machine.

        Args:
            invoice_id: Unique identifier for the invoice
            initial_state: Starting state (default: new)
            on_transition: Optional callback called on each transition
                          with (invoice_id, source_state, dest_state)
        """
        self.invoice_id = invoice_id
        self._on_transition = on_transition
        self._history: list[dict[str, Any]] = []

        # Validate initial state
        if initial_state not in InvoiceState.all_states():
            raise ValueError(f"Invalid initial state: {initial_state}")

        # Initialize the machine
        self.machine = Machine(
            model=self,
            states=InvoiceState.all_states(),
            transitions=self.TRANSITIONS,
            initial=initial_state,
            auto_transitions=False,  # Disable automatic transitions
            send_event=True,  # Pass event data to callbacks
            before_state_change=self._before_transition,
            after_state_change=self._after_transition,
        )

        # Record initial state
        self._record_history(None, initial_state, "initialized")

    @property
    def current_state(self) -> str:
        """Get the current state."""
        return self.state  # type: ignore[return-value]

    @property
    def is_terminal(self) -> bool:
        """Check if current state is terminal."""
        return InvoiceState.is_terminal(self.current_state)

    @property
    def history(self) -> list[dict[str, Any]]:
        """Get transition history."""
        return self._history.copy()

    def _before_transition(self, event: Any) -> None:
        """Called before each transition."""
        logger.debug(
            f"Invoice {self.invoice_id}: Attempting transition "
            f"'{event.event.name}' from '{self.state}'"
        )

    def _after_transition(self, event: Any) -> None:
        """Called after each successful transition."""
        source = event.transition.source
        dest = event.transition.dest
        trigger = event.event.name

        self._record_history(source, dest, trigger)

        logger.info(
            f"Invoice {self.invoice_id}: Transition '{trigger}' "
            f"completed: {source} -> {dest}"
        )

        if self._on_transition:
            self._on_transition(self.invoice_id, source, dest)

    def _record_history(
        self, source: Optional[str], dest: str, trigger: str
    ) -> None:
        """Record a transition in history."""
        self._history.append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "source": source,
                "dest": dest,
                "trigger": trigger,
            }
        )

    def can_trigger(self, trigger: str) -> bool:
        """Check if a trigger can be executed from current state."""
        # Use the machine's may_<trigger> methods
        may_method = getattr(self, f"may_{trigger}", None)
        if may_method:
            return may_method()
        return False

    def get_available_triggers(self) -> list[str]:
        """Get list of triggers available from current state."""
        available = []
        for transition in self.TRANSITIONS:
            trigger = transition["trigger"]
            source = transition["source"]
            if source == self.current_state and trigger not in available:
                available.append(trigger)
        return available

    def trigger(self, trigger_name: str, **kwargs: Any) -> dict[str, Any]:
        """
        Execute a state transition.

        Args:
            trigger_name: Name of the trigger to execute
            **kwargs: Additional arguments passed to transition callbacks

        Returns:
            Dictionary with transition result

        Raises:
            TransitionError: If the transition is not valid from current state
        """
        if self.is_terminal:
            raise TransitionError(
                f"Cannot transition from terminal state '{self.current_state}'",
                current_state=self.current_state,
                attempted_trigger=trigger_name,
                invoice_id=self.invoice_id,
            )

        if not self.can_trigger(trigger_name):
            available = self.get_available_triggers()
            raise TransitionError(
                f"Cannot execute '{trigger_name}' from state '{self.current_state}'. "
                f"Available triggers: {available}",
                current_state=self.current_state,
                attempted_trigger=trigger_name,
                invoice_id=self.invoice_id,
            )

        previous_state = self.current_state

        try:
            # Get the trigger method and execute it
            trigger_method = getattr(self, trigger_name)
            trigger_method(**kwargs)
        except MachineError as e:
            raise TransitionError(
                str(e),
                current_state=previous_state,
                attempted_trigger=trigger_name,
                invoice_id=self.invoice_id,
            ) from e

        return {
            "success": True,
            "invoice_id": self.invoice_id,
            "previous_state": previous_state,
            "current_state": self.current_state,
            "trigger": trigger_name,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize state machine to dictionary."""
        return {
            "invoice_id": self.invoice_id,
            "current_state": self.current_state,
            "is_terminal": self.is_terminal,
            "available_triggers": self.get_available_triggers(),
            "history": self.history,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        on_transition: Optional[Callable[[str, str, str], None]] = None,
    ) -> "InvoiceFSM":
        """
        Restore state machine from dictionary.

        Args:
            data: Dictionary containing invoice_id and current_state
            on_transition: Optional transition callback

        Returns:
            Restored InvoiceFSM instance
        """
        return cls(
            invoice_id=data["invoice_id"],
            initial_state=data["current_state"],
            on_transition=on_transition,
        )

    def __repr__(self) -> str:
        return f"InvoiceFSM(invoice_id={self.invoice_id!r}, state={self.current_state!r})"
