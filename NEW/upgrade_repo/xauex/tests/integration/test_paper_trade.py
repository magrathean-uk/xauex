"""Integration test: paper trade on demo."""

import asyncio
import logging
import pytest
from config import load_config
from bot.api.client import ApiClient

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_paper_trade():
    """
    Manual integration test (requires live credentials + OBSERVE_ONLY=false).

    Steps:
    1. Connect and authenticate
    2. Get current bid/ask
    3. Calculate lot size for £30 risk
    4. Place BUY market order with SL and TP
    5. Verify order in open positions
    6. Close position
    7. Verify closed trade
    8. Verify state.json updated
    """
    pytest.skip("Integration test — requires live cTrader demo credentials and OBSERVE_ONLY=false.")
