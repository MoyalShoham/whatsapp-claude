"""
Conversational Invoice Agent - The ONLY LLM-based agent for invoice management.

This agent:
- Loads business rules from agent_prompt.md (single source of truth)
- Parses tool calls from Claude's responses
- Delegates tool execution to InvoiceOrchestrator (FSM validator)

Used by both SIMULATOR and PRODUCTION modes.
The difference is configuration only, not code path.
"""

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AgentMode(Enum):
    """Agent execution mode."""
    SIMULATOR = "simulator"  # Local testing, no external calls
    PRODUCTION = "production"  # Real WhatsApp API calls


class ConversationalAgent:
    """
    The single LLM-based agent for invoice management.

    This agent:
    - Loads prompt from agent_prompt.md (single source of truth)
    - Uses Claude for natural language understanding
    - Delegates all FSM operations to InvoiceOrchestrator
    - Works in both SIMULATOR and PRODUCTION modes

    Business rules live in agent_prompt.md, NOT in code.
    """

    # Tool name to FSM trigger mapping
    TOOL_TO_TRIGGER: dict[str, str] = {
        "approve_invoice": "approve",
        "reject_invoice": "reject",
        "confirm_payment": "confirm_payment",
        "create_dispute": "dispute",
        "close_invoice": "close",
    }

    def __init__(
        self,
        orchestrator: Any,
        llm_provider: Any,
        mode: AgentMode = AgentMode.SIMULATOR,
        prompt_path: Optional[Path] = None,
    ):
        """
        Initialize the conversational agent.

        Args:
            orchestrator: InvoiceOrchestrator for FSM operations.
            llm_provider: LLM provider (ClaudeLLMProvider).
            mode: SIMULATOR or PRODUCTION mode.
            prompt_path: Path to agent prompt template.
        """
        self.orchestrator = orchestrator
        self.llm_provider = llm_provider
        self.mode = mode
        self.prompt_path = prompt_path or Path(__file__).parent.parent / "llm_router" / "agent_prompt.md"
        self._prompt_template: Optional[str] = None

        logger.info(f"ConversationalAgent initialized in {mode.value} mode")

    @property
    def prompt_template(self) -> str:
        """Load and cache prompt template from agent_prompt.md."""
        if self._prompt_template is None:
            self._prompt_template = self.prompt_path.read_text(encoding="utf-8")
            logger.debug(f"Loaded prompt template from {self.prompt_path}")
        return self._prompt_template

    def process_message(
        self,
        message: str,
        customer_id: str,
        context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Process a user message and return a natural response.

        This is the main entry point for both SIMULATOR and PRODUCTION.

        Args:
            message: User's message.
            customer_id: Customer identifier (phone number).
            context: Additional context (conversation history, etc).

        Returns:
            Natural language response.
        """
        context = context or {}

        # Build context string for the prompt
        context_str = self._build_context(customer_id, context)

        # Build prompt from template
        prompt = self.prompt_template.replace("{{user_message}}", message)
        prompt = prompt.replace("{{context}}", context_str)

        # Call LLM
        try:
            response = self.llm_provider.complete(prompt)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return "Sorry, I'm having trouble processing your request. Please try again."

        # Parse and execute any tool calls
        final_response = self._process_response(response, customer_id)

        return final_response

    def _build_context(self, customer_id: str, context: dict[str, Any]) -> str:
        """Build context string for the prompt."""
        lines = [f"Customer ID: {customer_id}"]

        # Get customer's invoices from orchestrator
        invoices = self.orchestrator.list_invoices()
        if invoices:
            lines.append(f"Total invoices in system: {len(invoices)}")

            # List active (non-closed) invoices
            active = []
            for inv in invoices:
                if inv["state"] != "closed":
                    active.append(f"  - {inv['invoice_id']}: {inv['state']}")

            if active:
                lines.append("Active invoices:")
                lines.extend(active)
            else:
                lines.append("No active invoices")
        else:
            lines.append("No invoices found")

        # Add conversation history if available
        if context.get("conversation_history"):
            lines.append("\nRecent conversation:")
            for msg in context["conversation_history"][-3:]:
                role = "User" if msg.get("role") == "user" else "Agent"
                content = msg.get("content", "")[:100]
                lines.append(f"  {role}: {content}")

        return "\n".join(lines)

    def _process_response(self, response: str, customer_id: str) -> str:
        """
        Process LLM response, execute any tool calls, and return final response.
        """
        # Pattern to find tool calls: [TOOL: name]{...}[/TOOL]
        tool_pattern = r'\[TOOL:\s*(\w+)\]\s*(\{[^}]+\})\s*\[/TOOL\]'

        matches = list(re.finditer(tool_pattern, response, re.DOTALL))

        if not matches:
            # No tool calls, return response as-is (clean up any artifacts)
            return self._clean_response(response)

        # Execute tool calls and build final response
        final_response = response

        for match in matches:
            tool_name = match.group(1).strip()
            tool_args_str = match.group(2).strip()

            try:
                tool_args = json.loads(tool_args_str)
            except json.JSONDecodeError:
                logger.warning(f"Invalid tool args: {tool_args_str}")
                continue

            # Execute tool through orchestrator
            tool_result = self._execute_tool(tool_name, tool_args, customer_id)

            # Replace tool call with result
            tool_call_text = match.group(0)
            result_text = self._format_tool_result(tool_name, tool_result)
            final_response = final_response.replace(tool_call_text, result_text)

        return self._clean_response(final_response)

    def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        customer_id: str,
    ) -> dict[str, Any]:
        """
        Execute a tool through the orchestrator.

        The orchestrator handles FSM validation and audit.
        """
        invoice_id = args.get("invoice_id", "")
        reason = args.get("reason", "")

        try:
            # Query tools (no state change)
            if tool_name == "list_invoices":
                invoices = self.orchestrator.list_invoices(
                    state_filter=args.get("state_filter")
                )
                return {
                    "success": True,
                    "message": f"Found {len(invoices)} invoice(s)",
                    "data": {"invoices": invoices},
                }

            elif tool_name == "get_invoice_status":
                if not invoice_id:
                    return {"success": False, "error": "Invoice ID required"}

                fsm = self.orchestrator.get_invoice(invoice_id)
                if not fsm:
                    return {"success": False, "error": f"Invoice {invoice_id} not found"}

                return {
                    "success": True,
                    "message": f"Invoice {invoice_id} is in state {fsm.current_state}",
                    "data": {
                        "invoice_id": invoice_id,
                        "current_state": fsm.current_state,
                        "available_actions": fsm.get_available_triggers(),
                    },
                }

            # State-changing tools - delegate to orchestrator
            trigger = self.TOOL_TO_TRIGGER.get(tool_name)
            if trigger:
                if not invoice_id:
                    return {"success": False, "error": "Invoice ID required"}

                result = self.orchestrator.execute_transition(
                    invoice_id=invoice_id,
                    trigger=trigger,
                    customer_id=customer_id,
                    reason=reason,
                )

                return {
                    "success": result.success,
                    "message": result.message,
                    "error": result.error,
                    "data": {
                        "invoice_id": result.invoice_id,
                        "previous_state": result.previous_state,
                        "current_state": result.current_state,
                    },
                }

            # Unknown tool
            logger.warning(f"Unknown tool: {tool_name}")
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"success": False, "error": str(e)}

    def _format_tool_result(self, tool_name: str, result: dict[str, Any]) -> str:
        """Format tool result for inclusion in response."""
        if not result.get("success", False):
            error = result.get("error", "Unknown error")
            return f"(Unable to complete action: {error})"

        # Format based on tool type
        if tool_name == "list_invoices":
            invoices = result.get("data", {}).get("invoices", [])
            if not invoices:
                return "You don't have any invoices at the moment."

            lines = [f"You have {len(invoices)} invoice(s):"]
            for inv in invoices:
                state = inv["state"].replace("_", " ")
                lines.append(f"  - {inv['invoice_id']}: {state}")
            return "\n".join(lines)

        elif tool_name == "get_invoice_status":
            data = result.get("data", {})
            state = data.get("current_state", "unknown").replace("_", " ")
            inv_id = data.get("invoice_id", "")
            return f"Invoice {inv_id} is currently: {state}"

        else:
            # Generic success message from orchestrator
            return result.get("message", "Done!")

    def _clean_response(self, response: str) -> str:
        """Clean up response text."""
        # Remove any remaining tool artifacts
        response = re.sub(r'\[TOOL:.*?\[/TOOL\]', '', response, flags=re.DOTALL)

        # Clean up extra whitespace
        response = re.sub(r'\n{3,}', '\n\n', response)
        response = response.strip()

        return response

    # ========== Convenience Methods ==========

    def create_invoice(self, invoice_id: str, customer_id: Optional[str] = None) -> Any:
        """Create a new invoice (delegates to orchestrator)."""
        return self.orchestrator.create_invoice(invoice_id, customer_id)

    def get_invoice_state(self, invoice_id: str) -> Optional[str]:
        """Get invoice state (delegates to orchestrator)."""
        return self.orchestrator.get_invoice_state(invoice_id)
