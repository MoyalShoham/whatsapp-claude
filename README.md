# Invoice Automation Agent

A production-ready invoice automation agent using a state machine + tools architecture for handling customer invoice-related messages.

## Overview

This agent handles:
- Invoice questions and inquiries
- Invoice approval/rejection workflows
- Payment confirmations
- Invoice disputes
- Invoice copy requests

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           INVOICE AUTOMATION AGENT                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │   Customer   │────▶│  LLM Router  │────▶│    Tools     │                │
│  │   Message    │     │              │     │              │                │
│  └──────────────┘     │  - Intent    │     │ - Approve    │                │
│                       │  - Tool      │     │ - Reject     │                │
│                       │  - Arguments │     │ - Pay        │                │
│                       │  - Guards    │     │ - Dispute    │                │
│                       └──────┬───────┘     └──────┬───────┘                │
│                              │                    │                         │
│                              ▼                    ▼                         │
│                       ┌──────────────────────────────────┐                 │
│                       │         STATE MACHINE            │                 │
│                       │      (Source of Truth)           │                 │
│                       │                                  │                 │
│                       │  new → sent → awaiting → ...     │                 │
│                       │                                  │                 │
│                       │  ✓ Validates all transitions     │                 │
│                       │  ✓ Blocks invalid actions        │                 │
│                       │  ✓ Maintains history             │                 │
│                       └──────────────┬───────────────────┘                 │
│                                      │                                      │
│                                      ▼                                      │
│                       ┌──────────────────────────────────┐                 │
│                       │          EVENT BUS               │                 │
│                       │                                  │                 │
│                       │  invoice_approved                │                 │
│                       │  invoice_paid                    │                 │
│                       │  invoice_overdue                 │                 │
│                       └──────────────────────────────────┘                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
.
├── vendor/
│   └── claude_framework/        # Vendored external framework
├── agents/
│   └── invoice_agent/
│       ├── agent.py             # Original agent (intent-based)
│       ├── orchestrator.py      # E2E flow controller
│       └── infrastructure.py    # Audit log, events
├── llm_router/
│   ├── router.py                # LLM-based intent router
│   ├── providers.py             # Claude, Mock, Stub providers
│   ├── schemas.py               # RouterDecision, intents, tools
│   └── prompt.md                # Prompt template with guard rails
├── tools/
│   ├── base.py                  # BaseInvoiceTool, store
│   └── invoice_tools.py         # All invoice tools
├── state_machine/
│   ├── invoice_state.py         # InvoiceFSM
│   └── models.py                # Domain models
├── tests/
│   ├── test_state_machine.py
│   ├── test_tools.py
│   ├── test_router.py
│   ├── test_provider_integration.py
│   ├── test_e2e_invoice_flow.py
│   └── test_production_hardening.py
└── pyproject.toml
```

## How Routing Works

The LLM Router analyzes customer messages and returns structured routing decisions:

```
┌─────────────────┐
│ Customer says:  │
│ "I approve      │
│  INV-001"       │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                    LLM ROUTER                           │
│                                                         │
│  1. Build prompt with:                                  │
│     - Available intents (approve, reject, pay, etc.)    │
│     - Available tools (approve_invoice, etc.)           │
│     - Current state (awaiting_approval)                 │
│     - Guard rails (no fake IDs, no assumed payments)    │
│                                                         │
│  2. Call LLM provider (Claude/Stub/Mock)                │
│     - temperature = 0 (deterministic)                   │
│     - timeout + retry protection                        │
│                                                         │
│  3. Parse JSON response                                 │
│     - Validate against schema                           │
│     - Fallback to "unknown" on errors                   │
│                                                         │
│  4. Return RouterDecision:                              │
│     {                                                   │
│       intent: "invoice_approval",                       │
│       tool: "approve_invoice",                          │
│       arguments: { invoice_id: "INV-001" },             │
│       confidence: "high",                               │
│       warnings: []                                      │
│     }                                                   │
└─────────────────────────────────────────────────────────┘
```

### Router Guard Rails

The router enforces strict rules:

1. **Never invent invoice IDs** - Only use IDs explicitly in the message
2. **Never assume payment** - "I'll pay" ≠ "I have paid"
3. **Never skip validation** - State mismatches generate warnings
4. **When in doubt, return unknown** - Ambiguous messages trigger clarification

## How State Machine Protects Actions

The state machine is the **source of truth** for all invoice operations:

```
                    STATE MACHINE PROTECTION
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  EVERY tool execution goes through the FSM:             │
│                                                         │
│  1. Tool receives request: approve("INV-001")           │
│                                                         │
│  2. FSM checks: can_trigger("approve")?                 │
│     - Current state: "awaiting_approval" → ✓ YES        │
│     - Current state: "new" → ✗ NO (blocked)             │
│     - Current state: "closed" → ✗ NO (terminal)         │
│                                                         │
│  3. If blocked:                                         │
│     - TransitionError raised                            │
│     - Action logged to audit                            │
│     - Response: "Cannot approve from state 'new'"       │
│                                                         │
│  4. If allowed:                                         │
│     - State transitions: awaiting_approval → approved   │
│     - Event fired: invoice_approved                     │
│     - History recorded                                  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Valid State Transitions

