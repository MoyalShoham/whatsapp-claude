#!/usr/bin/env python3
"""
WhatsApp Simulator - CLI interface for testing the Invoice Agent.

This simulator uses the SAME ConversationalAgent as production.
The only difference is configuration (AgentMode.SIMULATOR).

Usage:
    python channels/whatsapp/simulator.py

Commands:
    /create INV-XXX  - Create a new invoice
    /state INV-XXX   - Check invoice state
    /advance INV-XXX trigger - Advance invoice state
    /context         - Show current context
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

from agents.conversational_agent import ConversationalAgent, AgentMode
from agents.invoice_agent import InvoiceOrchestrator
from llm_router import get_default_provider
from tools.base import InMemoryInvoiceStore


def print_header() -> None:
    """Print simulator header."""
    print("\n" + "=" * 60)
    print("WhatsApp Invoice Agent Simulator")
    print("=" * 60)
    print("""
Commands:
  /create INV-XXX    - Create a new invoice
  /state INV-XXX     - Check invoice state
  /advance INV-XXX <trigger> - Advance state manually
  /list              - List all invoices
  /context           - Show current context
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


def print_state_table(orchestrator: InvoiceOrchestrator, invoice_id: str) -> None:
    """Print state table for an invoice."""
    fsm = orchestrator.get_invoice(invoice_id)
    if not fsm:
        print(f"Invoice {invoice_id} not found")
        return

    triggers = ", ".join(fsm.get_available_triggers()) or "none"
    print(f"\n+{'-' * 50}+")
    print(f"| Invoice: {fsm.invoice_id:<40}|")
    print(f"+{'-' * 50}+")
    print(f"| Current State: {fsm.current_state:<34}|")
    print(f"| Is Terminal: {str(fsm.is_terminal):<36}|")
    print(f"| Available: {triggers:<38}|")
    print(f"+{'-' * 50}+\n")


def handle_command(
    cmd: str,
    orchestrator: InvoiceOrchestrator,
    phone: str,
) -> bool:
    """
    Handle simulator commands.

    Args:
        cmd: The command string.
        orchestrator: Invoice orchestrator for direct FSM operations.
        phone: Simulated phone number.

    Returns:
        True if should continue, False if should exit.
    """
    parts = cmd.strip().split()
    command = parts[0].lower()

    if command == "exit":
        print("\nSimulator closed. Goodbye!")
        return False

    elif command == "/help":
        print_header()

    elif command == "/create":
        if len(parts) < 2:
            print("Usage: /create INV-XXX")
        else:
            invoice_id = parts[1].upper()
            orchestrator.create_invoice(invoice_id)
            print(f"Created invoice: {invoice_id}")
            print_state_table(orchestrator, invoice_id)

    elif command == "/state":
        if len(parts) < 2:
            print("Usage: /state INV-XXX")
        else:
            invoice_id = parts[1].upper()
            print_state_table(orchestrator, invoice_id)

    elif command == "/advance":
        if len(parts) < 3:
            print("Usage: /advance INV-XXX <trigger>")
            print("   Triggers: send_invoice, request_approval, approve, reject,")
            print("             request_payment, confirm_payment, close, dispute")
        else:
            invoice_id = parts[1].upper()
            trigger = parts[2].lower()
            result = orchestrator.execute_transition(
                invoice_id=invoice_id,
                trigger=trigger,
                customer_id=phone,
            )
            if result.success:
                print(f"Transition: {result.previous_state} -> {result.current_state}")
                print_state_table(orchestrator, invoice_id)
            else:
                print(f"Error: {result.error}")

    elif command == "/list":
        invoices = orchestrator.list_invoices()
        if not invoices:
            print("No invoices found. Use /create INV-XXX to create one.")
        else:
            print("\nInvoices:")
            for inv in invoices:
                print(f"   - {inv['invoice_id']}: {inv['state']}")
            print()

    elif command == "/context":
        print(f"\nPhone: {phone}")
        invoices = orchestrator.list_invoices()
        if invoices:
            print(f"Total invoices: {len(invoices)}")
            active = [i for i in invoices if i["state"] != "closed"]
            print(f"Active invoices: {len(active)}")
        else:
            print("No invoices")
        print()

    elif command.startswith("/"):
        print(f"Unknown command: {command}")
        print("   Type /help for available commands")

    else:
        # Not a command - treat as message
        return True  # Signal to process as message

    return True


def main() -> None:
    """Run the WhatsApp simulator."""
    print_header()

    # Initialize components
    print("Initializing...")

    # Create shared store
    store = InMemoryInvoiceStore()

    # Create orchestrator (FSM validator + tool executor)
    orchestrator = InvoiceOrchestrator(store=store)

    # Get LLM provider
    provider = get_default_provider()
    provider_name = type(provider).__name__
    print(f"LLM Provider: {provider_name}")

    if provider_name == "ClaudeLLMProvider":
        print("   Using Claude API for natural language understanding")
    else:
        print("   Using pattern-based fallback (set ANTHROPIC_API_KEY for Claude)")

    # Create conversational agent in SIMULATOR mode
    agent = ConversationalAgent(
        orchestrator=orchestrator,
        llm_provider=provider,
        mode=AgentMode.SIMULATOR,
    )

    phone = "+972500000000"

    print("Ready!\n")
    print(f"Simulating as: {phone}")
    print("Tip: Start with /create INV-001 to create an invoice\n")

    while True:
        try:
            # Get input
            user_input = input("You (WhatsApp): ").strip()

            if not user_input:
                continue

            # Check if it's a command
            if user_input.startswith("/") or user_input.lower() == "exit":
                should_continue = handle_command(user_input, orchestrator, phone)
                if not should_continue:
                    break
                continue

            # Process as WhatsApp message through the agent
            timestamp = datetime.now().strftime("%H:%M")
            print(f"[{timestamp}] Sending...")

            response = agent.process_message(user_input, phone)

            # Print response with formatting
            print(f"\nBot [{timestamp}]:")
            for line in response.split("\n"):
                print(f"   {line}")
            print()

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
