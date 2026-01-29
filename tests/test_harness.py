#!/usr/bin/env python3
"""
Invoice Agent Test Harness

This script provides a comprehensive test harness that:
- Simulates full end-to-end flows
- Prints state tables
- Verifies guard rails
- Tests forbidden transitions
- Demonstrates dispute reopening flow

Run with: python -m tests.test_harness
"""

import sys
from dataclasses import dataclass
from typing import Callable

from agents.invoice_agent import InvoiceAgent
from state_machine.invoice_state import InvoiceFSM, InvoiceState, TransitionError
from tools.base import InMemoryInvoiceStore


# ============================================================================
# Test Harness Utilities
# ============================================================================


@dataclass
class TestResult:
    """Result of a single test."""

    name: str
    passed: bool
    message: str


def print_header(title: str) -> None:
    """Print a section header."""
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70)


def print_state_table(fsm: InvoiceFSM) -> None:
    """Print current state table for an invoice."""
    print(f"\n┌{'─' * 50}┐")
    print(f"│ Invoice: {fsm.invoice_id:<40}│")
    print(f"├{'─' * 50}┤")
    print(f"│ Current State: {fsm.current_state:<34}│")
    print(f"│ Is Terminal: {str(fsm.is_terminal):<36}│")
    print(f"│ Available Triggers: {', '.join(fsm.get_available_triggers()):<29}│")
    print(f"└{'─' * 50}┘")


def print_history_table(fsm: InvoiceFSM) -> None:
    """Print transition history table."""
    print(f"\n┌{'─' * 70}┐")
    print(f"│ Transition History for {fsm.invoice_id:<46}│")
    print(f"├{'─' * 20}┬{'─' * 20}┬{'─' * 28}┤")
    print(f"│ {'From':<18} │ {'To':<18} │ {'Trigger':<26} │")
    print(f"├{'─' * 20}┼{'─' * 20}┼{'─' * 28}┤")
    for entry in fsm.history:
        source = entry["source"] or "-"
        dest = entry["dest"]
        trigger = entry["trigger"]
        print(f"│ {source:<18} │ {dest:<18} │ {trigger:<26} │")
    print(f"└{'─' * 20}┴{'─' * 20}┴{'─' * 28}┘")


def print_test_result(result: TestResult) -> None:
    """Print a single test result."""
    status = "✓ PASS" if result.passed else "✗ FAIL"
    print(f"  {status}: {result.name}")
    if not result.passed:
        print(f"         {result.message}")


# ============================================================================
# Test Cases
# ============================================================================


def test_happy_path_flow() -> TestResult:
    """Test the complete happy path: new -> closed."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-001")

        transitions = [
            ("send_invoice", InvoiceState.INVOICE_SENT),
            ("request_approval", InvoiceState.AWAITING_APPROVAL),
            ("approve", InvoiceState.APPROVED),
            ("request_payment", InvoiceState.PAYMENT_PENDING),
            ("confirm_payment", InvoiceState.PAID),
            ("close", InvoiceState.CLOSED),
        ]

        for trigger, expected_state in transitions:
            fsm.trigger(trigger)
            if fsm.current_state != expected_state:
                return TestResult(
                    "Happy Path Flow",
                    False,
                    f"Expected {expected_state}, got {fsm.current_state}",
                )

        if not fsm.is_terminal:
            return TestResult("Happy Path Flow", False, "Final state should be terminal")

        return TestResult("Happy Path Flow", True, "All transitions successful")
    except Exception as e:
        return TestResult("Happy Path Flow", False, str(e))


def test_rejection_flow() -> TestResult:
    """Test the rejection flow: new -> rejected -> closed."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-002")

        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        fsm.trigger("reject")

        if fsm.current_state != InvoiceState.REJECTED:
            return TestResult(
                "Rejection Flow",
                False,
                f"Expected rejected, got {fsm.current_state}",
            )

        fsm.trigger("close")

        if fsm.current_state != InvoiceState.CLOSED:
            return TestResult(
                "Rejection Flow",
                False,
                f"Expected closed, got {fsm.current_state}",
            )

        return TestResult("Rejection Flow", True, "Rejection flow successful")
    except Exception as e:
        return TestResult("Rejection Flow", False, str(e))


def test_dispute_from_approved() -> TestResult:
    """Test dispute can be raised from approved state."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-003", initial_state=InvoiceState.APPROVED)

        fsm.trigger("dispute")

        if fsm.current_state != InvoiceState.DISPUTED:
            return TestResult(
                "Dispute from Approved",
                False,
                f"Expected disputed, got {fsm.current_state}",
            )

        return TestResult("Dispute from Approved", True, "Dispute created successfully")
    except Exception as e:
        return TestResult("Dispute from Approved", False, str(e))


def test_dispute_from_payment_pending() -> TestResult:
    """Test dispute can be raised during payment pending."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-004", initial_state=InvoiceState.PAYMENT_PENDING)

        fsm.trigger("dispute")

        if fsm.current_state != InvoiceState.DISPUTED:
            return TestResult(
                "Dispute from Payment Pending",
                False,
                f"Expected disputed, got {fsm.current_state}",
            )

        return TestResult("Dispute from Payment Pending", True, "Dispute created successfully")
    except Exception as e:
        return TestResult("Dispute from Payment Pending", False, str(e))


