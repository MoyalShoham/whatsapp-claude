"""
FastAPI application for WhatsApp Business API webhook integration.

This server uses the SAME ConversationalAgent as the simulator.
The only difference is configuration (AgentMode.PRODUCTION).

Endpoints:
- GET /webhook  - Verification endpoint for Meta
- POST /webhook - Receive incoming messages
- GET /health   - Health check
- GET /invoices - List invoices (admin)
"""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.config import Settings, get_settings
from server.whatsapp_client import WhatsAppClient
from agents.conversational_agent import ConversationalAgent, AgentMode
from agents.invoice_agent import InvoiceOrchestrator, AuditLog
from llm_router import get_default_provider
from tools.base import InMemoryInvoiceStore

logger = logging.getLogger(__name__)


# ============================================================================
# Response Models
# ============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    mode: str
    invoices_count: int


class InvoiceResponse(BaseModel):
    """Invoice info response."""

    invoice_id: str
    state: str
    is_terminal: bool
    available_triggers: list[str]


# ============================================================================
# Application State
# ============================================================================


class AppState:
    """Application state container."""

    def __init__(self, settings: Settings):
        self.settings = settings

        # Create shared store
        self.store = InMemoryInvoiceStore()

        # Create orchestrator (FSM validator + tool executor + audit)
        self.orchestrator = InvoiceOrchestrator(store=self.store)

        # Create audit log
        self.audit_log = AuditLog()

        # Create LLM provider
        self.llm_provider = get_default_provider()

        # Create conversational agent in PRODUCTION mode
        self.agent = ConversationalAgent(
            orchestrator=self.orchestrator,
            llm_provider=self.llm_provider,
            mode=AgentMode.PRODUCTION,
        )

        # Create WhatsApp client
        self.whatsapp_client = WhatsAppClient(
            api_token=settings.whatsapp_api_token,
            phone_number_id=settings.whatsapp_phone_number_id,
        )

        # Conversation history per phone number
        self._conversations: dict[str, list[dict[str, Any]]] = {}

    def add_to_history(self, phone: str, role: str, content: str) -> None:
        """Add message to conversation history."""
        if phone not in self._conversations:
            self._conversations[phone] = []

        self._conversations[phone].append({
            "role": role,
            "content": content,
        })

        # Keep last 10 messages
        if len(self._conversations[phone]) > 10:
            self._conversations[phone] = self._conversations[phone][-10:]

    def get_history(self, phone: str) -> list[dict[str, Any]]:
        """Get conversation history for a phone number."""
        return self._conversations.get(phone, [])


# Global state (will be initialized on startup)
app_state: Optional[AppState] = None


# ============================================================================
# Lifespan
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global app_state

    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Starting Invoice Agent Server...")

    # Initialize app state
    app_state = AppState(settings)

    logger.info(f"Server ready on {settings.host}:{settings.port}")
    logger.info(f"Agent mode: PRODUCTION")
    logger.info(f"LLM provider: {type(app_state.llm_provider).__name__}")

    yield

    # Cleanup
    logger.info("Shutting down...")
    app_state.audit_log.close()


# ============================================================================
# Application Factory
# ============================================================================


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="Invoice Automation Agent",
        description="WhatsApp Business API webhook server for invoice automation",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Register routes
    app.add_api_route("/webhook", webhook_verify, methods=["GET"])
    app.add_api_route("/webhook", webhook_receive, methods=["POST"])
    app.add_api_route("/health", health_check, methods=["GET"])
    app.add_api_route("/invoices", list_invoices, methods=["GET"])
    app.add_api_route("/invoices/{invoice_id}", get_invoice, methods=["GET"])
    app.add_api_route("/invoices", create_invoice, methods=["POST"])

    return app


# ============================================================================
# Webhook Endpoints
# ============================================================================


async def webhook_verify(request: Request) -> str:
    """
    Verify webhook subscription with Meta.

    Meta sends a GET request with:
    - hub.mode=subscribe
    - hub.verify_token=<your_token>
    - hub.challenge=<challenge_string>

    We must return the challenge if token matches.
    """
    params = dict(request.query_params)

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    settings = get_settings()

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully")
        return challenge or ""

    logger.warning(f"Webhook verification failed: mode={mode}, token={token}")
    raise HTTPException(status_code=403, detail="Verification failed")


