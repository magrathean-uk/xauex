"""Backwards-compatible wrapper that preserves the old GoldOracle import path."""

from bridge.assets import resolve_asset
from bridge.market_oracle import MarketOracle


class GoldOracle(MarketOracle):
    def __init__(self, config):
        super().__init__(config=config, asset=resolve_asset('XAUUSD'))
