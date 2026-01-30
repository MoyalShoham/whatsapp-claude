"""
LLM Router for invoice automation agent.

This router analyzes user messages and returns structured routing decisions.
It does NOT execute tools or modify state - it only provides recommendations.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from pydantic import ValidationError

from llm_router.schemas import (
    INTENT_TOOL_MAPPING,
    Confidence,
    RouterDecision,
    RouterIntent,
    RouterTool,
    ToolArguments,
    is_tool_valid_for_state,
)

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    def complete(self, prompt: str) -> str:
        """Send prompt to LLM and return response."""
        ...


class StubLLMProvider:
    """
    Stub LLM provider for testing and development.

    Uses rule-based logic to simulate LLM responses.
    Replace with actual LLM integration (Anthropic, OpenAI, etc.) in production.
    """

    # Keyword patterns for intent detection
    INTENT_PATTERNS: dict[RouterIntent, list[re.Pattern[str]]] = {
        RouterIntent.LIST_INVOICES: [
            re.compile(r"\b(all|my|show|list)\s+(active\s+)?invoices\b", re.I),
            re.compile(r"\blist\s+(all|my)?\s*invoices?\b", re.I),
            re.compile(r"\bshow\s+(me\s+)?(all\s+)?(my\s+)?invoices?\b", re.I),
            re.compile(r"\bwhat\s+invoices\b", re.I),
            re.compile(r"\bwhich\s+invoices\b", re.I),
            re.compile(r"\binvoices\s+(do\s+)?i\s+have\b", re.I),
            re.compile(r"\bpending\s+invoices\b", re.I),
            re.compile(r"\bopen\s+invoices\b", re.I),
            re.compile(r"\bactive\s+invoices\b", re.I),
        ],
        RouterIntent.INVOICE_APPROVAL: [
            re.compile(r"\bapprove\b", re.I),
            re.compile(r"\baccept\b", re.I),
            re.compile(r"\bok\s+with\b", re.I),
            re.compile(r"\blooks\s+good\b", re.I),
            re.compile(r"\bproceed\b", re.I),
        ],
        RouterIntent.INVOICE_REJECTION: [
            re.compile(r"\breject\b", re.I),
            re.compile(r"\bdecline\b", re.I),
            re.compile(r"\brefuse\b", re.I),
            re.compile(r"\bnot\s+accept\b", re.I),
        ],
        RouterIntent.PAYMENT_CONFIRMATION: [
            re.compile(r"\b(have\s+)?paid\b", re.I),
            re.compile(r"\bpayment\s+(sent|made|completed|done)\b", re.I),
            re.compile(r"\btransferred\b", re.I),
            re.compile(r"\bsent\s+(the\s+)?money\b", re.I),
        ],
        RouterIntent.INVOICE_DISPUTE: [
            re.compile(r"\bdispute\b", re.I),
            re.compile(r"\bcontest\b", re.I),
            re.compile(r"\bincorrect\b", re.I),
            re.compile(r"\bwrong\s+amount\b", re.I),
            re.compile(r"\berror\b", re.I),
        ],
        RouterIntent.REQUEST_INVOICE_COPY: [
            re.compile(r"\bresend\b", re.I),
            re.compile(r"\bsend\s+(me\s+)?(a\s+)?copy\b", re.I),
            re.compile(r"\bemail\s+(me\s+)?(the\s+)?invoice\b", re.I),
            re.compile(r"\bneed\s+(a\s+)?copy\b", re.I),
        ],
        RouterIntent.INVOICE_QUESTION: [
            re.compile(r"\bwhat\s+is\b", re.I),
            re.compile(r"\bhow\s+much\b", re.I),
            re.compile(r"\bstatus\b", re.I),
            re.compile(r"\bdetails?\b", re.I),
            re.compile(r"\bwhen\b", re.I),
            re.compile(r"\bdue\s+date\b", re.I),
        ],
    }

    # Pattern for future payment (NOT confirmation)
    FUTURE_PAYMENT_PATTERNS = [
        re.compile(r"\bwill\s+pay\b", re.I),
        re.compile(r"\bgoing\s+to\s+pay\b", re.I),
        re.compile(r"\bplan\s+to\s+pay\b", re.I),
        re.compile(r"\bi'?ll\s+pay\b", re.I),
        re.compile(r"\bpay\s+(you\s+)?(tomorrow|later|soon|next)\b", re.I),
    ]

    # Invoice ID extraction pattern
    INVOICE_ID_PATTERN = re.compile(
        r"(?:invoice\s+)?(?:#|INV-?)?(\d{3,})|INV-\d+",
        re.I,
    )

    def complete(self, prompt: str) -> str:
        """
        Simulate LLM response using rule-based logic.

        In production, this would call an actual LLM API.
        """
        # Extract user message and state from prompt
        user_message = self._extract_user_message(prompt)
        current_state = self._extract_state(prompt)

        # Detect intent
        intent = self._detect_intent(user_message)

        # Check for future payment (not a confirmation)
        if intent == RouterIntent.PAYMENT_CONFIRMATION:
            if self._is_future_payment(user_message):
                intent = RouterIntent.GENERAL_QUESTION

        # Extract invoice ID
        invoice_id = self._extract_invoice_id(user_message)

        # Determine tool
        tool = self._intent_to_tool(intent)

        # Build decision
        decision = self._build_decision(
            intent=intent,
            tool=tool,
            invoice_id=invoice_id,
            current_state=current_state,
            user_message=user_message,
        )

        return json.dumps(decision, indent=2)

    def _extract_user_message(self, prompt: str) -> str:
        """Extract user message from prompt."""
        # Look for the user message section
        marker = "## User Message"
        if marker in prompt:
            parts = prompt.split(marker)
            if len(parts) > 1:
                # Get text after marker until "## Your Response"
                msg_part = parts[1].split("## Your Response")[0]
                return msg_part.strip()
        return prompt

    def _extract_state(self, prompt: str) -> str:
        """Extract current state from prompt."""
        match = re.search(r"\*\*Current Invoice State\*\*:\s*(\w+)", prompt)
        if match:
            return match.group(1)
        return "unknown"

    def _detect_intent(self, message: str) -> RouterIntent:
        """Detect intent from message using patterns."""
        scores: dict[RouterIntent, int] = {}

        for intent, patterns in self.INTENT_PATTERNS.items():
            score = sum(1 for p in patterns if p.search(message))
            if score > 0:
                scores[intent] = score

        if not scores:
            # Check if it's a very short/vague message
            if len(message.split()) <= 3:
                return RouterIntent.UNKNOWN
            return RouterIntent.GENERAL_QUESTION

        # Return highest scoring intent
        return max(scores, key=lambda k: scores[k])

    def _is_future_payment(self, message: str) -> bool:
        """Check if message indicates future payment, not confirmation."""
        return any(p.search(message) for p in self.FUTURE_PAYMENT_PATTERNS)

    def _extract_invoice_id(self, message: str) -> Optional[str]:
        """Extract invoice ID from message."""
        match = self.INVOICE_ID_PATTERN.search(message)
        if match:
            full_match = match.group(0).upper()
            if full_match.startswith("INV"):
                return full_match.replace("INV", "INV-").replace("INV--", "INV-")
            if match.group(1):
                return f"INV-{match.group(1)}"
        return None

    def _intent_to_tool(self, intent: RouterIntent) -> RouterTool:
        """Map intent to primary tool."""
        tool_mapping = {
            RouterIntent.INVOICE_QUESTION: RouterTool.GET_INVOICE_STATUS,
            RouterIntent.LIST_INVOICES: RouterTool.LIST_INVOICES,
            RouterIntent.INVOICE_APPROVAL: RouterTool.APPROVE_INVOICE,
            RouterIntent.INVOICE_REJECTION: RouterTool.REJECT_INVOICE,
            RouterIntent.PAYMENT_CONFIRMATION: RouterTool.CONFIRM_PAYMENT,
            RouterIntent.INVOICE_DISPUTE: RouterTool.CREATE_DISPUTE,
            RouterIntent.REQUEST_INVOICE_COPY: RouterTool.RESEND_INVOICE,
            RouterIntent.GENERAL_QUESTION: RouterTool.NONE,
            RouterIntent.UNKNOWN: RouterTool.NONE,
        }
        return tool_mapping.get(intent, RouterTool.NONE)

    def _build_decision(
        self,
        intent: RouterIntent,
        tool: RouterTool,
        invoice_id: Optional[str],
        current_state: str,
        user_message: str,
    ) -> dict[str, Any]:
        """Build the decision dictionary."""
        decision: dict[str, Any] = {
            "intent": intent.value,
            "tool": tool.value,
            "arguments": {},
            "confidence": "high",
            "reasoning": "",
            "requires_clarification": False,
            "clarification_prompt": None,
            "warnings": [],
        }

        # Add invoice_id if found
        if invoice_id:
            decision["arguments"]["invoice_id"] = invoice_id

        # Check for missing invoice ID (not needed for list_invoices)
        if tool not in [RouterTool.NONE, RouterTool.LIST_INVOICES] and not invoice_id:
            if intent not in [RouterIntent.GENERAL_QUESTION, RouterIntent.UNKNOWN, RouterIntent.LIST_INVOICES]:
                decision["requires_clarification"] = True
                decision["clarification_prompt"] = (
                    "Which invoice would you like me to help with? "
                    "Please provide the invoice number."
                )
                decision["confidence"] = "medium"

        # Check for rejection/dispute without reason
        if intent in [RouterIntent.INVOICE_REJECTION, RouterIntent.INVOICE_DISPUTE]:
            # Simple check: if message is short, probably no reason
            if len(user_message.split()) < 6:
                decision["requires_clarification"] = True
                if intent == RouterIntent.INVOICE_REJECTION:
                    decision["clarification_prompt"] = (
                        f"To reject invoice {invoice_id or 'this invoice'}, "
                        "please provide a reason for the rejection."
                    )
                else:
                    decision["clarification_prompt"] = (
                        f"To dispute invoice {invoice_id or 'this invoice'}, "
                        "please describe the issue."
                    )
                decision["confidence"] = "medium"

        # Check state validity
        if tool != RouterTool.NONE and current_state != "unknown":
            if not is_tool_valid_for_state(tool, current_state):
                decision["warnings"].append(
                    f"Invoice is in '{current_state}' state - "
                    f"{tool.value} may not be valid from this state"
                )

        # Handle unknown intent
        if intent == RouterIntent.UNKNOWN:
            decision["confidence"] = "low"
            decision["requires_clarification"] = True
            decision["clarification_prompt"] = (
                "I'm not sure what you'd like to do. "
                "Would you like to check invoice status, approve, reject, or something else?"
            )
            decision["reasoning"] = "Message is ambiguous or unclear"

        # Handle future payment
        if intent == RouterIntent.GENERAL_QUESTION and "pay" in user_message.lower():
            if self._is_future_payment(user_message):
                decision["warnings"].append(
                    "This is not a payment confirmation - "
                    "user indicates future intent to pay, not completed payment"
                )
                decision["reasoning"] = (
                    "User indicates future payment intent, not a confirmation"
                )

        # Set reasoning if not set
        if not decision["reasoning"]:
            decision["reasoning"] = f"Detected {intent.value} intent"

        return decision


class LLMRouter:
    """
    LLM-based router for invoice automation.

    Responsibilities:
    - Build prompts from template
    - Call LLM provider
    - Parse and validate responses
    - Enforce JSON schema
    - Fallback to unknown on ambiguity

    This router does NOT:
    - Execute tools
    - Modify state
    - Make external API calls (except to LLM)
    """

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        prompt_path: Optional[Path] = None,
    ):
        """
        Initialize the router.

        Args:
            llm_provider: LLM provider for completions. Uses stub if not provided.
            prompt_path: Path to prompt template. Uses default if not provided.
        """
        self.llm_provider = llm_provider or StubLLMProvider()
        self.prompt_path = prompt_path or Path(__file__).parent / "prompt.md"
        self._prompt_template: Optional[str] = None

    @property
    def prompt_template(self) -> str:
        """Load and cache prompt template."""
        if self._prompt_template is None:
            self._prompt_template = self.prompt_path.read_text(encoding="utf-8")
        return self._prompt_template

    def route(
        self,
        message: str,
        state: str,
        context: Optional[dict[str, Any]] = None,
    ) -> RouterDecision:
        """
        Route a user message to determine intent, tool, and arguments.

        Args:
            message: The user's message
            state: Current invoice state (e.g., "new", "awaiting_approval")
            context: Additional context (invoice_id, conversation_history, etc.)

        Returns:
            RouterDecision with routing recommendation

        Note:
            This method does NOT execute any tools or modify state.
            It only returns a recommendation.
        """
        context = context or {}

        logger.debug(f"Routing message: {message[:50]}... (state={state})")

        # Build prompt
        prompt = self._build_prompt(message, state, context)

        # Call LLM
        try:
            llm_response = self.llm_provider.complete(prompt)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return self._fallback_decision(message, str(e))

        # Parse response
        try:
            decision = self._parse_response(llm_response)
        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return self._fallback_decision(message, f"Parse error: {e}")

        # Validate decision
        decision = self._validate_decision(decision, state)

        logger.info(
            f"Routed to intent={decision.intent}, tool={decision.tool}, "
            f"confidence={decision.confidence}"
        )

        return decision

    def _build_prompt(
        self,
        message: str,
        state: str,
        context: dict[str, Any],
    ) -> str:
        """Build the prompt from template."""
        prompt = self.prompt_template

        # Replace placeholders
        replacements = {
            "{{user_message}}": message,
            "{{current_state}}": state,
            "{{invoice_id}}": context.get("invoice_id", "Not specified"),
            "{{conversation_history}}": self._format_conversation_history(
                context.get("conversation_history", [])
            ),
        }

        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, str(value))

        return prompt

    def _format_conversation_history(
        self,
        history: list[dict[str, str]],
    ) -> str:
        """Format conversation history for prompt."""
        if not history:
            return "No previous messages"

        lines = []
        for msg in history[-5:]:  # Last 5 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]
            lines.append(f"- {role}: {content}")

        return "\n".join(lines)

    def _parse_response(self, response: str) -> RouterDecision:
        """Parse LLM response into RouterDecision."""
        # Try to extract JSON from response
        json_str = self._extract_json(response)

        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        # Validate against schema
        try:
            decision = RouterDecision(**data)
        except ValidationError as e:
            raise ValueError(f"Schema validation failed: {e}") from e

        return decision

    def _extract_json(self, response: str) -> str:
        """Extract JSON from response, handling markdown code blocks."""
        # Try to find JSON in code block
        code_block_match = re.search(
            r"```(?:json)?\s*([\s\S]*?)```",
            response,
        )
        if code_block_match:
            return code_block_match.group(1).strip()

        # Try to find raw JSON object
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            return json_match.group(0)

        # Return as-is and let JSON parser handle it
        return response.strip()

    def _validate_decision(
        self,
        decision: RouterDecision,
        state: str,
    ) -> RouterDecision:
        """Validate and potentially adjust the decision."""
        warnings = list(decision.warnings)

        # Check intent-tool consistency
        valid_tools = INTENT_TOOL_MAPPING.get(decision.intent, [])
        if decision.tool not in valid_tools and valid_tools:
            warnings.append(
                f"Tool '{decision.tool}' is not typically used with intent '{decision.intent}'"
            )

        # Check state-tool validity
        if not is_tool_valid_for_state(RouterTool(decision.tool), state):
            warnings.append(
                f"Tool '{decision.tool}' cannot be executed from state '{state}'"
            )

        # Update decision with any new warnings
        if warnings != decision.warnings:
            decision = RouterDecision(
                intent=decision.intent,
                tool=decision.tool,
                arguments=decision.arguments,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                requires_clarification=decision.requires_clarification,
                clarification_prompt=decision.clarification_prompt,
                warnings=warnings,
            )

        return decision

    def _fallback_decision(
        self,
        message: str,
        error: str,
    ) -> RouterDecision:
        """Create a fallback decision when routing fails."""
        return RouterDecision(
            intent=RouterIntent.UNKNOWN,
            tool=RouterTool.NONE,
            arguments=ToolArguments(),
            confidence=Confidence.LOW,
            reasoning=f"Routing failed: {error}",
            requires_clarification=True,
            clarification_prompt=(
                "I'm having trouble understanding your request. "
                "Could you please rephrase or provide more details?"
            ),
            warnings=[f"Routing error: {error}"],
        )
