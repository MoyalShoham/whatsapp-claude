"""
Conversational Invoice Agent - Natural language interface for invoice management.

This agent uses Claude to have natural conversations while managing invoices.
It parses tool calls from Claude's responses and executes them.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ConversationalAgent:
    """
    A conversational agent that manages invoices through natural dialogue.

    Unlike the JSON router approach, this agent:
    - Responds naturally in conversation
    - Embeds tool calls in responses when actions are needed
    - Handles context and state automatically
    """

    def __init__(
        self,
        store: Any,
        llm_provider: Any,
        prompt_path: Optional[Path] = None,
    ):
        """
        Initialize the conversational agent.

        Args:
            store: Invoice store instance
            llm_provider: LLM provider (ClaudeLLMProvider)
            prompt_path: Path to agent prompt template
        """
        self.store = store
        self.llm_provider = llm_provider
        self.prompt_path = prompt_path or Path(__file__).parent.parent / "llm_router" / "agent_prompt.md"
        self._prompt_template: Optional[str] = None

        # Tool implementations
        self._tools = {
            "list_invoices": self._tool_list_invoices,
            "get_invoice_status": self._tool_get_invoice_status,
            "approve_invoice": self._tool_approve_invoice,
            "reject_invoice": self._tool_reject_invoice,
            "confirm_payment": self._tool_confirm_payment,
            "create_dispute": self._tool_create_dispute,
            "close_invoice": self._tool_close_invoice,
        }

    @property
    def prompt_template(self) -> str:
        """Load and cache prompt template."""
        if self._prompt_template is None:
            self._prompt_template = self.prompt_path.read_text(encoding="utf-8")
        return self._prompt_template

    def process_message(
        self,
        message: str,
        customer_id: str,
        context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Process a user message and return a natural response.

        Args:
            message: User's message
            customer_id: Customer identifier (phone number)
            context: Additional context

        Returns:
            Natural language response
        """
        context = context or {}

        # Build context string
        context_str = self._build_context(customer_id, context)

        # Build prompt
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

        # Get customer's invoices
        all_invoices = self.store.list_invoices()
        if all_invoices:
            lines.append(f"Total invoices in system: {len(all_invoices)}")

            # List active (non-closed) invoices
            active = []
            for inv_id in all_invoices:
                fsm = self.store.get_fsm(inv_id)
                if fsm and fsm.current_state != "closed":
                    active.append(f"  - {inv_id}: {fsm.current_state}")

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

            # Execute tool
            tool_result = self._execute_tool(tool_name, tool_args)

            # Replace tool call with result
            tool_call_text = match.group(0)
            result_text = self._format_tool_result(tool_name, tool_result)
            final_response = final_response.replace(tool_call_text, result_text)

        return self._clean_response(final_response)

    def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool and return the result."""
        tool_fn = self._tools.get(tool_name)

        if not tool_fn:
            logger.warning(f"Unknown tool: {tool_name}")
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        try:
            return tool_fn(**args)
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
                lines.append(f"• {inv['invoice_id']}: {state}")
            return "\n".join(lines)

        elif tool_name == "get_invoice_status":
            data = result.get("data", {})
            state = data.get("current_state", "unknown").replace("_", " ")
            inv_id = data.get("invoice_id", "")
            return f"Invoice {inv_id} is currently: {state}"

        else:
            # Generic success message
            return result.get("message", "Done!")

    def _clean_response(self, response: str) -> str:
        """Clean up response text."""
        # Remove any remaining tool artifacts
        response = re.sub(r'\[TOOL:.*?\[/TOOL\]', '', response, flags=re.DOTALL)

        # Clean up extra whitespace
        response = re.sub(r'\n{3,}', '\n\n', response)
        response = response.strip()

        return response

    # ========== Tool Implementations ==========

    def _tool_list_invoices(
        self,
        state_filter: Optional[str] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """List all invoices."""
        all_ids = self.store.list_invoices()

        invoices = []
        for inv_id in all_ids:
            fsm = self.store.get_fsm(inv_id)
            if fsm:
                if state_filter and fsm.current_state != state_filter:
                    continue
                invoices.append({
                    "invoice_id": inv_id,
                    "state": fsm.current_state,
                    "is_terminal": fsm.is_terminal,
                })

        return {
            "success": True,
            "message": f"Found {len(invoices)} invoice(s)",
            "data": {"invoices": invoices},
        }

    def _tool_get_invoice_status(
        self,
        invoice_id: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Get invoice status."""
        fsm = self.store.get_fsm(invoice_id)
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

    def _tool_approve_invoice(
        self,
        invoice_id: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Approve an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return {"success": False, "error": f"Invoice {invoice_id} not found"}

        if fsm.current_state != "awaiting_approval":
            return {
                "success": False,
                "error": f"Cannot approve - invoice is in '{fsm.current_state}' state",
            }

        try:
            fsm.trigger("approve")
            self.store.save_fsm(fsm)
            return {
                "success": True,
                "message": f"Invoice {invoice_id} has been approved!",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_reject_invoice(
        self,
        invoice_id: str,
        reason: str = "",
        **kwargs,
    ) -> dict[str, Any]:
        """Reject an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return {"success": False, "error": f"Invoice {invoice_id} not found"}

        if fsm.current_state != "awaiting_approval":
            return {
                "success": False,
                "error": f"Cannot reject - invoice is in '{fsm.current_state}' state",
            }

        try:
            fsm.trigger("reject")
            self.store.save_fsm(fsm)
            return {
                "success": True,
                "message": f"Invoice {invoice_id} has been rejected.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_confirm_payment(
        self,
        invoice_id: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Confirm payment for an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return {"success": False, "error": f"Invoice {invoice_id} not found"}

        if fsm.current_state != "payment_pending":
            return {
                "success": False,
                "error": f"Cannot confirm payment - invoice is in '{fsm.current_state}' state",
            }

        try:
            fsm.trigger("confirm_payment")
            self.store.save_fsm(fsm)
            return {
                "success": True,
                "message": f"Payment confirmed for invoice {invoice_id}. Thank you!",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_create_dispute(
        self,
        invoice_id: str,
        reason: str = "",
        **kwargs,
    ) -> dict[str, Any]:
        """Create a dispute for an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return {"success": False, "error": f"Invoice {invoice_id} not found"}

        if not fsm.can_trigger("dispute"):
            return {
                "success": False,
                "error": f"Cannot create dispute from '{fsm.current_state}' state",
            }

        try:
            fsm.trigger("dispute")
            self.store.save_fsm(fsm)
            return {
                "success": True,
                "message": f"Dispute created for invoice {invoice_id}. We'll review it shortly.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_close_invoice(
        self,
        invoice_id: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Close an invoice."""
        fsm = self.store.get_fsm(invoice_id)
        if not fsm:
            return {"success": False, "error": f"Invoice {invoice_id} not found"}

        if not fsm.can_trigger("close"):
            return {
                "success": False,
                "error": f"Cannot close - invoice must be 'paid' or 'rejected' first",
            }

        try:
            fsm.trigger("close")
            self.store.save_fsm(fsm)
            return {
                "success": True,
                "message": f"Invoice {invoice_id} has been closed.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
