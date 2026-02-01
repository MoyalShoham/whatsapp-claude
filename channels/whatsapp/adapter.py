"""
WhatsApp Adapter - Bridge between WhatsApp messages and Invoice Agent.

This adapter:
1. Receives incoming WhatsApp messages
2. Routes them through the Invoice Orchestrator
3. Returns formatted responses for WhatsApp
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from agents.invoice_agent import InvoiceOrchestrator, ToolExecutionResult

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppMessage:
    """Represents an incoming WhatsApp message."""

    phone: str
    text: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    message_id: Optional[str] = None


@dataclass
class WhatsAppResponse:
    """Represents an outgoing WhatsApp response."""

    text: str
    phone: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    original_message_id: Optional[str] = None


class WhatsAppAdapter:
    """
    Adapter between WhatsApp channel and Invoice Agent.

    Handles:
    - Message formatting
    - Conversation context tracking
    - Response generation
    """

    def __init__(
        self,
        orchestrator: Optional[InvoiceOrchestrator] = None,
        max_history: int = 10,
    ):
        """
        Initialize the WhatsApp adapter.

        Args:
            orchestrator: Invoice orchestrator instance. Creates new if not provided.
            max_history: Maximum messages to keep in conversation history per user.
        """
        self.orchestrator = orchestrator or InvoiceOrchestrator()
        self.max_history = max_history

        # Track conversation history per phone number
        self._conversations: dict[str, list[dict[str, Any]]] = {}

        # Track active invoice context per phone number
        self._active_invoice: dict[str, Optional[str]] = {}

    def handle_incoming(
        self,
        phone: str,
        text: str,
        invoice_id: Optional[str] = None,
    ) -> str:
        """
        Handle an incoming WhatsApp message.

        Args:
            phone: Sender's phone number.
            text: Message text.
            invoice_id: Optional invoice ID context.

        Returns:
            Response text for WhatsApp.
        """
        logger.info(f"WhatsApp message from {phone}: {text[:50]}...")

        # Add to conversation history
        self._add_to_history(phone, "user", text)

        # Get active invoice context if not provided
        # Don't use cached invoice_id for list requests
        list_keywords = ["all invoices", "my invoices", "list invoices", "show invoices", "active invoices", "pending invoices"]
        is_list_request = any(kw in text.lower() for kw in list_keywords)
        effective_invoice_id = None if is_list_request else (invoice_id or self._active_invoice.get(phone))

        # Build context with conversation history
        context = {
            "channel": "whatsapp",
            "phone": phone,
            "conversation_history": self._get_history(phone),
        }

        # Process through orchestrator
        try:
            result = self.orchestrator.process_message(
                message=text,
                invoice_id=effective_invoice_id,
                customer_id=phone,  # Use phone as customer ID
                context=context,
            )
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            response = "Sorry, an error occurred while processing your message. Please try again."
            self._add_to_history(phone, "assistant", response)
            return response

        # Update active invoice context
        if result.invoice_id:
            self._active_invoice[phone] = result.invoice_id

        # Format response
        response = self._format_response(result)

        # Add response to history
        self._add_to_history(phone, "assistant", response)

        return response

    def handle_message(
        self,
        channel: str,
        sender: str,
        message: str,
        **kwargs: Any,
    ) -> str:
        """
        Generic message handler interface.

        This method provides compatibility with the InvoiceAgent interface.

        Args:
            channel: Channel name (should be "whatsapp").
            sender: Sender identifier (phone number).
            message: Message text.
            **kwargs: Additional arguments.

        Returns:
            Response text.
        """
        return self.handle_incoming(
            phone=sender,
            text=message,
            invoice_id=kwargs.get("invoice_id"),
        )

    def set_active_invoice(self, phone: str, invoice_id: str) -> None:
        """Set the active invoice for a phone number."""
        self._active_invoice[phone] = invoice_id

    def get_active_invoice(self, phone: str) -> Optional[str]:
        """Get the active invoice for a phone number."""
        return self._active_invoice.get(phone)

    def clear_context(self, phone: str) -> None:
        """Clear all context for a phone number."""
        self._conversations.pop(phone, None)
        self._active_invoice.pop(phone, None)

    def create_invoice(self, invoice_id: str) -> None:
        """Create a new invoice (convenience method)."""
        self.orchestrator.create_invoice(invoice_id)

    def _add_to_history(self, phone: str, role: str, content: str) -> None:
        """Add a message to conversation history."""
        if phone not in self._conversations:
            self._conversations[phone] = []

        self._conversations[phone].append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Trim history if too long
        if len(self._conversations[phone]) > self.max_history:
            self._conversations[phone] = self._conversations[phone][-self.max_history:]

    def _get_history(self, phone: str) -> list[dict[str, Any]]:
        """Get conversation history for a phone number."""
        return self._conversations.get(phone, [])

    def _format_response(self, result: ToolExecutionResult) -> str:
        """
        Format orchestration result for WhatsApp.

        Args:
            result: Orchestration result.

        Returns:
            Formatted response text.
        """
        # If clarification is needed
        if result.requires_clarification and result.clarification_prompt:
            return result.clarification_prompt

        # Build response message
        parts = [result.message]

        # Add state info if available
        if result.current_state and result.invoice_id:
            state_display = result.current_state.replace("_", " ").title()
            parts.append(f"\nInvoice {result.invoice_id} status: {state_display}")

        # Add available actions hint
        if result.raw_decision and not result.requires_clarification:
            available = self.orchestrator.store.get_fsm(result.invoice_id)
            if available:
                triggers = available.get_available_triggers()
                if triggers:
                    actions = ", ".join(t.replace("_", " ") for t in triggers[:3])
                    parts.append(f"Available actions: {actions}")

        # Add warnings if any
        if result.warnings:
            for warning in result.warnings[:2]:  # Limit to 2 warnings
                parts.append(f"⚠️ {warning}")

        return "\n".join(parts)
