# WhatsApp Invoice Automation Agent

## Overview

A production-ready Invoice Automation Agent that handles invoice lifecycle management through WhatsApp Business API. Built with a state machine architecture, LLM-powered intent classification, and persistent storage.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   WhatsApp      │────▶│   Webhook       │────▶│   Orchestrator  │
│   Business API  │◀────│   Server        │◀────│   (Agent)       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                        ┌───────────────────────────────┼───────────────────────────────┐
                        │                               │                               │
                        ▼                               ▼                               ▼
                ┌─────────────────┐             ┌─────────────────┐             ┌─────────────────┐
                │   LLM Router    │             │   State Machine │             │   Database      │
                │   (Claude API)  │             │   (transitions) │             │   (SQLAlchemy)  │
                └─────────────────┘             └─────────────────┘             └─────────────────┘
```

## Components

### 1. State Machine (`state_machine/`)
- **InvoiceFSM**: Finite state machine for invoice lifecycle
- **States**: new → invoice_sent → awaiting_approval → approved → payment_pending → paid → closed
- **Transitions**: send_invoice, approve, reject, dispute, confirm_payment, close

### 2. LLM Router (`llm_router/`)
- **ClaudeLLMProvider**: Production Claude API integration with retry logic
- **MockLLMProvider**: Testing without API calls
- **Intent Classification**: check_status, request_approval, confirm_payment, etc.

### 3. Tools (`tools/`)
- **CheckInvoiceStatusTool**: Query invoice state
- **SendInvoiceTool**: Send invoice to customer
- **RequestApprovalTool**: Request customer approval
- **ApproveInvoiceTool**: Mark invoice as approved
- **RejectInvoiceTool**: Mark invoice as rejected
- **RequestPaymentTool**: Request payment
- **ConfirmPaymentTool**: Confirm payment received
- **CloseInvoiceTool**: Close the invoice

### 4. Agent (`agents/invoice_agent/`)
- **InvoiceOrchestrator**: E2E flow controller
- **AuditLog**: Append-only audit trail
- **EnhancedEventBus**: Event-driven architecture

### 5. WhatsApp Channel (`channels/whatsapp/`)
- **WhatsAppAdapter**: Message format conversion
- **WhatsAppSimulator**: Local testing without API

### 6. Webhook Server (`server/`)
- **FastAPI Application**: Webhook endpoint
- **WhatsApp Client**: Business API integration
- **Health Checks**: Monitoring endpoints

### 7. Database (`database/`)
- **SQLAlchemy Models**: invoices, customers, audit_log, conversations
- **DatabaseInvoiceStore**: Persistent storage implementation
- **Session Management**: Connection pooling and transactions

## Invoice States

| State | Description |
|-------|-------------|
| `new` | Initial state |
| `invoice_sent` | Invoice delivered to customer |
| `awaiting_approval` | Waiting for customer approval |
| `approved` | Customer approved |
| `rejected` | Customer rejected |
| `payment_pending` | Awaiting payment |
| `paid` | Payment confirmed |
| `disputed` | Under dispute |
| `closed` | Terminal state |

## Intents

| Intent | Description | Tools |
|--------|-------------|-------|
| `check_status` | Query invoice state | CheckInvoiceStatusTool |
| `request_approval` | Request approval | RequestApprovalTool |
| `approve` | Approve invoice | ApproveInvoiceTool |
| `reject` | Reject invoice | RejectInvoiceTool |
| `confirm_payment` | Confirm payment | ConfirmPaymentTool |
| `dispute` | Raise dispute | DisputeTool |
| `general_inquiry` | General questions | - |

## Setup

### Prerequisites
- Python 3.11+
- WhatsApp Business API access
- Anthropic API key (for Claude)

### Installation

```bash
# Clone repository
git clone https://github.com/MoyalShoham/whatsapp-agent.git
cd whatsapp-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[all]"

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Environment Variables

```env
# WhatsApp Business API
WHATSAPP_ACCESS_TOKEN=your_access_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_VERIFY_TOKEN=your_verify_token

# Claude API
ANTHROPIC_API_KEY=your_anthropic_key

# Database
DATABASE_URL=sqlite:///./invoices.db
# or: DATABASE_URL=postgresql://user:pass@host:5432/invoices
```

### Running

```bash
# Start webhook server
python -m server.run

# Or with uvicorn
uvicorn server.app:app --host 0.0.0.0 --port 8000

# Initialize database
python -c "from database import init_db; init_db()"
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/webhook` | WhatsApp verification |
| POST | `/webhook` | Receive messages |
| GET | `/health` | Health check |
| GET | `/invoices` | List invoices |
| POST | `/invoices` | Create invoice |
| GET | `/invoices/{id}` | Get invoice details |

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific tests
pytest tests/test_state_machine.py
pytest tests/test_tools.py
pytest tests/test_server.py
```

## Project Structure

```
whatsapp-agent/
├── agents/
│   └── invoice_agent/
│       ├── agent.py           # Base agent
│       ├── orchestrator.py    # E2E flow
│       └── infrastructure.py  # Events & audit
├── channels/
│   └── whatsapp/
│       ├── adapter.py         # Message adapter
│       └── simulator.py       # Local testing
├── database/
│   ├── models.py             # SQLAlchemy models
│   ├── session.py            # DB connections
│   ├── store.py              # Invoice store
│   └── invoice_data.py       # Line items & PDF
├── llm_router/
│   ├── router.py             # Main router
│   ├── providers.py          # LLM providers
│   ├── schemas.py            # Pydantic models
│   └── prompt.md             # System prompt
├── scheduler/
│   ├── __init__.py           # Module exports
│   └── tasks.py              # Background tasks
├── server/
│   ├── app.py                # FastAPI app
│   ├── config.py             # Settings
│   ├── whatsapp_client.py    # API client
│   └── run.py                # Entry point
├── state_machine/
│   ├── invoice_state.py      # FSM implementation
│   └── models.py             # State models
├── tools/
│   ├── base.py               # Base tool class
│   └── invoice_tools.py      # Invoice tools
├── tests/
│   ├── test_state_machine.py
│   ├── test_tools.py
│   ├── test_router.py
│   ├── test_server.py
│   ├── test_database.py
│   ├── test_scheduler.py
│   └── test_invoice_data.py
├── pyproject.toml            # Project config
├── SUMMARY.md                # This file
└── README.md                 # Quick start
```

## Development Status

- [x] State Machine Architecture
- [x] Invoice Tools (8 tools)
- [x] LLM Router with Claude
- [x] WhatsApp Channel Adapter
- [x] Webhook Server (FastAPI)
- [x] Database Storage (SQLAlchemy)
- [x] Invoice Data Model (amounts, line items, addresses)
- [x] Scheduled Tasks (reminders, follow-ups, overdue checks)
- [x] PDF Generation (with reportlab support)
- [ ] Rate Limiting
- [ ] Metrics & Monitoring

## License

MIT License - See LICENSE file
