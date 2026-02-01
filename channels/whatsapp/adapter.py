"""
WhatsApp Adapter - Bridge between WhatsApp messages and Conversational Agent.

This adapter:
1. Receives incoming WhatsApp messages
2. Routes them through the ConversationalAgent
3. Returns formatted responses for WhatsApp

Note: The production server (server/app.py) uses ConversationalAgent directly.
This adapter is primarily for testing and simulator use.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from agents.conversational_agent import ConversationalAgent, AgentMode
from agents.invoice_agent import InvoiceOrchestrator
from llm_router import get_default_provider
from tools.base import InMemoryInvoiceStore

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
    Adapter between WhatsApp channel and Conversational Agent.

    Handles:
    - Message formatting
    - Conversation context tracking
    - Response generation via ConversationalAgent
    """

    def __init__(
        self,
        agent: Optional[ConversationalAgent] = None,
        max_history: int = 10,
        mode: AgentMode = AgentMode.SIMULATOR,
    ):
        """
        Initialize the WhatsApp adapter.

        Args:
            agent: Conversational agent instance. Creates new if not provided.
            max_history: Maximum messages to keep in conversation history per user.
            mode: Agent mode (SIMULATOR or PRODUCTION).
        """
        if agent is None:
            # Create default agent with orchestrator and provider
            store = InMemoryInvoiceStore()
            orchestrator = InvoiceOrchestrator(store=store)
            llm_provider = get_default_provider()
            agent = ConversationalAgent(
                orchestrator=orchestrator,
                llm_provider=llm_provider,
                mode=mode,
            )

        self.agent = agent
        self.max_history = max_history

        # Track conversation history per phone number
        self._conversations: dict[str, list[dict[str, Any]]] = {}

    def handle_incoming(
        self,
        phone: str,
        text: str,
        message_id: Optional[str] = None,
    ) -> str:
        """
        Handle an incoming WhatsApp message.

        Args:
            phone: Sender's phone number.
            text: Message text.
            message_id: Optional WhatsApp message ID for correlation.

        Returns:
            Response text for WhatsApp.
        """
        logger.info(f"WhatsApp message from {phone}: {text[:50]}...")

        # Add to conversation history
        self._add_to_history(phone, "user", text)

        # Build context with conversation history
        context = {
            "channel": "whatsapp",
            "phone": phone,
            "message_id": message_id,
            "conversation_history": self._get_history(phone),
        }

        # Process through ConversationalAgent
        try:
            response = self.agent.process_message(
                message=text,
                customer_id=phone,
                context=context,
            )
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            response = "Sorry, an error occurred while processing your message. Please try again."

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
            message_id=kwargs.get("message_id"),
        )

    def clear_context(self, phone: str) -> None:
        """Clear all context for a phone number."""
        self._conversations.pop(phone, None)

    def create_invoice(self, invoice_id: str, customer_id: Optional[str] = None) -> Any:
        """Create a new invoice (delegates to agent's orchestrator)."""
        return self.agent.create_invoice(invoice_id, customer_id)

    def get_invoice_state(self, invoice_id: str) -> Optional[str]:
        """Get invoice state (delegates to agent's orchestrator)."""
        return self.agent.get_invoice_state(invoice_id)

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
