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
.
├── vendor/
│   └── claude_framework/        # Vendored external framework
├── agents/
│   └── invoice_agent/           # Invoice domain logic
├── tools/                       # LangChain / Claude tools
├── state_machine/               # Invoice states + transitions
├── tests/                       # Test harnesses
├── README.md
└── pyproject.toml
```

## State Machine

The invoice lifecycle follows a unified state machine:

```
┌─────────┐
│   new   │
└────┬────┘
     │ send_invoice
     ▼
┌─────────────────┐
│  invoice_sent   │
└────────┬────────┘
         │ request_approval
         ▼
┌────────────────────┐
│  awaiting_approval │
└─────────┬──────────┘
          │
    ┌─────┴─────┐
    │           │
    ▼           ▼
┌────────┐  ┌──────────┐
│approved│  │ rejected │
└───┬────┘  └────┬─────┘
    │            │
    ▼            │
┌────────────────┐│
│payment_pending ││
└───────┬────────┘│
        │         │
        ▼         │
    ┌──────┐      │
    │ paid │      │
    └──┬───┘      │
       │          │
       ▼          ▼
   ┌────────┐  ┌──────────┐
   │ closed │  │ disputed │◄──┐
   └────────┘  └────┬─────┘   │
                    │         │
                    └─────────┘
                    (can reopen)
```

## Supported Intents

| Intent | Description |
|--------|-------------|
| `invoice_question` | General questions about an invoice |
| `invoice_approval` | Request to approve an invoice |
| `invoice_rejection` | Request to reject an invoice |
| `payment_confirmation` | Confirm payment has been made |
| `invoice_dispute` | Dispute an invoice |
| `request_invoice_copy` | Request a copy of the invoice |
| `general_question` | General non-invoice questions |

## Installation

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

## Usage

```python
from agents.invoice_agent import InvoiceAgent
from state_machine.invoice_state import InvoiceFSM

# Create a new invoice state machine
fsm = InvoiceFSM(invoice_id="INV-001")

# Process through the agent
agent = InvoiceAgent()
response = agent.process_message(
    message="I'd like to approve invoice INV-001",
    invoice_id="INV-001"
)
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=agents --cov=tools --cov=state_machine

# Run specific test file
pytest tests/test_state_machine.py -v
```

## Development

```bash
# Lint
ruff check .

# Format
ruff format .

# Type check
mypy agents tools state_machine
```

## License

MIT
