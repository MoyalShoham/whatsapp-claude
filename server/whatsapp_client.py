"""
WhatsApp Business API client for sending messages.

Documentation: https://developers.facebook.com/docs/whatsapp/cloud-api/
"""

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class WhatsAppClientError(Exception):
    """Error communicating with WhatsApp API."""

    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[dict] = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class WhatsAppClient:
    """
    Client for WhatsApp Business Cloud API.

    Features:
    - Send text messages
    - Send template messages
    - Mark messages as read
    - Async HTTP calls
    """

    BASE_URL = "https://graph.facebook.com/v18.0"

    def __init__(
        self,
        api_token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize WhatsApp client.

        Args:
            api_token: WhatsApp Business API access token.
            phone_number_id: WhatsApp Business phone number ID.
            timeout: HTTP request timeout in seconds.
        """
        self.api_token = api_token
        self.phone_number_id = phone_number_id
        self.timeout = timeout

        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return bool(self.api_token and self.phone_number_id)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_message(
        self,
        to: str,
        text: str,
    ) -> dict[str, Any]:
        """
        Send a text message.

        Args:
            to: Recipient phone number (with country code, no +).
            text: Message text.

        Returns:
            API response with message ID.

        Raises:
            WhatsAppClientError: If sending fails.
        """
        if not self.is_configured:
            logger.warning("WhatsApp client not configured - simulating send")
            return {"messaging_product": "whatsapp", "simulated": True}

        # Clean phone number
        to = to.lstrip("+").replace(" ", "").replace("-", "")

        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        return await self._make_request("POST", url, json=payload)

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en",
        components: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """
        Send a template message.

        Args:
            to: Recipient phone number.
            template_name: Name of the approved template.
            language_code: Template language code.
            components: Template components (header, body, button parameters).

        Returns:
            API response with message ID.
        """
        if not self.is_configured:
            logger.warning("WhatsApp client not configured - simulating send")
            return {"messaging_product": "whatsapp", "simulated": True}

        to = to.lstrip("+").replace(" ", "").replace("-", "")

        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"

        template = {
            "name": template_name,
            "language": {"code": language_code},
        }

        if components:
            template["components"] = components

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template,
        }

        return await self._make_request("POST", url, json=payload)

    async def mark_as_read(self, message_id: str) -> dict[str, Any]:
        """
        Mark a message as read.

        Args:
            message_id: ID of the message to mark as read.

        Returns:
            API response.
        """
        if not self.is_configured:
            return {"success": True, "simulated": True}

        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"

        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }

        return await self._make_request("POST", url, json=payload)

    async def send_interactive_buttons(
        self,
        to: str,
        body_text: str,
        buttons: list[dict[str, str]],
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Send an interactive message with buttons.

        Args:
            to: Recipient phone number.
            body_text: Message body.
            buttons: List of buttons, each with 'id' and 'title'.
            header_text: Optional header text.
            footer_text: Optional footer text.

        Returns:
            API response.
        """
        if not self.is_configured:
            return {"messaging_product": "whatsapp", "simulated": True}

        to = to.lstrip("+").replace(" ", "").replace("-", "")

        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"

        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": btn["id"], "title": btn["title"][:20]},
                    }
                    for btn in buttons[:3]  # Max 3 buttons
                ],
            },
        }

        if header_text:
            interactive["header"] = {"type": "text", "text": header_text}

        if footer_text:
            interactive["footer"] = {"text": footer_text}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }

        return await self._make_request("POST", url, json=payload)

    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make HTTP request to WhatsApp API."""
        client = await self._get_client()

        try:
            response = await client.request(method, url, **kwargs)

            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("error", {}).get("message", "Unknown error")
                raise WhatsAppClientError(
                    f"WhatsApp API error: {error_msg}",
                    status_code=response.status_code,
                    response=error_data,
                )

            return response.json()

        except httpx.TimeoutException as e:
            raise WhatsAppClientError(f"Request timeout: {e}") from e
        except httpx.RequestError as e:
            raise WhatsAppClientError(f"Request error: {e}") from e
