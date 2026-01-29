"""Intent classification for invoice-related messages."""

import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from state_machine.models import Intent

logger = logging.getLogger(__name__)


class ClassifiedIntent(BaseModel):
    """Result of intent classification."""

    intent: Intent
    confidence: float = Field(..., ge=0.0, le=1.0)
    invoice_id: Optional[str] = None
    extracted_entities: dict[str, str] = Field(default_factory=dict)


class IntentClassifier:
    """
    Simple rule-based intent classifier.

    In production, this would be replaced with an LLM-based classifier
    or a trained ML model.
    """

    # Keyword patterns for each intent
    INTENT_PATTERNS: dict[Intent, list[str]] = {
        Intent.INVOICE_APPROVAL: [
            r"\bapprove\b",
            r"\baccept\b",
            r"\bok\s+with\s+(the\s+)?invoice\b",
            r"\blooks\s+good\b",
            r"\bproceed\b",
        ],
        Intent.INVOICE_REJECTION: [
            r"\breject\b",
            r"\bdecline\b",
            r"\brefuse\b",
            r"\bnot\s+accept\b",
            r"\bdon'?t\s+accept\b",
        ],
        Intent.PAYMENT_CONFIRMATION: [
            r"\bpaid\b",
            r"\bpayment\s+(sent|made|completed|done)\b",
            r"\btransfer(red)?\b",
            r"\bsent\s+(the\s+)?money\b",
        ],
        Intent.INVOICE_DISPUTE: [
            r"\bdispute\b",
            r"\bcontest\b",
            r"\bchallenge\b",
            r"\bincorrect\b",
            r"\bwrong\s+amount\b",
            r"\berror\s+in\b",
            r"\bmistake\b",
        ],
        Intent.REQUEST_INVOICE_COPY: [
            r"\bsend\s+(me\s+)?(a\s+)?copy\b",
            r"\bresend\b",
            r"\bemail\s+(me\s+)?(the\s+)?invoice\b",
            r"\bget\s+(a\s+)?copy\b",
            r"\bneed\s+(the\s+)?invoice\b",
        ],
        Intent.INVOICE_QUESTION: [
            r"\bwhat\s+is\b",
            r"\bhow\s+much\b",
            r"\bwhen\s+(is|was)\b",
            r"\bstatus\b",
            r"\bdetails?\b",
            r"\bexplain\b",
            r"\bbreakdown\b",
        ],
    }

    # Pattern to extract invoice IDs (e.g., INV-001, INV001, #001)
    INVOICE_ID_PATTERN = re.compile(
        r"(?:invoice\s+)?(?:#|INV-?)?(\d{3,})|INV-\d+",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        """Initialize the classifier with compiled patterns."""
        self._compiled_patterns: dict[Intent, list[re.Pattern[str]]] = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            self._compiled_patterns[intent] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

    def classify(self, message: str) -> ClassifiedIntent:
        """
        Classify the intent of a message.

        Args:
            message: The user message to classify

        Returns:
            ClassifiedIntent with the detected intent and confidence
        """
        message_lower = message.lower()
        scores: dict[Intent, float] = {}

        # Score each intent based on pattern matches
        for intent, patterns in self._compiled_patterns.items():
            matches = sum(1 for p in patterns if p.search(message_lower))
            if matches > 0:
                # Simple scoring: more matches = higher confidence
                scores[intent] = min(0.5 + (matches * 0.2), 0.95)

        # Extract invoice ID if present
        invoice_id = self._extract_invoice_id(message)
        entities: dict[str, str] = {}
        if invoice_id:
            entities["invoice_id"] = invoice_id

        # Determine the best intent
        if scores:
            best_intent = max(scores, key=lambda k: scores[k])
            confidence = scores[best_intent]
        else:
            # Default to general question if no patterns match
            best_intent = Intent.GENERAL_QUESTION
            confidence = 0.3

        logger.debug(
            f"Classified message as {best_intent} with confidence {confidence:.2f}"
        )

        return ClassifiedIntent(
            intent=best_intent,
            confidence=confidence,
            invoice_id=invoice_id,
            extracted_entities=entities,
        )

    def _extract_invoice_id(self, message: str) -> Optional[str]:
        """Extract invoice ID from message if present."""
        match = self.INVOICE_ID_PATTERN.search(message)
        if match:
            # Normalize the invoice ID format
            full_match = match.group(0).upper()
            if full_match.startswith("INV"):
                return full_match.replace("INV", "INV-").replace("INV--", "INV-")
            return f"INV-{match.group(1)}"
        return None