def test_dispute_from_paid() -> TestResult:
    """Test dispute can be raised after payment."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-005", initial_state=InvoiceState.PAID)

        fsm.trigger("dispute")

        if fsm.current_state != InvoiceState.DISPUTED:
            return TestResult(
                "Dispute from Paid",
                False,
                f"Expected disputed, got {fsm.current_state}",
            )

        return TestResult("Dispute from Paid", True, "Dispute created successfully")
    except Exception as e:
        return TestResult("Dispute from Paid", False, str(e))


def test_dispute_resolution_reopens_flow() -> TestResult:
    """Test that resolving dispute reopens the approval flow."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-006", initial_state=InvoiceState.DISPUTED)

        fsm.trigger("resolve_dispute")

        if fsm.current_state != InvoiceState.AWAITING_APPROVAL:
            return TestResult(
                "Dispute Resolution Reopens Flow",
                False,
                f"Expected awaiting_approval, got {fsm.current_state}",
            )

        # Should be able to approve again
        fsm.trigger("approve")

        if fsm.current_state != InvoiceState.APPROVED:
            return TestResult(
                "Dispute Resolution Reopens Flow",
                False,
                "Could not re-approve after dispute resolution",
            )

        return TestResult(
            "Dispute Resolution Reopens Flow",
            True,
            "Dispute resolution successfully reopens approval flow",
        )
    except Exception as e:
        return TestResult("Dispute Resolution Reopens Flow", False, str(e))


def test_full_dispute_cycle() -> TestResult:
    """Test complete dispute cycle: approve -> dispute -> resolve -> approve -> pay."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-007", initial_state=InvoiceState.AWAITING_APPROVAL)

        # First approval
        fsm.trigger("approve")

        # Dispute
        fsm.trigger("dispute")

        # Resolve
        fsm.trigger("resolve_dispute")

        # Re-approve
        fsm.trigger("approve")

        # Complete payment
        fsm.trigger("request_payment")
        fsm.trigger("confirm_payment")

        if fsm.current_state != InvoiceState.PAID:
            return TestResult(
                "Full Dispute Cycle",
                False,
                f"Expected paid, got {fsm.current_state}",
            )

        return TestResult("Full Dispute Cycle", True, "Complete dispute cycle successful")
    except Exception as e:
        return TestResult("Full Dispute Cycle", False, str(e))


# ============================================================================
# Forbidden Transition Tests
# ============================================================================


def test_forbidden_approve_from_new() -> TestResult:
    """Test that approve is blocked from 'new' state."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-F01")
        fsm.trigger("approve")
        return TestResult(
            "Block: Approve from New",
            False,
            "Should have raised TransitionError",
        )
    except TransitionError:
        return TestResult("Block: Approve from New", True, "Correctly blocked")
    except Exception as e:
        return TestResult("Block: Approve from New", False, str(e))


def test_forbidden_payment_before_approval() -> TestResult:
    """Test that payment confirmation is blocked before approval."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-F02", initial_state=InvoiceState.AWAITING_APPROVAL)
        fsm.trigger("confirm_payment")
        return TestResult(
            "Block: Payment Before Approval",
            False,
            "Should have raised TransitionError",
        )
    except TransitionError:
        return TestResult("Block: Payment Before Approval", True, "Correctly blocked")
    except Exception as e:
        return TestResult("Block: Payment Before Approval", False, str(e))


def test_forbidden_payment_without_request() -> TestResult:
    """Test that payment confirmation is blocked without payment request."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-F03", initial_state=InvoiceState.APPROVED)
        fsm.trigger("confirm_payment")
        return TestResult(
            "Block: Payment Without Request",
            False,
            "Should have raised TransitionError",
        )
    except TransitionError:
        return TestResult("Block: Payment Without Request", True, "Correctly blocked")
    except Exception as e:
        return TestResult("Block: Payment Without Request", False, str(e))


def test_forbidden_dispute_from_new() -> TestResult:
    """Test that dispute is blocked from 'new' state."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-F04")
        fsm.trigger("dispute")
        return TestResult(
            "Block: Dispute from New",
            False,
            "Should have raised TransitionError",
        )
    except TransitionError:
        return TestResult("Block: Dispute from New", True, "Correctly blocked")
    except Exception as e:
        return TestResult("Block: Dispute from New", False, str(e))


def test_forbidden_transition_from_closed() -> TestResult:
    """Test that all transitions are blocked from closed state."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-F05", initial_state=InvoiceState.CLOSED)
        fsm.trigger("send_invoice")
        return TestResult(
            "Block: Transition from Closed",
            False,
            "Should have raised TransitionError",
        )
    except TransitionError as e:
        if "terminal" in str(e).lower():
            return TestResult("Block: Transition from Closed", True, "Correctly blocked")
        return TestResult(
            "Block: Transition from Closed",
            False,
            f"Wrong error: {e}",
        )
    except Exception as e:
        return TestResult("Block: Transition from Closed", False, str(e))


