"""Invoice operation tools for LangChain/Claude integration."""

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from state_machine.invoice_state import InvoiceFSM, InvoiceState, TransitionError
from tools.base import BaseInvoiceTool, InvoiceStore, ToolResult

logger = logging.getLogger(__name__)


# ============================================================================
# Tool Input Schemas (for LangChain integration)
# ============================================================================


class InvoiceIdInput(BaseModel):
    """Input schema for tools that only need invoice_id."""

    invoice_id: str = Field(..., description="The invoice identifier")


class ApprovalInput(BaseModel):
    """Input schema for approval tool."""

    invoice_id: str = Field(..., description="The invoice identifier")
    approver_id: Optional[str] = Field(None, description="ID of the approver")
    reason: Optional[str] = Field(None, description="Reason for approval")


class RejectionInput(BaseModel):
    """Input schema for rejection tool."""

    invoice_id: str = Field(..., description="The invoice identifier")
    reason: str = Field(..., description="Reason for rejection")


class PaymentInput(BaseModel):
    """Input schema for payment confirmation tool."""

    invoice_id: str = Field(..., description="The invoice identifier")
    payment_reference: Optional[str] = Field(None, description="Payment reference number")
    payment_method: Optional[str] = Field(None, description="Payment method used")


class DisputeInput(BaseModel):
    """Input schema for dispute creation tool."""

    invoice_id: str = Field(..., description="The invoice identifier")
    reason: str = Field(..., description="Reason for the dispute")


class ResolveDisputeInput(BaseModel):
    """Input schema for dispute resolution tool."""

    invoice_id: str = Field(..., description="The invoice identifier")
    resolution: str = Field(..., description="Resolution details")


# ============================================================================
# Invoice Tools
# ============================================================================


class GetInvoiceStatusTool(BaseInvoiceTool):
    """Tool to get the current status of an invoice."""

    name = "get_invoice_status"
    description = (
        "Get the current status and available actions for an invoice. "
        "Use this to check what state an invoice is in before taking action."
    )

    def _execute(self, invoice_id: str, **kwargs: Any) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        return ToolResult(
            success=True,
            message=f"Invoice '{invoice_id}' is in state '{fsm.current_state}'",
            data={
                "invoice_id": invoice_id,
                "current_state": fsm.current_state,
                "is_terminal": fsm.is_terminal,
                "available_actions": fsm.get_available_triggers(),
                "history": fsm.history,
            },
        )


