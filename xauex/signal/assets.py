"""Asset profiles for the XAUEX signal pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable
import json


@dataclass(frozen=True)
class AssetProfile:
    symbol: str
    display_name: str
    asset_class: str
    aliases: tuple[str, ...]
    project_name: str
    simulation_requirement: str
    parser_brief: str
    distance_unit: str
    min_stop_loss_distance: float
    max_stop_loss_distance: float
    default_stop_loss_distance: float
    min_take_profit_rr: float
    max_take_profit_rr: float
    execution_supported: bool


_ASSET_DATA = json.loads(r'''{
    "XAUUSD": {
        "symbol": "XAUUSD",
        "display_name": "Spot Gold (XAUUSD)",
        "asset_class": "metals",
        "aliases": [
            "XAU",
            "GOLD",
            "XAUUSD"
        ],
        "project_name": "XAUEX London Morning - XAUUSD",
        "simulation_requirement": "Simulate how current macro, central-bank, positioning, ETF-flow and geopolitical developments are likely to influence XAUUSD from the London cash open through the late-morning London session today. Focus on fresh Federal Reserve signals, overnight USD and rates direction, inflation or labor surprises, safe-haven demand, central-bank gold demand, bullion liquidity, ETF flows and speculative positioning that are likely to matter during today's London morning trade only.",
        "parser_brief": "Translate the swarm output into a single London-morning XAUUSD trade decision: BUY, SELL or HOLD. Gold usually reacts to real-rate expectations, USD direction, safe-haven demand, ETF flows and speculative positioning. The goal is one trade around the London open, held only through late morning / midday London time. Distances must be expressed in USD per ounce so XAUEX can execute the signal directly.",
        "distance_unit": "usd",
        "min_stop_loss_distance": 10.0,
        "max_stop_loss_distance": 15.0,
        "default_stop_loss_distance": 12.0,
        "min_take_profit_rr": 1.5,
        "max_take_profit_rr": 2.5,
        "execution_supported": true
    },
    "WTI": {
        "symbol": "WTI",
        "display_name": "WTI Crude Oil",
        "asset_class": "energy",
        "aliases": [
            "WTI",
            "USOIL",
            "OIL",
            "XTIUSD"
        ],
        "project_name": "XAUEX Macro Swarm - WTI",
        "simulation_requirement": "Simulate how current supply, demand, inventory, shipping, weather and geopolitical developments are likely to influence front-month WTI crude over the next 24 to 72 hours. Focus on EIA inventory data, refinery utilization, OPEC and non-OPEC supply guidance, hurricane disruptions, Middle East shipping risk, USD sensitivity and speculative positioning.",
        "parser_brief": "Translate the swarm output into a directional WTI signal. Oil reacts strongly to inventory surprises, supply outages, OPEC guidance, freight disruption, weather and demand revisions. Distances should be expressed as USD per barrel. XAUEX does not execute this asset yet; keep the signal schema consistent for later multi-asset execution.",
        "distance_unit": "usd",
        "min_stop_loss_distance": 1.0,
        "max_stop_loss_distance": 3.0,
        "default_stop_loss_distance": 1.6,
        "min_take_profit_rr": 1.6,
        "max_take_profit_rr": 3.0,
        "execution_supported": false
    },
    "GBPJPY": {
        "symbol": "GBPJPY",
        "display_name": "GBPJPY",
        "asset_class": "fx",
        "aliases": [
            "GBPJPY",
            "GBP/JPY",
            "GJ"
        ],
        "project_name": "XAUEX Macro Swarm - GBPJPY",
        "simulation_requirement": "Simulate how current Bank of England and Bank of Japan policy expectations, UK and Japan macro data, yield differentials, risk sentiment and official FX intervention risk are likely to influence GBPJPY over the next 24 to 96 hours. Focus on Bank Rate expectations, inflation and labor data, JGB and gilt yield differentials, BoJ communication, Ministry of Finance intervention signals and broad risk-on or risk-off conditions.",
        "parser_brief": "Translate the swarm output into a directional GBPJPY signal. This cross is primarily driven by UK-Japan rate-differential expectations, BoE and BoJ communication, global risk appetite and intervention risk. Distances should be expressed in pips. XAUEX does not execute this asset yet; keep the schema ready for a later symbol-aware execution engine.",
        "distance_unit": "pips",
        "min_stop_loss_distance": 35.0,
        "max_stop_loss_distance": 120.0,
        "default_stop_loss_distance": 65.0,
        "min_take_profit_rr": 1.5,
        "max_take_profit_rr": 3.0,
        "execution_supported": false
    }
}''')

ASSET_REGISTRY: Dict[str, AssetProfile] = {
    symbol: AssetProfile(
        symbol=data['symbol'],
        display_name=data['display_name'],
        asset_class=data['asset_class'],
        aliases=tuple(data['aliases']),
        project_name=data['project_name'],
        simulation_requirement=data['simulation_requirement'],
        parser_brief=data['parser_brief'],
        distance_unit=data['distance_unit'],
        min_stop_loss_distance=float(data['min_stop_loss_distance']),
        max_stop_loss_distance=float(data['max_stop_loss_distance']),
        default_stop_loss_distance=float(data['default_stop_loss_distance']),
        min_take_profit_rr=float(data['min_take_profit_rr']),
        max_take_profit_rr=float(data['max_take_profit_rr']),
        execution_supported=bool(data['execution_supported']),
    )
    for symbol, data in _ASSET_DATA.items()
}

_ALIAS_MAP: Dict[str, str] = {}
for profile in ASSET_REGISTRY.values():
    for alias in profile.aliases:
        _ALIAS_MAP[alias.upper()] = profile.symbol
    _ALIAS_MAP[profile.symbol.upper()] = profile.symbol


def resolve_asset(asset: str | None) -> AssetProfile:
    candidate = (asset or 'XAUUSD').upper().strip()
    symbol = _ALIAS_MAP.get(candidate)
    if symbol is None:
        allowed = ', '.join(sorted(ASSET_REGISTRY))
        raise ValueError(f'Unknown asset {asset!r}. Supported assets: {allowed}')
    return ASSET_REGISTRY[symbol]


def all_assets() -> Iterable[AssetProfile]:
    return ASSET_REGISTRY.values()


def all_symbols() -> list[str]:
    return list(ASSET_REGISTRY)
