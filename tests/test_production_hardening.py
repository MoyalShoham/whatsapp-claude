"""
Tests for production hardening features.

- Timeout handling for LLM calls
- Retry with backoff
- Audit log (append-only)
- Structured error types
- Blocked action logging
"""

import json
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.invoice_agent.infrastructure import (
    AuditLog,
    AuditAction,
    AuditEntry,
    EnhancedEventBus,
    EnhancedEventSubscriber,
    EnhancedInvoiceEvent,
    EventType,
    OverdueInvoiceChecker,
)
from agents.invoice_agent.orchestrator import (
    InvoiceOrchestrator,
    StateError,
    ToolError,
)
from llm_router import (
    LLMRouter,
    MockLLMProvider,
    RouterIntent,
    RouterTool,
    LLMError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMResponseError,
)
from tools.base import InMemoryInvoiceStore


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def audit_log() -> AuditLog:
    """Create a fresh audit log."""
    return AuditLog()


@pytest.fixture
def store() -> InMemoryInvoiceStore:
    """Create a fresh store."""
    return InMemoryInvoiceStore()


@pytest.fixture
def mock_provider() -> MockLLMProvider:
    """Create a mock provider."""
    return MockLLMProvider()


@pytest.fixture
def orchestrator(store: InMemoryInvoiceStore, mock_provider: MockLLMProvider) -> InvoiceOrchestrator:
    """Create an orchestrator with mock provider."""
    # Note: InvoiceOrchestrator doesn't use router - that's ConversationalAgent's responsibility
    return InvoiceOrchestrator(store=store)


# ============================================================================
# Timeout Fallback Tests
# ============================================================================


class TestTimeoutFallback:
    """Test timeout handling for LLM calls."""

    def test_timeout_returns_fallback_decision(self) -> None:
        """Test that timeout returns a safe fallback decision."""
        mock = MockLLMProvider(
            error_on_call=0,
            error_type=LLMTimeoutError,
        )
        router = LLMRouter(llm_provider=mock)

        decision = router.route("Approve INV-001", state="awaiting_approval")

        # Should return fallback, not crash
        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.requires_clarification
        assert any("error" in w.lower() for w in decision.warnings)

    def test_timeout_error_is_retryable(self) -> None:
        """Test that timeout error is marked as retryable."""
        error = LLMTimeoutError("Test timeout", provider="test", timeout_seconds=30)
        assert error.retryable is True
        assert error.timeout_seconds == 30

    def test_rate_limit_error_is_retryable(self) -> None:
        """Test that rate limit error is marked as retryable."""
        error = LLMRateLimitError("Rate limited", provider="test", retry_after=60)
        assert error.retryable is True
        assert error.retry_after == 60

    def test_response_error_is_not_retryable(self) -> None:
        """Test that response error is not retryable."""
        error = LLMResponseError("Bad response", provider="test", raw_response="garbage")
        assert error.retryable is False


# ============================================================================
# Invalid JSON Recovery Tests
# ============================================================================


class TestInvalidJSONRecovery:
    """Test recovery from invalid JSON responses."""

    def test_recover_from_completely_invalid_json(self) -> None:
        """Test recovery from non-JSON response."""
        mock = MockLLMProvider(responses=["This is not JSON at all!!!"])
        router = LLMRouter(llm_provider=mock)

        decision = router.route("Test", state="new")

        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.requires_clarification

    def test_recover_from_truncated_json(self) -> None:
        """Test recovery from truncated JSON."""
        mock = MockLLMProvider(responses=['{"intent": "invoice_approval", "tool":'])
        router = LLMRouter(llm_provider=mock)

        decision = router.route("Test", state="new")

        assert decision.intent == RouterIntent.UNKNOWN
        assert decision.requires_clarification

    def test_recover_from_wrong_schema(self) -> None:
        """Test recovery from JSON with wrong schema."""
        mock = MockLLMProvider(responses=[json.dumps({
            "intent": "not_a_valid_intent",
            "tool": "approve_invoice",
        })])
        router = LLMRouter(llm_provider=mock)

        decision = router.route("Test", state="new")

        assert decision.intent == RouterIntent.UNKNOWN

    def test_extract_json_from_markdown(self) -> None:
        """Test JSON extraction from markdown code blocks."""
        response = """Here's my analysis:

```json
{
  "intent": "invoice_approval",
  "tool": "approve_invoice",
  "arguments": {"invoice_id": "INV-001"},
  "confidence": "high",
  "reasoning": "Clear approval",
  "requires_clarification": false,
  "clarification_prompt": null,
  "warnings": []
}
```
"""
        mock = MockLLMProvider(responses=[response])
        router = LLMRouter(llm_provider=mock)

        decision = router.route("Approve INV-001", state="awaiting_approval")

        assert decision.intent == RouterIntent.INVOICE_APPROVAL
        assert decision.arguments.invoice_id == "INV-001"


