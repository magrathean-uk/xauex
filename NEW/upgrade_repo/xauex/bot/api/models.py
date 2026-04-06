"""Data models for cTrader API interactions."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SymbolSpec:
    """Symbol specification from cTrader API."""
    symbol: str
    lot_size: float          # e.g., 100 for XAUUSD
    volume_min: float
    volume_max: float
    volume_step: float
    digits: int
    pip_value: float


@dataclass
class Position:
    """Position from cTrader API."""
    position_id: str
    symbol: str
    direction: str           # "LONG" or "SHORT"
    volume: float
    entry_price: float
    current_price: float
    unrealised_pnl: float
    open_time: datetime
    stop_loss: float
    take_profit: float


@dataclass
class Account:
    """Account state from cTrader API."""
    balance: float
    equity: float
    currency: str
    margin_available: float
    open_pnl: float = 0.0