async def webhook_receive(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Receive incoming WhatsApp messages.

    Payload structure:
    {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "<WHATSAPP_BUSINESS_ACCOUNT_ID>",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {...},
                    "contacts": [{...}],
                    "messages": [{
                        "from": "<PHONE_NUMBER>",
                        "id": "<MESSAGE_ID>",
                        "timestamp": "<TIMESTAMP>",
                        "text": {"body": "<MESSAGE_TEXT>"},
                        "type": "text"
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    """
    # Verify signature if app secret is configured
    settings = get_settings()
    if settings.meta_app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        body = await request.body()
        if not verify_signature(body, signature, settings.meta_app_secret):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

    # Parse payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.debug(f"Webhook payload: {payload}")

    # Process messages in background
    if payload.get("object") == "whatsapp_business_account":
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "messages":
                    value = change.get("value", {})
                    messages = value.get("messages", [])

                    for message in messages:
                        background_tasks.add_task(
                            process_incoming_message,
                            message=message,
                            metadata=value.get("metadata", {}),
                        )

    # Always return 200 quickly to acknowledge receipt
    return JSONResponse(content={"status": "received"}, status_code=200)


async def process_incoming_message(
    message: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    """Process an incoming WhatsApp message using ConversationalAgent."""
    global app_state

    if not app_state:
        logger.error("App state not initialized")
        return

    try:
        # Extract message details
        phone = message.get("from", "")
        message_id = message.get("id", "")
        message_type = message.get("type", "")

        # Only handle text messages for now
        if message_type != "text":
            logger.info(f"Ignoring non-text message type: {message_type}")
            return

        text = message.get("text", {}).get("body", "")

        if not text:
            return

        logger.info(f"Processing message from {phone}: {text[:50]}...")

        # Log to audit
        app_state.audit_log.log_message_received(
            message=text,
            customer_id=phone,
        )

        # Add to conversation history
        app_state.add_to_history(phone, "user", text)

        # Process through ConversationalAgent (same code path as simulator)
        context = {
            "channel": "whatsapp",
            "message_id": message_id,
            "conversation_history": app_state.get_history(phone),
        }

        response_text = app_state.agent.process_message(
            message=text,
            customer_id=phone,
            context=context,
        )

        # Add response to history
        app_state.add_to_history(phone, "assistant", response_text)

        # Send response via WhatsApp API
        await app_state.whatsapp_client.send_message(
            to=phone,
            text=response_text,
        )

        logger.info(f"Sent response to {phone}")

    except Exception as e:
        logger.exception(f"Error processing message: {e}")

        # Try to send error message
        try:
            await app_state.whatsapp_client.send_message(
                to=phone,
                text="Sorry, an error occurred processing your message. Please try again.",
            )
        except Exception:
            pass


# ============================================================================
# Admin Endpoints
# ============================================================================


async def health_check() -> HealthResponse:
    """Health check endpoint."""
    global app_state

    if not app_state:
        raise HTTPException(status_code=503, detail="Service not ready")

    return HealthResponse(
        status="healthy",
        version="1.0.0",
        mode="production",
        invoices_count=len(app_state.orchestrator.list_invoices()),
    )


async def list_invoices() -> list[InvoiceResponse]:
    """List all invoices."""
    global app_state

    if not app_state:
        raise HTTPException(status_code=503, detail="Service not ready")

    invoices = []
    for inv in app_state.orchestrator.list_invoices():
        invoices.append(InvoiceResponse(
            invoice_id=inv["invoice_id"],
            state=inv["state"],
            is_terminal=inv["is_terminal"],
            available_triggers=inv["available_triggers"],
        ))

    return invoices


async def get_invoice(invoice_id: str) -> InvoiceResponse:
    """Get a specific invoice."""
    global app_state

    if not app_state:
        raise HTTPException(status_code=503, detail="Service not ready")

    fsm = app_state.orchestrator.get_invoice(invoice_id.upper())
    if not fsm:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return InvoiceResponse(
        invoice_id=fsm.invoice_id,
        state=fsm.current_state,
        is_terminal=fsm.is_terminal,
        available_triggers=fsm.get_available_triggers(),
    )


class CreateInvoiceRequest(BaseModel):
    """Request to create an invoice."""

    invoice_id: str


async def create_invoice(request: CreateInvoiceRequest) -> InvoiceResponse:
    """Create a new invoice."""
    global app_state

    if not app_state:
        raise HTTPException(status_code=503, detail="Service not ready")

    invoice_id = request.invoice_id.upper()

    # Check if exists
    if app_state.orchestrator.get_invoice(invoice_id):
        raise HTTPException(status_code=409, detail="Invoice already exists")

    # Create
    fsm = app_state.orchestrator.create_invoice(invoice_id)

    return InvoiceResponse(
        invoice_id=fsm.invoice_id,
        state=fsm.current_state,
        is_terminal=fsm.is_terminal,
        available_triggers=fsm.get_available_triggers(),
    )


# ============================================================================
# Helpers
# ============================================================================


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Meta webhook signature."""
    if not signature.startswith("sha256="):
        return False

    expected = signature[7:]
    computed = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, computed)


# ============================================================================
# App Instance
# ============================================================================


app = create_app()
