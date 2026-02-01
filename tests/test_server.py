"""Tests for the webhook server."""

import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# Skip if fastapi not installed
pytest.importorskip("fastapi")


@pytest.fixture
def client():
    """Create test client."""
    from server.app import create_app, app_state
    from server.config import Settings
    from agents.invoice_agent import InvoiceOrchestrator
    from llm_router import LLMRouter, MockLLMProvider
    from tools.base import InMemoryInvoiceStore

    # Create app with test settings
    app = create_app()

    # Initialize test state
    import server.app as app_module

    store = InMemoryInvoiceStore()
    mock_provider = MockLLMProvider()
    # Note: InvoiceOrchestrator doesn't use router - that's ConversationalAgent's responsibility
    orchestrator = InvoiceOrchestrator(store=store)

    class TestState:
        def __init__(self):
            self.settings = Settings(whatsapp_verify_token="test_token")
            self.store = store
            self.orchestrator = orchestrator
            self.audit_log = AsyncMock()
            self.whatsapp_client = AsyncMock()

    app_module.app_state = TestState()

    return TestClient(app)


class TestWebhookVerification:
    """Test webhook verification endpoint."""

    def test_verify_success(self, client):
        """Test successful webhook verification."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_token",
                "hub.challenge": "challenge_123",
            },
        )

        assert response.status_code == 200
        assert response.text == '"challenge_123"'

    def test_verify_wrong_token(self, client):
        """Test verification with wrong token."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "challenge_123",
            },
        )

        assert response.status_code == 403

    def test_verify_wrong_mode(self, client):
        """Test verification with wrong mode."""
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": "test_token",
                "hub.challenge": "challenge_123",
            },
        )

        assert response.status_code == 403


class TestWebhookReceive:
    """Test webhook receive endpoint."""

    def test_receive_text_message(self, client):
        """Test receiving a text message."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "456"},
                                "messages": [
                                    {
                                        "from": "972500000000",
                                        "id": "msg_123",
                                        "timestamp": "1234567890",
                                        "text": {"body": "Hello"},
                                        "type": "text",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        response = client.post("/webhook", json=payload)

        assert response.status_code == 200
        assert response.json() == {"status": "received"}

    def test_receive_non_text_message(self, client):
        """Test receiving a non-text message."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "from": "972500000000",
                                        "id": "msg_123",
                                        "type": "image",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        response = client.post("/webhook", json=payload)

        # Should still return 200
        assert response.status_code == 200

    def test_receive_empty_payload(self, client):
        """Test receiving empty payload."""
        response = client.post("/webhook", json={})

        assert response.status_code == 200

    def test_receive_invalid_json(self, client):
        """Test receiving invalid JSON."""
        response = client.post(
            "/webhook",
            content="not json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test health check returns OK."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "invoices_count" in data


class TestInvoiceEndpoints:
    """Test invoice admin endpoints."""

    def test_list_invoices_empty(self, client):
        """Test listing invoices when empty."""
        response = client.get("/invoices")

        assert response.status_code == 200
        assert response.json() == []

    def test_create_invoice(self, client):
        """Test creating an invoice."""
        response = client.post(
            "/invoices",
            json={"invoice_id": "INV-001"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["invoice_id"] == "INV-001"
        assert data["state"] == "new"

    def test_create_duplicate_invoice(self, client):
        """Test creating duplicate invoice."""
        # Create first
        client.post("/invoices", json={"invoice_id": "INV-002"})

        # Try duplicate
        response = client.post(
            "/invoices",
            json={"invoice_id": "INV-002"},
        )

        assert response.status_code == 409

    def test_get_invoice(self, client):
        """Test getting a specific invoice."""
        # Create first
        client.post("/invoices", json={"invoice_id": "INV-003"})

        # Get it
        response = client.get("/invoices/INV-003")

        assert response.status_code == 200
        data = response.json()
        assert data["invoice_id"] == "INV-003"

    def test_get_nonexistent_invoice(self, client):
        """Test getting nonexistent invoice."""
        response = client.get("/invoices/INV-999")

        assert response.status_code == 404

    def test_list_invoices_after_create(self, client):
        """Test listing invoices after creating some."""
        client.post("/invoices", json={"invoice_id": "INV-004"})
        client.post("/invoices", json={"invoice_id": "INV-005"})

        response = client.get("/invoices")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        invoice_ids = [inv["invoice_id"] for inv in data]
        assert "INV-004" in invoice_ids
        assert "INV-005" in invoice_ids


class TestSignatureVerification:
    """Test webhook signature verification."""

    def test_verify_signature_function(self):
        """Test the signature verification helper."""
        from server.app import verify_signature

        secret = "test_secret"
        payload = b'{"test": "data"}'

        # Compute valid signature
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        # Valid signature
        assert verify_signature(payload, f"sha256={expected}", secret)

        # Invalid signature
        assert not verify_signature(payload, "sha256=invalid", secret)

        # Missing prefix
        assert not verify_signature(payload, expected, secret)
