"""Integration test: cTrader API connection."""

import asyncio
import logging
import pytest
from config import load_config
from bot.api.client import ApiClient

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_api_connection():
    """
    Manual integration test (requires live credentials).

    Verifies:
    1. SSL connection succeeds
    2. OAuth authentication succeeds
    3. Account balance ~£3,000, currency GBP
    4. XAUUSD symbol spec retrieved
    5. 200 H1 bars retrieved
    6. Weekly bar retrieved and levels valid
    7. Tick stream receives ticks
    8. Token refresh succeeds
    """
    pytest.skip("Integration test — requires live cTrader demo credentials.")
