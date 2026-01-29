"""Schemas and type definitions for the LLM Router."""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class RouterIntent(str, Enum):
    """Valid intents the router can identify."""

    INVOICE_QUESTION = "invoice_question"
    INVOICE_APPROVAL = "invoice_approval"
    INVOICE_REJECTION = "invoice_rejection"
    PAYMENT_CONFIRMATION = "payment_confirmation"
    INVOICE_DISPUTE = "invoice_dispute"
    REQUEST_INVOICE_COPY = "request_invoice_copy"
    GENERAL_QUESTION = "general_question"
    UNKNOWN = "unknown"


class RouterTool(str, Enum):
    """Valid tools the router can recommend."""

    GET_INVOICE_STATUS = "get_invoice_status"
    APPROVE_INVOICE = "approve_invoice"
    REJECT_INVOICE = "reject_invoice"
    CONFIRM_PAYMENT = "confirm_payment"
    RESEND_INVOICE = "resend_invoice"
    CREATE_DISPUTE = "create_dispute"
    RESOLVE_DISPUTE = "resolve_dispute"
    CLOSE_INVOICE = "close_invoice"
    NONE = "none"  # For cases where no tool should be called


class Confidence(str, Enum):
    """Confidence levels for routing decisions."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ToolArguments(BaseModel):
    """Arguments to pass to a tool."""

    invoice_id: Optional[str] = Field(None, description="Invoice identifier")
    reason: Optional[str] = Field(None, description="Reason for action (rejection, dispute)")
    resolution: Optional[str] = Field(None, description="Resolution details for disputes")
    approver_id: Optional[str] = Field(None, description="ID of the approver")
    payment_reference: Optional[str] = Field(None, description="Payment reference number")
    payment_method: Optional[str] = Field(None, description="Payment method used")

    @field_validator("invoice_id")
    @classmethod
    def validate_invoice_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate invoice ID format if provided."""
        if v is not None:
            # Must not be empty
            if not v.strip():
                return None
            # Normalize format
            v = v.strip().upper()
            # Basic format check - should contain INV or be numeric
            if not (v.startswith("INV") or v.isdigit() or v.startswith("#")):
                # Try to extract if it's something like "invoice 001"
                pass
        return v


class RouterDecision(BaseModel):
    """
    Structured decision from the LLM Router.

    This object represents the router's analysis of a user message.
    It does NOT execute any actions - it only provides a recommendation.
    """

    intent: RouterIntent = Field(
        ...,
        description="The classified intent of the user message",
    )
    tool: RouterTool = Field(
        ...,
        description="The recommended tool to handle this intent",
    )
    arguments: ToolArguments = Field(
        default_factory=ToolArguments,
        description="Arguments to pass to the tool",
    )
    confidence: Confidence = Field(
        default=Confidence.MEDIUM,
        description="Confidence level in this routing decision",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of the routing decision",
    )
    requires_clarification: bool = Field(
        default=False,
        description="Whether clarification is needed from the user",
    )
    clarification_prompt: Optional[str] = Field(
        None,
        description="Question to ask user if clarification is needed",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Any warnings about potential issues with this request",
    )

    class Config:
        use_enum_values = True

    def is_actionable(self) -> bool:
        """Check if this decision can be acted upon."""
        return (
            self.intent != RouterIntent.UNKNOWN
            and self.tool != RouterTool.NONE
            and not self.requires_clarification
            and self.confidence != Confidence.LOW
        )

    def to_execution_dict(self) -> dict[str, Any]:
        """Convert to dictionary for tool execution."""
        return {
            "tool": self.tool,
            "arguments": self.arguments.model_dump(exclude_none=True),
        }


# Mapping of intents to their valid tools
INTENT_TOOL_MAPPING: dict[RouterIntent, list[RouterTool]] = {
    RouterIntent.INVOICE_QUESTION: [RouterTool.GET_INVOICE_STATUS],
    RouterIntent.INVOICE_APPROVAL: [RouterTool.APPROVE_INVOICE],
    RouterIntent.INVOICE_REJECTION: [RouterTool.REJECT_INVOICE],
    RouterIntent.PAYMENT_CONFIRMATION: [RouterTool.CONFIRM_PAYMENT],
    RouterIntent.INVOICE_DISPUTE: [RouterTool.CREATE_DISPUTE],
    RouterIntent.REQUEST_INVOICE_COPY: [RouterTool.RESEND_INVOICE],
    RouterIntent.GENERAL_QUESTION: [RouterTool.NONE, RouterTool.GET_INVOICE_STATUS],
    RouterIntent.UNKNOWN: [RouterTool.NONE],
}


# States from which each tool can be called
TOOL_VALID_STATES: dict[RouterTool, list[str]] = {
    RouterTool.GET_INVOICE_STATUS: ["*"],  # Any state
    RouterTool.APPROVE_INVOICE: ["awaiting_approval"],
    RouterTool.REJECT_INVOICE: ["awaiting_approval"],
    RouterTool.CONFIRM_PAYMENT: ["payment_pending"],
    RouterTool.RESEND_INVOICE: ["invoice_sent", "awaiting_approval", "approved", "payment_pending"],
    RouterTool.CREATE_DISPUTE: ["approved", "payment_pending", "paid"],
    RouterTool.RESOLVE_DISPUTE: ["disputed"],
    RouterTool.CLOSE_INVOICE: ["paid", "rejected"],
    RouterTool.NONE: ["*"],
}


def is_tool_valid_for_state(tool: RouterTool, state: str) -> bool:
    """Check if a tool can be called from the given state."""
    valid_states = TOOL_VALID_STATES.get(tool, [])
    return "*" in valid_states or state in valid_states
