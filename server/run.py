#!/usr/bin/env python3
"""
Run the Invoice Agent webhook server.

Usage:
    python server/run.py

Environment variables:
    HOST - Server host (default: 0.0.0.0)
    PORT - Server port (default: 8000)
    DEBUG - Enable debug mode (default: false)
    WHATSAPP_VERIFY_TOKEN - Token for webhook verification
    WHATSAPP_API_TOKEN - WhatsApp Business API access token
    WHATSAPP_PHONE_NUMBER_ID - WhatsApp Business phone number ID
    ANTHROPIC_API_KEY - Claude API key
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import uvicorn
from server.config import get_settings


def main() -> None:
    """Run the server."""
    settings = get_settings()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Invoice Automation Agent - Webhook Server          ║
╠══════════════════════════════════════════════════════════════╣
║  Host: {settings.host:<54}║
║  Port: {settings.port:<54}║
║  Debug: {str(settings.debug):<53}║
║  WhatsApp Configured: {str(bool(settings.whatsapp_api_token)):<39}║
║  Claude Configured: {str(bool(settings.anthropic_api_key)):<41}║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "server.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
