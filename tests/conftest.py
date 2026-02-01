"""
Pytest configuration and fixtures.

CRITICAL: This file is loaded BEFORE test collection.
Environment variables MUST be loaded here for pytest.mark.skipif to work correctly.
"""

from dotenv import load_dotenv

# Load environment variables before pytest collects tests
# This ensures skipif conditions can access environment variables
load_dotenv()
