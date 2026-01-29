"""FastAPI server for WhatsApp webhook integration."""

from server.app import create_app
from server.config import Settings

__all__ = ["create_app", "Settings"]
