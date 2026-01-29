"""Base tool class for invoice operations."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, Protocol, TypeVar

from pydantic import BaseModel, Field

from state_machine.invoice_state import InvoiceFSM, TransitionError

logger = logging.getLogger(__name__)


class ToolResult(BaseModel):
    """Standardized tool result."""

    success: bool
    message: str
    data: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None

    def to_json(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        result = {
            "success": self.success,
            "message": self.message,
        }
        if self.data:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        return result


class InvoiceStore(Protocol):
    """Protocol for invoice storage."""

    def get_fsm(self, invoice_id: str) -> Optional[InvoiceFSM]:
        """Get state machine for invoice."""
        ...

    def save_fsm(self, fsm: InvoiceFSM) -> None:
        """Save state machine."""
        ...


class InMemoryInvoiceStore:
    """Simple in-memory invoice store for development/testing."""

    def __init__(self) -> None:
        self._invoices: dict[str, InvoiceFSM] = {}

    def get_fsm(self, invoice_id: str) -> Optional[InvoiceFSM]:
        """Get state machine for invoice."""
        return self._invoices.get(invoice_id)

    def save_fsm(self, fsm: InvoiceFSM) -> None:
        """Save state machine."""
        self._invoices[fsm.invoice_id] = fsm

    def create_invoice(self, invoice_id: str) -> InvoiceFSM:
        """Create a new invoice FSM."""
        fsm = InvoiceFSM(invoice_id=invoice_id)
        self.save_fsm(fsm)
        return fsm

    def list_invoices(self) -> list[str]:
        """List all invoice IDs."""
        return list(self._invoices.keys())


# Global store instance (can be replaced with dependency injection)
_default_store: Optional[InMemoryInvoiceStore] = None


def get_default_store() -> InMemoryInvoiceStore:
    """Get or create the default invoice store."""
    global _default_store
    if _default_store is None:
        _default_store = InMemoryInvoiceStore()
    return _default_store


def set_default_store(store: InMemoryInvoiceStore) -> None:
    """Set the default invoice store."""
    global _default_store
    _default_store = store


T = TypeVar("T", bound="BaseInvoiceTool")


class BaseInvoiceTool(ABC):
    """
    Base class for invoice-related tools.

    All tools must:
    1. Check current state before executing
    2. Enforce guard rails
    3. Return structured JSON
    4. Never mutate state illegally
    """

    name: str
    description: str

    def __init__(self, store: Optional[InvoiceStore] = None):
        """
        Initialize the tool.

        Args:
            store: Invoice store instance. Uses default if not provided.
        """
        self.store = store or get_default_store()

    def _get_fsm(self, invoice_id: str) -> Optional[InvoiceFSM]:
        """Get the state machine for an invoice."""
        return self.store.get_fsm(invoice_id)

    def _save_fsm(self, fsm: InvoiceFSM) -> None:
        """Save the state machine."""
        self.store.save_fsm(fsm)

    def _not_found_result(self, invoice_id: str) -> ToolResult:
        """Return a not found error result."""
        return ToolResult(
            success=False,
            message=f"Invoice '{invoice_id}' not found",
            error={
                "code": "INVOICE_NOT_FOUND",
                "invoice_id": invoice_id,
            },
        )

    def _transition_error_result(self, e: TransitionError) -> ToolResult:
        """Return a transition error result."""
        return ToolResult(
            success=False,
            message=str(e),
            error=e.to_dict(),
        )

    @abstractmethod
    def _execute(self, invoice_id: str, **kwargs: Any) -> ToolResult:
        """
        Execute the tool operation.

        Args:
            invoice_id: The invoice to operate on
            **kwargs: Additional arguments

        Returns:
            ToolResult with operation outcome
        """
        ...

    def run(self, invoice_id: str, **kwargs: Any) -> dict[str, Any]:
        """
        Run the tool and return JSON result.

        Args:
            invoice_id: The invoice to operate on
            **kwargs: Additional arguments

        Returns:
            JSON-serializable dictionary
        """
        logger.info(f"Tool '{self.name}' executing for invoice '{invoice_id}'")

        try:
            result = self._execute(invoice_id, **kwargs)
        except TransitionError as e:
            logger.warning(f"Tool '{self.name}' transition error: {e}")
            result = self._transition_error_result(e)
        except Exception as e:
            logger.exception(f"Tool '{self.name}' unexpected error: {e}")
            result = ToolResult(
                success=False,
                message=f"Unexpected error: {str(e)}",
                error={
                    "code": "INTERNAL_ERROR",
                    "message": str(e),
                },
            )

        return result.to_json()
