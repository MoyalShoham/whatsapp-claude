"""Tests for WhatsApp adapter."""

import pytest

from agents.invoice_agent import InvoiceOrchestrator
from channels.whatsapp.adapter import WhatsAppAdapter
from llm_router import LLMRouter, MockLLMProvider, RouterIntent, RouterTool
from state_machine.invoice_state import InvoiceState
from tools.base import InMemoryInvoiceStore


@pytest.fixture
def store() -> InMemoryInvoiceStore:
    """Create fresh store."""
    return InMemoryInvoiceStore()


@pytest.fixture
def mock_provider() -> MockLLMProvider:
    """Create mock provider."""
    return MockLLMProvider()


@pytest.fixture
def adapter(store: InMemoryInvoiceStore, mock_provider: MockLLMProvider) -> WhatsAppAdapter:
    """Create WhatsApp adapter with mock provider."""
    router = LLMRouter(llm_provider=mock_provider)
    orchestrator = InvoiceOrchestrator(store=store, router=router)
    return WhatsAppAdapter(orchestrator=orchestrator)


class TestWhatsAppAdapter:
    """Test WhatsApp adapter functionality."""

    def test_handle_incoming_basic(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test basic message handling."""
        mock_provider.set_response(
            intent=RouterIntent.GENERAL_QUESTION,
            tool=RouterTool.NONE,
            confidence="high",
            reasoning="General greeting",
        )

        response = adapter.handle_incoming("+972500000000", "Hello")

        assert response is not None
        assert isinstance(response, str)

    def test_handle_approval_flow(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test approval flow through adapter."""
        # Create and setup invoice
        adapter.create_invoice("INV-001")
        adapter.orchestrator.advance_invoice("INV-001", "send_invoice")
        adapter.orchestrator.advance_invoice("INV-001", "request_approval")

        # Mock approval response
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
        )

        response = adapter.handle_incoming(
            "+972500000000",
            "I approve invoice INV-001",
        )

        assert "INV-001" in response or "approved" in response.lower()

        # Verify state changed
        state = adapter.orchestrator.get_invoice_state("INV-001")
        assert state == InvoiceState.APPROVED

    def test_conversation_history_tracked(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that conversation history is tracked."""
        phone = "+972500000000"

        mock_provider.set_response(
            intent=RouterIntent.GENERAL_QUESTION,
            tool=RouterTool.NONE,
        )

        # Send multiple messages
        adapter.handle_incoming(phone, "Hello")
        adapter.handle_incoming(phone, "What is invoice status?")

        history = adapter._get_history(phone)

        # Should have 4 entries (2 user + 2 assistant)
        assert len(history) == 4
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"

    def test_active_invoice_context_persists(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that active invoice context persists between messages."""
        phone = "+972500000000"

        adapter.create_invoice("INV-001")
        adapter.set_active_invoice(phone, "INV-001")

        assert adapter.get_active_invoice(phone) == "INV-001"

        # Clear context
        adapter.clear_context(phone)
        assert adapter.get_active_invoice(phone) is None

    def test_handle_message_interface(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test the generic handle_message interface."""
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_QUESTION,
            tool=RouterTool.GET_INVOICE_STATUS,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
        )

        adapter.create_invoice("INV-001")

        response = adapter.handle_message(
            channel="whatsapp",
            sender="+972500000000",
            message="What is the status of INV-001?",
            invoice_id="INV-001",
        )

        assert response is not None

    def test_clarification_request(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that clarification requests are handled."""
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={},
            confidence="medium",
            requires_clarification=True,
            clarification_prompt="Which invoice would you like to approve?",
        )

        response = adapter.handle_incoming("+972500000000", "Approve the invoice")

        assert "which invoice" in response.lower()

    def test_error_handling(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test error handling in adapter."""
        # Force an error by using invalid provider
        class ErrorProvider:
            def complete(self, prompt: str) -> str:
                raise Exception("Test error")

        adapter.orchestrator.router.llm_provider = ErrorProvider()

        response = adapter.handle_incoming("+972500000000", "Test message")

        # Should return error message, not crash
        assert "שגיאה" in response or "error" in response.lower()

    def test_history_max_limit(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that history is limited to max_history."""
        adapter.max_history = 4
        phone = "+972500000000"

        mock_provider.set_response(
            intent=RouterIntent.GENERAL_QUESTION,
            tool=RouterTool.NONE,
        )

        # Send more messages than max_history
        for i in range(10):
            adapter.handle_incoming(phone, f"Message {i}")

        history = adapter._get_history(phone)

        # Should be limited (4 messages = 2 exchanges of user+assistant)
        assert len(history) <= 4


class TestMultipleUsers:
    """Test handling multiple users."""

    def test_separate_contexts_per_user(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that each user has separate context."""
        phone1 = "+972500000001"
        phone2 = "+972500000002"

        adapter.create_invoice("INV-001")
        adapter.create_invoice("INV-002")

        adapter.set_active_invoice(phone1, "INV-001")
        adapter.set_active_invoice(phone2, "INV-002")

        assert adapter.get_active_invoice(phone1) == "INV-001"
        assert adapter.get_active_invoice(phone2) == "INV-002"

        # Clear one user's context
        adapter.clear_context(phone1)

        assert adapter.get_active_invoice(phone1) is None
        assert adapter.get_active_invoice(phone2) == "INV-002"

    def test_separate_history_per_user(
        self,
        adapter: WhatsAppAdapter,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that each user has separate history."""
        phone1 = "+972500000001"
        phone2 = "+972500000002"

        mock_provider.set_response(
            intent=RouterIntent.GENERAL_QUESTION,
            tool=RouterTool.NONE,
        )

        adapter.handle_incoming(phone1, "Hello from user 1")
        adapter.handle_incoming(phone2, "Hello from user 2")
        adapter.handle_incoming(phone1, "Another message from user 1")

        history1 = adapter._get_history(phone1)
        history2 = adapter._get_history(phone2)

        # User 1 should have 4 entries (2 messages + 2 responses)
        assert len(history1) == 4
        # User 2 should have 2 entries
        assert len(history2) == 2
