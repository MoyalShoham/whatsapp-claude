#!/usr/bin/env python3
"""
WhatsApp Simulator - CLI interface for testing the Invoice Agent.

This simulator provides a terminal-based interface that mimics
WhatsApp messaging for testing the invoice automation flow.

Usage:
    python channels/whatsapp/simulator.py

Commands:
    /create INV-XXX  - Create a new invoice
    /state INV-XXX   - Check invoice state
    /advance INV-XXX trigger - Advance invoice state
    /context         - Show current context
    /clear           - Clear conversation context
    /help            - Show help
    exit             - Exit simulator
"""

import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from agents.invoice_agent import InvoiceOrchestrator
from channels.whatsapp.adapter import WhatsAppAdapter
from llm_router import LLMRouter, get_default_provider
from tools.base import InMemoryInvoiceStore


def print_header() -> None:
    """Print simulator header."""
    print("\n" + "=" * 60)
    print("📱 WhatsApp Invoice Agent Simulator")
    print("=" * 60)
    print("""
Commands:
  /create INV-XXX    - Create a new invoice
  /state INV-XXX     - Check invoice state
  /advance INV-XXX <trigger> - Advance state manually
  /list              - List all invoices
  /context           - Show current conversation context
  /clear             - Clear conversation context
  /help              - Show this help
  exit               - Exit simulator

Example flow:
  /create INV-001
  /advance INV-001 send_invoice
  /advance INV-001 request_approval
  I approve invoice INV-001
  I have paid invoice INV-001
""")
    print("=" * 60 + "\n")


def print_state_table(store: InMemoryInvoiceStore, invoice_id: str) -> None:
    """Print state table for an invoice."""
    fsm = store.get_fsm(invoice_id)
    if not fsm:
        print(f"❌ Invoice {invoice_id} not found")
        return

    print(f"\n┌{'─' * 50}┐")
    print(f"│ Invoice: {fsm.invoice_id:<40}│")
    print(f"├{'─' * 50}┤")
    print(f"│ Current State: {fsm.current_state:<34}│")
    print(f"│ Is Terminal: {str(fsm.is_terminal):<36}│")
    triggers = ", ".join(fsm.get_available_triggers()) or "none"
    print(f"│ Available: {triggers:<38}│")
    print(f"└{'─' * 50}┘\n")


def handle_command(
    cmd: str,
    adapter: WhatsAppAdapter,
    phone: str,
) -> bool:
    """
    Handle simulator commands.

    Returns:
        True if should continue, False if should exit.
    """
    parts = cmd.strip().split()
    command = parts[0].lower()

    if command == "exit":
        print("\n👋 Simulator closed. Goodbye!")
        return False

    elif command == "/help":
        print_header()

    elif command == "/create":
        if len(parts) < 2:
            print("❌ Usage: /create INV-XXX")
        else:
            invoice_id = parts[1].upper()
            adapter.create_invoice(invoice_id)
            print(f"✅ Created invoice: {invoice_id}")
            print_state_table(adapter.orchestrator.store, invoice_id)

    elif command == "/state":
        if len(parts) < 2:
            print("❌ Usage: /state INV-XXX")
        else:
            invoice_id = parts[1].upper()
            print_state_table(adapter.orchestrator.store, invoice_id)

    elif command == "/advance":
        if len(parts) < 3:
            print("❌ Usage: /advance INV-XXX <trigger>")
            print("   Triggers: send_invoice, request_approval, approve, reject,")
            print("             request_payment, confirm_payment, close, dispute")
        else:
            invoice_id = parts[1].upper()
            trigger = parts[2].lower()
            try:
                result = adapter.orchestrator.advance_invoice(
                    invoice_id=invoice_id,
                    trigger=trigger,
                    customer_id=phone,
                )
                print(f"✅ Transition: {result['previous_state']} → {result['current_state']}")
                print_state_table(adapter.orchestrator.store, invoice_id)
            except Exception as e:
                print(f"❌ Error: {e}")

    elif command == "/list":
        invoices = adapter.orchestrator.store.list_invoices()
        if not invoices:
            print("📭 No invoices found. Use /create INV-XXX to create one.")
        else:
            print("\n📋 Invoices:")
            for inv_id in invoices:
                fsm = adapter.orchestrator.store.get_fsm(inv_id)
                state = fsm.current_state if fsm else "unknown"
                print(f"   • {inv_id}: {state}")
            print()

    elif command == "/context":
        active_inv = adapter.get_active_invoice(phone)
        history = adapter._get_history(phone)
        print(f"\n📱 Phone: {phone}")
        print(f"📋 Active Invoice: {active_inv or 'None'}")
        print(f"💬 Messages in history: {len(history)}")
        if history:
            print("\nRecent messages:")
            for msg in history[-3:]:
                role = "You" if msg["role"] == "user" else "Bot"
                content = msg["content"][:50] + "..." if len(msg["content"]) > 50 else msg["content"]
                print(f"   {role}: {content}")
        print()

    elif command == "/clear":
        adapter.clear_context(phone)
        print("✅ Context cleared")

    elif command.startswith("/"):
        print(f"❓ Unknown command: {command}")
        print("   Type /help for available commands")

    else:
        # Not a command - treat as message
        return True  # Signal to process as message

    return True


def main() -> None:
    """Run the WhatsApp simulator."""
    print_header()

    # Initialize components
    print("🔧 Initializing...")

    store = InMemoryInvoiceStore()
    router = LLMRouter(llm_provider=get_default_provider())
    orchestrator = InvoiceOrchestrator(store=store, router=router)
    adapter = WhatsAppAdapter(orchestrator=orchestrator)

    phone = "+972500000000"

    print("✅ Ready!\n")
    print(f"📱 Simulating as: {phone}")
    print("💡 Tip: Start with /create INV-001 to create an invoice\n")

    while True:
        try:
            # Get input
            user_input = input("You (WhatsApp): ").strip()

            if not user_input:
                continue

            # Check if it's a command
            if user_input.startswith("/") or user_input.lower() == "exit":
                should_continue = handle_command(user_input, adapter, phone)
                if not should_continue:
                    break
                continue

            # Process as WhatsApp message
            timestamp = datetime.now().strftime("%H:%M")
            print(f"[{timestamp}] Sending...")

            response = adapter.handle_incoming(phone, user_input)

            # Print response with formatting
            print(f"\n🤖 Bot [{timestamp}]:")
            for line in response.split("\n"):
                print(f"   {line}")
            print()

        except KeyboardInterrupt:
            print("\n\n👋 Interrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}\n")


if __name__ == "__main__":
    main()