def test_forbidden_close_from_new() -> TestResult:
    """Test that close is blocked from 'new' state."""
    try:
        fsm = InvoiceFSM(invoice_id="TEST-F06")
        fsm.trigger("close")
        return TestResult(
            "Block: Close from New",
            False,
            "Should have raised TransitionError",
        )
    except TransitionError:
        return TestResult("Block: Close from New", True, "Correctly blocked")
    except Exception as e:
        return TestResult("Block: Close from New", False, str(e))


# ============================================================================
# Agent Integration Tests
# ============================================================================


def test_agent_approval_flow() -> TestResult:
    """Test agent handles approval flow correctly."""
    try:
        store = InMemoryInvoiceStore()
        agent = InvoiceAgent(store=store)

        # Create invoice in awaiting_approval state
        fsm = store.create_invoice("AGENT-001")
        fsm.trigger("send_invoice")
        fsm.trigger("request_approval")
        store.save_fsm(fsm)

        # Process approval message
        response = agent.process_message("I approve invoice AGENT-001")

        if response.action_taken != "approve":
            return TestResult(
                "Agent Approval Flow",
                False,
                f"Expected action 'approve', got {response.action_taken}",
            )

        if response.current_state != "approved":
            return TestResult(
                "Agent Approval Flow",
                False,
                f"Expected state 'approved', got {response.current_state}",
            )

        return TestResult("Agent Approval Flow", True, "Agent correctly processed approval")
    except Exception as e:
        return TestResult("Agent Approval Flow", False, str(e))


def test_agent_blocks_invalid_transitions() -> TestResult:
    """Test agent blocks invalid transitions."""
    try:
        store = InMemoryInvoiceStore()
        agent = InvoiceAgent(store=store)

        # Create invoice in 'new' state
        store.create_invoice("AGENT-002")

        # Try to approve (should fail gracefully)
        response = agent.process_message("I approve invoice AGENT-002")

        if response.action_taken is not None:
            return TestResult(
                "Agent Blocks Invalid Transition",
                False,
                f"Should not have taken action, but took: {response.action_taken}",
            )

        if "cannot be approved" not in response.message.lower():
            return TestResult(
                "Agent Blocks Invalid Transition",
                False,
                "Expected helpful error message",
            )

        return TestResult(
            "Agent Blocks Invalid Transition",
            True,
            "Agent correctly blocked invalid transition",
        )
    except Exception as e:
        return TestResult("Agent Blocks Invalid Transition", False, str(e))


# ============================================================================
# Main Test Runner
# ============================================================================


def run_all_tests() -> None:
    """Run all tests and print results."""
    print_header("INVOICE AGENT TEST HARNESS")

    # Normal flow tests
    print_header("Normal Flow Tests")
    normal_tests = [
        test_happy_path_flow,
        test_rejection_flow,
    ]

    normal_results = []
    for test in normal_tests:
        result = test()
        normal_results.append(result)
        print_test_result(result)

    # Dispute flow tests
    print_header("Dispute Flow Tests")
    dispute_tests = [
        test_dispute_from_approved,
        test_dispute_from_payment_pending,
        test_dispute_from_paid,
        test_dispute_resolution_reopens_flow,
        test_full_dispute_cycle,
    ]

    dispute_results = []
    for test in dispute_tests:
        result = test()
        dispute_results.append(result)
        print_test_result(result)

    # Forbidden transition tests
    print_header("Guard Rail Tests (Forbidden Transitions)")
    forbidden_tests = [
        test_forbidden_approve_from_new,
        test_forbidden_payment_before_approval,
        test_forbidden_payment_without_request,
        test_forbidden_dispute_from_new,
        test_forbidden_transition_from_closed,
        test_forbidden_close_from_new,
    ]

    forbidden_results = []
    for test in forbidden_tests:
        result = test()
        forbidden_results.append(result)
        print_test_result(result)

    # Agent integration tests
    print_header("Agent Integration Tests")
    agent_tests = [
        test_agent_approval_flow,
        test_agent_blocks_invalid_transitions,
    ]

    agent_results = []
    for test in agent_tests:
        result = test()
        agent_results.append(result)
        print_test_result(result)

    # Demo: State Table
    print_header("Demo: State Table Visualization")
    demo_fsm = InvoiceFSM(invoice_id="DEMO-001")
    demo_fsm.trigger("send_invoice")
    demo_fsm.trigger("request_approval")
    demo_fsm.trigger("approve")
    print_state_table(demo_fsm)
    print_history_table(demo_fsm)

    # Summary
    print_header("Test Summary")
    all_results = normal_results + dispute_results + forbidden_results + agent_results
    passed = sum(1 for r in all_results if r.passed)
    total = len(all_results)
    failed = total - passed

    print(f"\n  Total Tests: {total}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")

    if failed == 0:
        print("\n  🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n  ⚠️  Some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