| From State | Allowed Triggers |
|------------|------------------|
| `new` | `send_invoice` |
| `invoice_sent` | `request_approval` |
| `awaiting_approval` | `approve`, `reject` |
| `approved` | `request_payment`, `dispute` |
| `payment_pending` | `confirm_payment`, `dispute` |
| `paid` | `close`, `dispute` |
| `rejected` | `close` |
| `disputed` | `resolve_dispute` |
| `closed` | *(terminal - no transitions)* |

## How to Add a New Tool Safely

Follow these steps to add a new tool without breaking existing functionality:

### Step 1: Define the Tool in `tools/invoice_tools.py`

```python
class MyNewTool(BaseInvoiceTool):
    """Description of what the tool does."""

    name = "my_new_tool"
    description = "What this tool does and when to use it."

    def _execute(
        self,
        invoice_id: str,
        **kwargs: Any,
    ) -> ToolResult:
        fsm = self._get_fsm(invoice_id)
        if not fsm:
            return self._not_found_result(invoice_id)

        # Check if action is valid from current state
        if fsm.current_state not in ["allowed_state_1", "allowed_state_2"]:
            return ToolResult(
                success=False,
                message=f"Cannot do this from state '{fsm.current_state}'",
                error={"code": "INVALID_STATE"},
            )

        # Execute the action (may trigger state transition)
        # ...

        return ToolResult(
            success=True,
            message="Action completed",
            data={"result": "data"},
        )
```

### Step 2: Add to Router Schemas (`llm_router/schemas.py`)

```python
class RouterTool(str, Enum):
    # ... existing tools ...
    MY_NEW_TOOL = "my_new_tool"

# Update valid states mapping
TOOL_VALID_STATES: dict[RouterTool, list[str]] = {
    # ... existing mappings ...
    RouterTool.MY_NEW_TOOL: ["allowed_state_1", "allowed_state_2"],
}
```

### Step 3: Add to Prompt Template (`llm_router/prompt.md`)

Add to the "Available Tools" table:

```markdown
| `my_new_tool` | Description | allowed_state_1, allowed_state_2 |
```

### Step 4: Register in Orchestrator (`agents/invoice_agent/orchestrator.py`)

```python
self._tools = {
    # ... existing tools ...
    RouterTool.MY_NEW_TOOL: MyNewTool(self.store),
}
```

### Step 5: Add Tests

```python
# tests/test_tools.py
class TestMyNewTool:
    def test_success_case(self, store):
        # Setup invoice in correct state
        # Execute tool
        # Assert success

    def test_blocked_from_wrong_state(self, store):
        # Setup invoice in wrong state
        # Execute tool
        # Assert failure with INVALID_STATE error
```

## Running Tests

```bash
# Install dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_state_machine.py -v

# Run with coverage
pytest --cov=agents --cov=tools --cov=state_machine --cov=llm_router

# Run only E2E tests
pytest tests/test_e2e_invoice_flow.py -v

# Run production hardening tests
pytest tests/test_production_hardening.py -v
```

### Test Categories

| Test File | Description |
|-----------|-------------|
| `test_state_machine.py` | FSM transitions, guards, history |
| `test_tools.py` | Individual tool execution |
| `test_router.py` | Intent classification, JSON parsing |
| `test_provider_integration.py` | LLM provider swapping, mocking |
| `test_e2e_invoice_flow.py` | Full message → state → event flow |
| `test_production_hardening.py` | Timeouts, retries, audit logs |
| `test_agent.py` | Original agent integration |
| `test_harness.py` | Manual test harness with state tables |

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Claude API key | Yes (for production) |

If `ANTHROPIC_API_KEY` is not set, the system falls back to a stub provider for testing.

### Provider Configuration

```python
from llm_router import LLMRouter, ClaudeLLMProvider, create_provider

# Use Claude (production)
router = LLMRouter(llm_provider=ClaudeLLMProvider())

# Use stub (testing/development)
router = LLMRouter()  # Defaults to StubLLMProvider

# Use mock (unit tests)
from llm_router import MockLLMProvider
mock = MockLLMProvider()
mock.set_response(intent=RouterIntent.INVOICE_APPROVAL, tool=RouterTool.APPROVE_INVOICE)
router = LLMRouter(llm_provider=mock)
```

## Event System

The system fires events on state transitions:

| Event | Trigger |
|-------|---------|
| `invoice_approved` | awaiting_approval → approved |
| `invoice_paid` | payment_pending → paid |
| `invoice_closed` | paid/rejected → closed |
| `invoice_disputed` | approved/payment_pending/paid → disputed |
| `invoice_overdue` | Scheduled check (external trigger) |

### Subscribing to Events

```python
from agents.invoice_agent import EnhancedEventSubscriber, EventType

class MySubscriber(EnhancedEventSubscriber):
    def get_subscribed_events(self) -> list[EventType]:
        return [EventType.INVOICE_APPROVED, EventType.INVOICE_PAID]

    def on_event(self, event):
        print(f"Received {event.event_type} for {event.invoice_id}")

# Register subscriber
orchestrator.event_bus.subscribe(MySubscriber())
```

## Audit Logging

All actions are logged to an append-only audit log:

```python
from agents.invoice_agent import AuditLog, AuditAction

audit = AuditLog(file_path=Path("audit.jsonl"))

# Automatic logging in orchestrator, or manual:
audit.log_message_received("Approve INV-001", invoice_id="INV-001")
audit.log_blocked_action("approve", "Invalid state", invoice_id="INV-001")

# Query audit log
blocked = audit.get_entries(action=AuditAction.BLOCKED_ACTION)
```

## License

MIT