class ApproveInvoiceTool(BaseInvoiceTool):
    """Tool to approve an invoice."""

    name = "approve_invoice"
    description = (
        "Approve an invoice that is awaiting approval. "
        "Can only be used when the invoice is in 'awaiting_approval' state."
    )

    def _execute(
        self,
        invoice_id: str,
        approver_id: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        # Check if we can approve
        if fsm.current_state != InvoiceState.AWAITING_APPROVAL:
            return ToolResult(
                success=False,
                message=f"Cannot approve invoice in state '{fsm.current_state}'. "
                f"Invoice must be in 'awaiting_approval' state.",
                error={
                    "code": "INVALID_STATE",
                    "current_state": fsm.current_state,
                    "required_state": InvoiceState.AWAITING_APPROVAL,
                },
            )

        # Execute the transition
        result = fsm.trigger("approve")
        self._save_fsm(fsm)

        logger.info(f"Invoice '{invoice_id}' approved by '{approver_id}'")

        return ToolResult(
            success=True,
            message=f"Invoice '{invoice_id}' has been approved",
            data={
                **result,
                "approver_id": approver_id,
                "reason": reason,
            },
        )


class RejectInvoiceTool(BaseInvoiceTool):
    """Tool to reject an invoice."""

    name = "reject_invoice"
    description = (
        "Reject an invoice that is awaiting approval. "
        "Requires a reason for rejection. "
        "Can only be used when the invoice is in 'awaiting_approval' state."
    )

    def _execute(
        self,
        invoice_id: str,
        reason: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        if not reason:
            return ToolResult(
                success=False,
                message="A reason is required for rejection",
                error={
                    "code": "MISSING_REASON",
                    "field": "reason",
                },
            )

        # Check if we can reject
        if fsm.current_state != InvoiceState.AWAITING_APPROVAL:
            return ToolResult(
                success=False,
                message=f"Cannot reject invoice in state '{fsm.current_state}'. "
                f"Invoice must be in 'awaiting_approval' state.",
                error={
                    "code": "INVALID_STATE",
                    "current_state": fsm.current_state,
                    "required_state": InvoiceState.AWAITING_APPROVAL,
                },
            )

        # Execute the transition
        result = fsm.trigger("reject")
        self._save_fsm(fsm)

        logger.info(f"Invoice '{invoice_id}' rejected. Reason: {reason}")

        return ToolResult(
            success=True,
            message=f"Invoice '{invoice_id}' has been rejected",
            data={
                **result,
                "reason": reason,
            },
        )


class ConfirmPaymentTool(BaseInvoiceTool):
    """Tool to confirm payment for an invoice."""

    name = "confirm_payment"
    description = (
        "Confirm that payment has been received for an invoice. "
        "Can only be used when the invoice is in 'payment_pending' state."
    )

    def _execute(
        self,
        invoice_id: str,
        payment_reference: Optional[str] = None,
        payment_method: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        # Check state - must be payment_pending
        if fsm.current_state != InvoiceState.PAYMENT_PENDING:
            # Provide helpful message based on current state
            if fsm.current_state == InvoiceState.AWAITING_APPROVAL:
                hint = "The invoice must be approved first."
            elif fsm.current_state == InvoiceState.APPROVED:
                hint = "Payment must be requested first."
            elif fsm.current_state == InvoiceState.PAID:
                hint = "Payment has already been confirmed."
            else:
                hint = f"Current state is '{fsm.current_state}'."

            return ToolResult(
                success=False,
                message=f"Cannot confirm payment. {hint}",
                error={
                    "code": "INVALID_STATE",
                    "current_state": fsm.current_state,
                    "required_state": InvoiceState.PAYMENT_PENDING,
                },
            )

        # Execute the transition
        result = fsm.trigger("confirm_payment")
        self._save_fsm(fsm)

        logger.info(f"Payment confirmed for invoice '{invoice_id}'")

        return ToolResult(
            success=True,
            message=f"Payment confirmed for invoice '{invoice_id}'",
            data={
                **result,
                "payment_reference": payment_reference,
                "payment_method": payment_method,
            },
        )


class ResendInvoiceTool(BaseInvoiceTool):
    """Tool to resend an invoice to the customer."""

    name = "resend_invoice"
    description = (
        "Resend an invoice to the customer. "
        "Can be used from most non-terminal states."
    )

    # States from which resend is allowed
    RESENDABLE_STATES = [
        InvoiceState.INVOICE_SENT,
        InvoiceState.AWAITING_APPROVAL,
        InvoiceState.APPROVED,
        InvoiceState.PAYMENT_PENDING,
    ]

    def _execute(self, invoice_id: str, **kwargs: Any) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        # Check if resend is allowed from current state
        if fsm.current_state not in self.RESENDABLE_STATES:
            return ToolResult(
                success=False,
                message=f"Cannot resend invoice in state '{fsm.current_state}'",
                error={
                    "code": "INVALID_STATE",
                    "current_state": fsm.current_state,
                    "allowed_states": self.RESENDABLE_STATES,
                },
            )

        # Note: Resending doesn't change state, it's an action
        logger.info(f"Invoice '{invoice_id}' resent to customer")

        return ToolResult(
            success=True,
            message=f"Invoice '{invoice_id}' has been resent to the customer",
            data={
                "invoice_id": invoice_id,
                "current_state": fsm.current_state,
                "action": "resend",
            },
        )


class CreateDisputeTool(BaseInvoiceTool):
    """Tool to create a dispute for an invoice."""

    name = "create_dispute"
    description = (
        "Create a dispute for an invoice. "
        "Can be used after approval, during payment pending, or after payment. "
        "This will halt the normal invoice flow until the dispute is resolved."
    )

    def _execute(
        self,
        invoice_id: str,
        reason: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        if not reason:
            return ToolResult(
                success=False,
                message="A reason is required to create a dispute",
                error={
                    "code": "MISSING_REASON",
                    "field": "reason",
                },
            )

        # Check if dispute is possible
        if not fsm.can_trigger("dispute"):
            return ToolResult(
                success=False,
                message=f"Cannot create dispute from state '{fsm.current_state}'. "
                f"Disputes can only be created after approval.",
                error={
                    "code": "INVALID_STATE",
                    "current_state": fsm.current_state,
                    "available_actions": fsm.get_available_triggers(),
                },
            )

        # Execute the transition
        result = fsm.trigger("dispute")
        self._save_fsm(fsm)

        logger.info(f"Dispute created for invoice '{invoice_id}'. Reason: {reason}")

        return ToolResult(
            success=True,
            message=f"Dispute created for invoice '{invoice_id}'",
            data={
                **result,
                "reason": reason,
            },
        )


class ResolveDisputeTool(BaseInvoiceTool):
    """Tool to resolve a dispute and reopen the invoice flow."""

    name = "resolve_dispute"
    description = (
        "Resolve a dispute for an invoice, returning it to the approval process. "
        "Can only be used when the invoice is in 'disputed' state."
    )

    def _execute(
        self,
        invoice_id: str,
        resolution: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        if not resolution:
            return ToolResult(
                success=False,
                message="A resolution is required to resolve the dispute",
                error={
                    "code": "MISSING_RESOLUTION",
                    "field": "resolution",
                },
            )

        # Check if we can resolve
        if fsm.current_state != InvoiceState.DISPUTED:
            return ToolResult(
                success=False,
                message=f"Cannot resolve dispute. Invoice is not disputed. "
                f"Current state: '{fsm.current_state}'",
                error={
                    "code": "NOT_DISPUTED",
                    "current_state": fsm.current_state,
                },
            )

        # Execute the transition
        result = fsm.trigger("resolve_dispute")
        self._save_fsm(fsm)

        logger.info(f"Dispute resolved for invoice '{invoice_id}'. Resolution: {resolution}")

        return ToolResult(
            success=True,
            message=f"Dispute resolved. Invoice '{invoice_id}' returned to approval process.",
            data={
                **result,
                "resolution": resolution,
            },
        )


class CloseInvoiceTool(BaseInvoiceTool):
    """Tool to close an invoice (terminal state)."""

    name = "close_invoice"
    description = (
        "Close an invoice, marking it as complete. "
        "Can only be used when the invoice is in 'paid' or 'rejected' state. "
        "This is a terminal action and cannot be undone."
    )

    def _execute(self, invoice_id: str, **kwargs: Any) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        # Check if we can close
        if not fsm.can_trigger("close"):
            allowed_states = [InvoiceState.PAID, InvoiceState.REJECTED]
            return ToolResult(
                success=False,
                message=f"Cannot close invoice in state '{fsm.current_state}'. "
                f"Invoice must be 'paid' or 'rejected' to close.",
                error={
                    "code": "INVALID_STATE",
                    "current_state": fsm.current_state,
                    "allowed_states": allowed_states,
                },
            )

        # Execute the transition
        result = fsm.trigger("close")
        self._save_fsm(fsm)

        logger.info(f"Invoice '{invoice_id}' closed")

        return ToolResult(
            success=True,
            message=f"Invoice '{invoice_id}' has been closed",
            data=result,
        )


# ============================================================================
# Tool Registry
# ============================================================================


def get_all_tools(store: Optional[InvoiceStore] = None) -> list[BaseInvoiceTool]:
    """Get all invoice tools with the given store."""
    return [
        GetInvoiceStatusTool(store),
        ApproveInvoiceTool(store),
        RejectInvoiceTool(store),
        ConfirmPaymentTool(store),
        ResendInvoiceTool(store),
        CreateDisputeTool(store),
        ResolveDisputeTool(store),
        CloseInvoiceTool(store),
    ]