# ============================================================================
# Audit Log Tests
# ============================================================================


class TestAuditLog:
    """Test audit logging functionality."""

    def test_log_creates_entry(self, audit_log: AuditLog) -> None:
        """Test basic log entry creation."""
        entry = audit_log.log(
            AuditAction.MESSAGE_RECEIVED,
            invoice_id="INV-001",
            message="Test message",
        )

        assert entry.action == AuditAction.MESSAGE_RECEIVED
        assert entry.invoice_id == "INV-001"
        assert entry.details["message"] == "Test message"
        assert entry.timestamp is not None
        assert entry.entry_id is not None

    def test_log_is_append_only(self, audit_log: AuditLog) -> None:
        """Test that log entries cannot be modified."""
        audit_log.log(AuditAction.MESSAGE_RECEIVED, invoice_id="INV-001")
        audit_log.log(AuditAction.ROUTING_DECISION, invoice_id="INV-001")
        audit_log.log(AuditAction.TOOL_EXECUTED, invoice_id="INV-001")

        entries = audit_log.get_all_entries()
        assert len(entries) == 3

        # Verify entries are preserved
        assert entries[0].action == AuditAction.MESSAGE_RECEIVED
        assert entries[1].action == AuditAction.ROUTING_DECISION
        assert entries[2].action == AuditAction.TOOL_EXECUTED

    def test_log_message_received(self, audit_log: AuditLog) -> None:
        """Test message received logging."""
        entry = audit_log.log_message_received(
            message="I approve invoice INV-001",
            invoice_id="INV-001",
            customer_id="CUST-001",
        )

        assert entry.action == AuditAction.MESSAGE_RECEIVED
        assert "I approve" in entry.details["message"]

    def test_log_routing_decision(self, audit_log: AuditLog) -> None:
        """Test routing decision logging."""
        entry = audit_log.log_routing_decision(
            intent="invoice_approval",
            tool="approve_invoice",
            confidence="high",
            invoice_id="INV-001",
            warnings=["State mismatch"],
        )

        assert entry.action == AuditAction.ROUTING_DECISION
        assert entry.details["intent"] == "invoice_approval"
        assert entry.details["warnings"] == ["State mismatch"]

    def test_log_tool_executed(self, audit_log: AuditLog) -> None:
        """Test tool execution logging."""
        entry = audit_log.log_tool_executed(
            tool_name="approve_invoice",
            success=True,
            invoice_id="INV-001",
            result={"state": "approved"},
        )

        assert entry.action == AuditAction.TOOL_EXECUTED
        assert entry.details["success"] is True

    def test_log_blocked_action(self, audit_log: AuditLog) -> None:
        """Test blocked action logging."""
        entry = audit_log.log_blocked_action(
            action="approve",
            reason="Invoice not in awaiting_approval state",
            invoice_id="INV-001",
            current_state="new",
        )

        assert entry.action == AuditAction.BLOCKED_ACTION
        assert entry.details["blocked_action"] == "approve"
        assert entry.details["reason"] == "Invoice not in awaiting_approval state"

    def test_log_error(self, audit_log: AuditLog) -> None:
        """Test error logging."""
        entry = audit_log.log_error(
            error_type="LLMTimeoutError",
            error_message="Request timed out after 30s",
            invoice_id="INV-001",
            retry_count=2,
        )

        assert entry.action == AuditAction.ERROR_OCCURRED
        assert entry.details["error_type"] == "LLMTimeoutError"
        assert entry.details["retry_count"] == 2

    def test_filter_entries_by_action(self, audit_log: AuditLog) -> None:
        """Test filtering entries by action type."""
        audit_log.log(AuditAction.MESSAGE_RECEIVED, invoice_id="INV-001")
        audit_log.log(AuditAction.ROUTING_DECISION, invoice_id="INV-001")
        audit_log.log(AuditAction.MESSAGE_RECEIVED, invoice_id="INV-002")

        entries = audit_log.get_entries(action=AuditAction.MESSAGE_RECEIVED)
        assert len(entries) == 2
        assert all(e.action == AuditAction.MESSAGE_RECEIVED for e in entries)

    def test_filter_entries_by_invoice(self, audit_log: AuditLog) -> None:
        """Test filtering entries by invoice ID."""
        audit_log.log(AuditAction.MESSAGE_RECEIVED, invoice_id="INV-001")
        audit_log.log(AuditAction.MESSAGE_RECEIVED, invoice_id="INV-002")
        audit_log.log(AuditAction.ROUTING_DECISION, invoice_id="INV-001")

        entries = audit_log.get_entries(invoice_id="INV-001")
        assert len(entries) == 2
        assert all(e.invoice_id == "INV-001" for e in entries)

    def test_log_to_file(self) -> None:
        """Test file persistence."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            file_path = Path(f.name)

        try:
            log = AuditLog(file_path=file_path)
            log.log(AuditAction.MESSAGE_RECEIVED, invoice_id="INV-001", message="Test")
            log.log(AuditAction.ROUTING_DECISION, invoice_id="INV-001", intent="approval")
            log.close()

            # Read back and verify
            with open(file_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 2
            entry1 = json.loads(lines[0])
            assert entry1["action"] == "message_received"
        finally:
            file_path.unlink(missing_ok=True)


# ============================================================================
# Enhanced Event Bus Tests
# ============================================================================


class TestEnhancedEventBus:
    """Test enhanced event bus functionality."""

    def test_events_are_idempotent(self) -> None:
        """Test that same event only fires once."""
        bus = EnhancedEventBus()

        class Counter(EnhancedEventSubscriber):
            def __init__(self) -> None:
                self.count = 0

            def on_event(self, event: EnhancedInvoiceEvent) -> None:
                self.count += 1

        counter = Counter()
        bus.subscribe(counter)

        event = EnhancedInvoiceEvent(
            event_type=EventType.INVOICE_APPROVED,
            invoice_id="INV-001",
            customer_id="CUST-001",
            timestamp=datetime.utcnow(),
            payload={"test": "data"},
        )

        # Publish twice
        result1 = bus.publish(event)
        result2 = bus.publish(event)

        assert result1 is True  # First publish succeeds
        assert result2 is False  # Second is idempotent
        assert counter.count == 1  # Only fired once

    def test_subscriber_filtering(self) -> None:
        """Test that subscribers can filter event types."""
        bus = EnhancedEventBus()

        class ApprovalOnlySubscriber(EnhancedEventSubscriber):
            def __init__(self) -> None:
                self.events: list[EnhancedInvoiceEvent] = []

            def get_subscribed_events(self) -> list[EventType]:
                return [EventType.INVOICE_APPROVED]

            def on_event(self, event: EnhancedInvoiceEvent) -> None:
                self.events.append(event)

        subscriber = ApprovalOnlySubscriber()
        bus.subscribe(subscriber)

        # Publish different event types
        bus.create_and_publish(EventType.INVOICE_APPROVED, "INV-001")
        bus.create_and_publish(EventType.INVOICE_PAID, "INV-001")
        bus.create_and_publish(EventType.INVOICE_APPROVED, "INV-002")

        # Should only receive approval events
        assert len(subscriber.events) == 2
        assert all(e.event_type == EventType.INVOICE_APPROVED for e in subscriber.events)

    def test_event_contains_full_payload(self) -> None:
        """Test events contain full payload."""
        bus = EnhancedEventBus()

        event = bus.create_and_publish(
            event_type=EventType.INVOICE_PAID,
            invoice_id="INV-001",
            customer_id="CUST-001",
            amount=100.00,
            currency="USD",
        )

        assert event.invoice_id == "INV-001"
        assert event.customer_id == "CUST-001"
        assert event.payload["amount"] == 100.00
        assert event.payload["currency"] == "USD"
        assert event.timestamp is not None
        assert event.event_id is not None

    def test_event_history(self) -> None:
        """Test event history retrieval."""
        bus = EnhancedEventBus()

        bus.create_and_publish(EventType.INVOICE_APPROVED, "INV-001")
        bus.create_and_publish(EventType.INVOICE_PAID, "INV-001")
        bus.create_and_publish(EventType.INVOICE_APPROVED, "INV-002")

        # Get all history
        all_events = bus.get_history()
        assert len(all_events) == 3

        # Filter by type
        approved_events = bus.get_history(event_type=EventType.INVOICE_APPROVED)
        assert len(approved_events) == 2

        # Filter by invoice
        inv001_events = bus.get_history(invoice_id="INV-001")
        assert len(inv001_events) == 2


# ============================================================================
# Blocked Actions Logging Tests
# ============================================================================


class TestBlockedActionsLogging:
    """Test that blocked actions are logged correctly."""

    def test_blocked_state_transition_logged(
        self,
        orchestrator: InvoiceOrchestrator,
        mock_provider: MockLLMProvider,
    ) -> None:
        """Test that blocked state transitions are logged."""
        audit_log = AuditLog()

        orchestrator.create_invoice("INV-001")

        # Try invalid transition - approval from 'new' state
        mock_provider.set_response(
            intent=RouterIntent.INVOICE_APPROVAL,
            tool=RouterTool.APPROVE_INVOICE,
            arguments={"invoice_id": "INV-001"},
            confidence="high",
        )

        result = orchestrator.process_message(
            message="Approve INV-001",
            invoice_id="INV-001",
        )

        # Action should fail
        assert not result.success

        # Log the blocked action
        audit_log.log_blocked_action(
            action="approve",
            reason="Invalid state transition",
            invoice_id="INV-001",
            current_state="new",
        )

        # Verify it's logged
        blocked_entries = audit_log.get_entries(action=AuditAction.BLOCKED_ACTION)
        assert len(blocked_entries) == 1
        assert blocked_entries[0].invoice_id == "INV-001"

    def test_state_error_is_raised_on_invalid_transition(
        self,
        orchestrator: InvoiceOrchestrator,
    ) -> None:
        """Test StateError is raised for invalid transitions."""
        orchestrator.create_invoice("INV-001")

        with pytest.raises(StateError) as exc_info:
            orchestrator.advance_invoice("INV-001", "approve")

        assert exc_info.value.current_state == "new"
        assert exc_info.value.attempted_action == "approve"
        assert exc_info.value.invoice_id == "INV-001"


# ============================================================================
# Overdue Invoice Tests
# ============================================================================


class TestOverdueInvoiceChecker:
    """Test overdue invoice checking."""

    def test_fires_overdue_event(self) -> None:
        """Test that overdue invoices trigger events."""
        bus = EnhancedEventBus()

        # Mock invoice data with overdue invoice
        invoices = [
            {
                "invoice_id": "INV-001",
                "customer_id": "CUST-001",
                "state": "payment_pending",
                "due_date": datetime(2020, 1, 1),  # Way in the past
            },
            {
                "invoice_id": "INV-002",
                "customer_id": "CUST-002",
                "state": "payment_pending",
                "due_date": datetime(2099, 1, 1),  # Way in the future
            },
        ]

        checker = OverdueInvoiceChecker(
            event_bus=bus,
            get_invoices=lambda: invoices,
        )

        overdue_ids = checker.check_overdue()

        assert "INV-001" in overdue_ids
        assert "INV-002" not in overdue_ids

        # Verify event was fired
        overdue_events = bus.get_history(event_type=EventType.INVOICE_OVERDUE)
        assert len(overdue_events) == 1
        assert overdue_events[0].invoice_id == "INV-001"
        assert "days_overdue" in overdue_events[0].payload


# ============================================================================
# Error Type Tests
# ============================================================================


class TestStructuredErrorTypes:
    """Test structured error types."""

    def test_llm_error_attributes(self) -> None:
        """Test LLMError has correct attributes."""
        error = LLMError("Test error", provider="claude", retryable=True)

        assert str(error) == "Test error"
        assert error.provider == "claude"
        assert error.retryable is True

    def test_state_error_attributes(self) -> None:
        """Test StateError has correct attributes."""
        error = StateError(
            "Cannot approve from new state",
            current_state="new",
            attempted_action="approve",
            invoice_id="INV-001",
        )

        assert "approve" in str(error)
        assert error.current_state == "new"
        assert error.attempted_action == "approve"
        assert error.invoice_id == "INV-001"

    def test_tool_error_attributes(self) -> None:
        """Test ToolError has correct attributes."""
        error = ToolError(
            "Tool failed",
            tool_name="approve_invoice",
            invoice_id="INV-001",
            details={"reason": "state mismatch"},
        )

        assert error.tool_name == "approve_invoice"
        assert error.invoice_id == "INV-001"
        assert error.details["reason"] == "state mismatch"
