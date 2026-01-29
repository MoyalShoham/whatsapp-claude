"""Invoice automation agent implementation."""

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.invoice_agent.intent_classifier import ClassifiedIntent, IntentClassifier
from state_machine.invoice_state import InvoiceFSM, InvoiceState, TransitionError
from state_machine.models import Conversation, Intent
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


class AgentResponse(BaseModel):
    """Response from the invoice agent."""

    message: str
    intent: Optional[Intent] = None
    invoice_id: Optional[str] = None
    action_taken: Optional[str] = None
    tool_result: Optional[dict[str, Any]] = None
    current_state: Optional[str] = None
    available_actions: list[str] = Field(default_factory=list)


class InvoiceAgent:
    """
    Invoice automation agent that handles customer invoice-related messages.

    The agent:
    1. Classifies the intent of incoming messages
    2. Extracts relevant entities (invoice IDs, amounts, etc.)
    3. Executes appropriate tools based on intent and state
    4. Returns structured responses

    Supported intents:
    - invoice_question: Questions about an invoice
    - invoice_approval: Request to approve an invoice
    - invoice_rejection: Request to reject an invoice
    - payment_confirmation: Confirm payment
    - invoice_dispute: Dispute an invoice
    - request_invoice_copy: Request a copy of the invoice
    - general_question: Non-invoice related questions
    """

    def __init__(self, store: Optional[InMemoryInvoiceStore] = None) -> None:
        """
        Initialize the invoice agent.

        Args:
            store: Invoice store for state persistence. Creates new if not provided.
        """
        self.store = store or InMemoryInvoiceStore()
        self.classifier = IntentClassifier()

        # Initialize tools with the store
        self.tools = {
            "get_status": GetInvoiceStatusTool(self.store),
            "approve": ApproveInvoiceTool(self.store),
            "reject": RejectInvoiceTool(self.store),
            "confirm_payment": ConfirmPaymentTool(self.store),
            "resend": ResendInvoiceTool(self.store),
            "dispute": CreateDisputeTool(self.store),
            "resolve_dispute": ResolveDisputeTool(self.store),
            "close": CloseInvoiceTool(self.store),
        }

        # Map intents to tool actions
        self._intent_handlers = {
            Intent.INVOICE_QUESTION: self._handle_invoice_question,
            Intent.INVOICE_APPROVAL: self._handle_approval,
            Intent.INVOICE_REJECTION: self._handle_rejection,
            Intent.PAYMENT_CONFIRMATION: self._handle_payment_confirmation,
            Intent.INVOICE_DISPUTE: self._handle_dispute,
            Intent.REQUEST_INVOICE_COPY: self._handle_resend_request,
            Intent.GENERAL_QUESTION: self._handle_general_question,
        }

    def create_invoice(self, invoice_id: str) -> InvoiceFSM:
        """Create a new invoice and return its state machine."""
        return self.store.create_invoice(invoice_id)

    def get_invoice_state(self, invoice_id: str) -> Optional[dict[str, Any]]:
        """Get the current state of an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if fsm:
            return fsm.to_dict()
        return None

    def process_message(
        self,
        message: str,
        invoice_id: Optional[str] = None,
        conversation: Optional[Conversation] = None,
        **context: Any,
    ) -> AgentResponse:
        """
        Process a user message and return a response.

        Args:
            message: The user's message
            invoice_id: Optional invoice ID (will extract from message if not provided)
            conversation: Optional conversation context
            **context: Additional context (reason, payment_reference, etc.)

        Returns:
            AgentResponse with the result
        """
        # Classify the intent
        classification = self.classifier.classify(message)

        # Use provided invoice_id or extracted one
        effective_invoice_id = invoice_id or classification.invoice_id

        logger.info(
            f"Processing message: intent={classification.intent}, "
            f"confidence={classification.confidence:.2f}, "
            f"invoice_id={effective_invoice_id}"
        )

        # Get the appropriate handler
        handler = self._intent_handlers.get(
            classification.intent,
            self._handle_general_question,
        )

        # Execute the handler
        return handler(
            message=message,
            classification=classification,
            invoice_id=effective_invoice_id,
            **context,
        )

    def _handle_invoice_question(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle invoice questions."""
        if not invoice_id:
            return AgentResponse(
                message="I'd be happy to help with your invoice question. "
                "Could you please provide the invoice number?",
                intent=classification.intent,
            )

        result = self.tools["get_status"].run(invoice_id)

        if not result["success"]:
            return AgentResponse(
                message=f"I couldn't find invoice {invoice_id}. "
                "Please verify the invoice number and try again.",
                intent=classification.intent,
                invoice_id=invoice_id,
                tool_result=result,
            )

        data = result["data"]
        state = data["current_state"]
        actions = data["available_actions"]

        return AgentResponse(
            message=f"Invoice {invoice_id} is currently in '{state}' status. "
            f"Available actions: {', '.join(actions) if actions else 'none'}.",
            intent=classification.intent,
            invoice_id=invoice_id,
            action_taken="get_status",
            tool_result=result,
            current_state=state,
            available_actions=actions,
        )

    def _handle_approval(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle invoice approval requests."""
        if not invoice_id:
            return AgentResponse(
                message="I can help approve an invoice. "
                "Which invoice would you like to approve?",
                intent=classification.intent,
            )

        # Check if invoice exists
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return AgentResponse(
                message=f"Invoice {invoice_id} was not found. "
                "Please verify the invoice number.",
                intent=classification.intent,
                invoice_id=invoice_id,
            )

        # Check current state
        if fsm.current_state != InvoiceState.AWAITING_APPROVAL:
            return AgentResponse(
                message=f"Invoice {invoice_id} cannot be approved right now. "
                f"It's currently in '{fsm.current_state}' status. "
                f"Approval is only possible when the invoice is awaiting approval.",
                intent=classification.intent,
                invoice_id=invoice_id,
                current_state=fsm.current_state,
                available_actions=fsm.get_available_triggers(),
            )

        # Execute approval
        result = self.tools["approve"].run(
            invoice_id,
            approver_id=context.get("approver_id"),
            reason=context.get("reason"),
        )

        if result["success"]:
            return AgentResponse(
                message=f"Invoice {invoice_id} has been approved successfully. "
                "The next step is to request payment.",
                intent=classification.intent,
                invoice_id=invoice_id,
                action_taken="approve",
                tool_result=result,
                current_state=result["data"]["current_state"],
                available_actions=["request_payment", "dispute"],
            )
        else:
            return AgentResponse(
                message=f"Could not approve invoice {invoice_id}: {result['message']}",
                intent=classification.intent,
                invoice_id=invoice_id,
                tool_result=result,
            )

    def _handle_rejection(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle invoice rejection requests."""
        if not invoice_id:
            return AgentResponse(
                message="I can help reject an invoice. "
                "Which invoice would you like to reject, and what is the reason?",
                intent=classification.intent,
            )

        reason = context.get("reason", "")

        # Check if invoice exists
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return AgentResponse(
                message=f"Invoice {invoice_id} was not found. "
                "Please verify the invoice number.",
                intent=classification.intent,
                invoice_id=invoice_id,
            )

        # Check current state
        if fsm.current_state != InvoiceState.AWAITING_APPROVAL:
            return AgentResponse(
                message=f"Invoice {invoice_id} cannot be rejected right now. "
                f"It's currently in '{fsm.current_state}' status.",
                intent=classification.intent,
                invoice_id=invoice_id,
                current_state=fsm.current_state,
            )

        # Require a reason
        if not reason:
            return AgentResponse(
                message=f"To reject invoice {invoice_id}, please provide a reason.",
                intent=classification.intent,
                invoice_id=invoice_id,
            )

        result = self.tools["reject"].run(invoice_id, reason=reason)

        if result["success"]:
            return AgentResponse(
                message=f"Invoice {invoice_id} has been rejected. Reason: {reason}",
                intent=classification.intent,
                invoice_id=invoice_id,
                action_taken="reject",
                tool_result=result,
                current_state=result["data"]["current_state"],
                available_actions=["close"],
            )
        else:
            return AgentResponse(
                message=f"Could not reject invoice {invoice_id}: {result['message']}",
                intent=classification.intent,
                invoice_id=invoice_id,
                tool_result=result,
            )

    def _handle_payment_confirmation(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle payment confirmation."""
        if not invoice_id:
            return AgentResponse(
                message="Thank you for confirming payment. "
                "Which invoice was this payment for?",
                intent=classification.intent,
            )

        # Check if invoice exists
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return AgentResponse(
                message=f"Invoice {invoice_id} was not found. "
                "Please verify the invoice number.",
                intent=classification.intent,
                invoice_id=invoice_id,
            )

        # Check current state
        if fsm.current_state != InvoiceState.PAYMENT_PENDING:
            state_messages = {
                InvoiceState.AWAITING_APPROVAL: "The invoice needs to be approved first.",
                InvoiceState.APPROVED: "Payment hasn't been requested yet.",
                InvoiceState.PAID: "Payment has already been confirmed.",
                InvoiceState.CLOSED: "This invoice is already closed.",
            }
            hint = state_messages.get(
                fsm.current_state,
                f"Current status is '{fsm.current_state}'.",
            )

            return AgentResponse(
                message=f"Cannot confirm payment for invoice {invoice_id}. {hint}",
                intent=classification.intent,
                invoice_id=invoice_id,
                current_state=fsm.current_state,
            )

        result = self.tools["confirm_payment"].run(
            invoice_id,
            payment_reference=context.get("payment_reference"),
            payment_method=context.get("payment_method"),
        )

        if result["success"]:
            return AgentResponse(
                message=f"Payment confirmed for invoice {invoice_id}. Thank you!",
                intent=classification.intent,
                invoice_id=invoice_id,
                action_taken="confirm_payment",
                tool_result=result,
                current_state=result["data"]["current_state"],
                available_actions=["close", "dispute"],
            )
        else:
            return AgentResponse(
                message=f"Could not confirm payment: {result['message']}",
                intent=classification.intent,
                invoice_id=invoice_id,
                tool_result=result,
            )

    def _handle_dispute(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle invoice dispute."""
        if not invoice_id:
            return AgentResponse(
                message="I understand you have a concern. "
                "Which invoice would you like to dispute?",
                intent=classification.intent,
            )

        reason = context.get("reason", "")

        # Check if invoice exists
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return AgentResponse(
                message=f"Invoice {invoice_id} was not found. "
                "Please verify the invoice number.",
                intent=classification.intent,
                invoice_id=invoice_id,
            )

        # Check if dispute is possible
        if not fsm.can_trigger("dispute"):
            return AgentResponse(
                message=f"Invoice {invoice_id} cannot be disputed in its current state "
                f"('{fsm.current_state}'). Disputes can only be created after approval.",
                intent=classification.intent,
                invoice_id=invoice_id,
                current_state=fsm.current_state,
            )

        if not reason:
            return AgentResponse(
                message=f"To create a dispute for invoice {invoice_id}, "
                "please describe the issue.",
                intent=classification.intent,
                invoice_id=invoice_id,
            )

        result = self.tools["dispute"].run(invoice_id, reason=reason)

        if result["success"]:
            return AgentResponse(
                message=f"A dispute has been created for invoice {invoice_id}. "
                "We will review and get back to you shortly.",
                intent=classification.intent,
                invoice_id=invoice_id,
                action_taken="dispute",
                tool_result=result,
                current_state=result["data"]["current_state"],
                available_actions=["resolve_dispute"],
            )
        else:
            return AgentResponse(
                message=f"Could not create dispute: {result['message']}",
                intent=classification.intent,
                invoice_id=invoice_id,
                tool_result=result,
            )

    def _handle_resend_request(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle invoice resend requests."""
        if not invoice_id:
            return AgentResponse(
                message="I can resend an invoice to you. "
                "Which invoice would you like me to send?",
                intent=classification.intent,
            )

        result = self.tools["resend"].run(invoice_id)

        if result["success"]:
            return AgentResponse(
                message=f"Invoice {invoice_id} has been resent to you. "
                "Please check your email.",
                intent=classification.intent,
                invoice_id=invoice_id,
                action_taken="resend",
                tool_result=result,
            )
        else:
            return AgentResponse(
                message=f"Could not resend invoice: {result['message']}",
                intent=classification.intent,
                invoice_id=invoice_id,
                tool_result=result,
            )

    def _handle_general_question(
        self,
        message: str,
        classification: ClassifiedIntent,
        invoice_id: Optional[str],
        **context: Any,
    ) -> AgentResponse:
        """Handle general questions."""
        return AgentResponse(
            message="I'm here to help with invoice-related questions. "
            "You can ask me about invoice status, approve or reject invoices, "
            "confirm payments, or request invoice copies. "
            "How can I assist you today?",
            intent=classification.intent,
            invoice_id=invoice_id,
        )

    def advance_state(
        self,
        invoice_id: str,
        trigger: str,
        **context: Any,
    ) -> dict[str, Any]:
        """
        Manually advance an invoice state (for testing or admin use).

        Args:
            invoice_id: The invoice to advance
            trigger: The state transition trigger
            **context: Additional context

        Returns:
            Result dictionary
        """
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return {"success": False, "error": f"Invoice {invoice_id} not found"}

        try:
            result = fsm.trigger(trigger)
            self.store.save_fsm(fsm)
            return result
        except TransitionError as e:
            return {"success": False, "error": str(e), "details": e.to_dict()}
