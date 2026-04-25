"""CFTC Commitments of Traders (COT) adapter for COMEX gold positioning.

The CFTC publishes weekly futures positioning every Friday for the prior
Tuesday close. Managed Money net longs are a leading indicator for gold:
- Extreme net longs (>90th percentile of recent 26 weeks): crowded long,
  reversal/squeeze risk skews bearish.
- Extreme net shorts (<10th percentile): washout, mean-reversion bias bullish.
- Mid-range positioning: no positioning edge, defer to fundamentals.

API: https://publicreporting.cftc.gov/resource/72hh-3qpy.json
     (Disaggregated Futures-Only, public Socrata endpoint, no key required)
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from statistics import mean, pstdev
from typing import Any

import httpx

from xauex.signal.config import SignalConfig

logger = logging.getLogger(__name__)

_COT_ENDPOINT = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'
_GOLD_MARKET_NAMES = (
    'GOLD - COMMODITY EXCHANGE INC.',
    'GOLD - COMMODITY EXCHANGE',
    'GOLD',
)
_COT_LOOKBACK_WEEKS = 26
_EXTREME_HIGH_PERCENTILE = 0.90
_EXTREME_LOW_PERCENTILE = 0.10
_COT_FRESHNESS_BLOCK_DAYS = 21  # >3 weeks old indicates feed broken


def fetch_cot_snapshot(*, config: SignalConfig) -> dict[str, Any]:
    """Fetch latest gold COT positioning. Returns LLM-friendly summary dict."""
    headers = {
        'User-Agent': config.source_user_agent,
        'Accept': 'application/json',
    }

    params = {
        'market_and_exchange_names': _GOLD_MARKET_NAMES[0],
        '$order': 'report_date_as_yyyy_mm_dd DESC',
        '$limit': str(_COT_LOOKBACK_WEEKS),
    }

    try:
        response = httpx.get(
            _COT_ENDPOINT,
            params=params,
            headers=headers,
            follow_redirects=True,
            timeout=config.source_timeout_seconds,
        )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning('[COT] Gold COT fetch failed: %s', exc)
        return _unavailable_payload(f'CFTC COT fetch failed: {exc}')

    if not isinstance(rows, list) or not rows:
        return _unavailable_payload('CFTC COT response was empty.')

    parsed = [_parse_row(row) for row in rows]
    parsed = [row for row in parsed if row is not None]
    if not parsed:
        return _unavailable_payload('CFTC COT rows could not be parsed.')

    parsed.sort(key=lambda row: row['report_date'], reverse=True)
    latest = parsed[0]
    history = parsed[: _COT_LOOKBACK_WEEKS]

    nets = [row['mm_net'] for row in history]
    latest_net = latest['mm_net']
    prev_net = history[1]['mm_net'] if len(history) > 1 else latest_net
    net_change = latest_net - prev_net

    sorted_nets = sorted(nets)
    rank = sum(1 for value in sorted_nets if value <= latest_net)
    percentile = rank / len(sorted_nets) if sorted_nets else 0.5

    avg_net = mean(nets) if nets else 0.0
    spread = pstdev(nets) if len(nets) > 1 else 0.0
    z_score = (latest_net - avg_net) / spread if spread > 0 else 0.0

    bias = _bias_from_positioning(percentile)
    extreme = (
        percentile >= _EXTREME_HIGH_PERCENTILE
        or percentile <= _EXTREME_LOW_PERCENTILE
    )

    age_seconds = max(
        0.0,
        (datetime.now(timezone.utc) - latest['report_date']).total_seconds(),
    )
    if age_seconds >= _COT_FRESHNESS_BLOCK_DAYS * 86400:
        return _unavailable_payload(
            f'CFTC COT data is stale ({int(age_seconds / 86400)} days old).'
        )

    open_interest = latest.get('open_interest') or 0
    net_pct_oi = round(latest_net / open_interest, 4) if open_interest else 0.0

    summary = (
        f'Managed Money net long {latest_net:+,d} contracts '
        f'({net_change:+,d} WoW, percentile {int(percentile * 100)} over '
        f'{len(history)} weeks). Bias: {bias}.'
    )
    return {
        'status': 'available',
        'available': True,
        'source': 'cftc_disaggregated_futures_only',
        'report_date_utc': latest['report_date'].strftime('%Y-%m-%dT%H:%M:%SZ'),
        'managed_money_net_long': latest_net,
        'managed_money_net_change_wow': net_change,
        'managed_money_long': latest['mm_long'],
        'managed_money_short': latest['mm_short'],
        'open_interest': open_interest,
        'net_pct_of_open_interest': net_pct_oi,
        'net_long_percentile_26w': round(percentile, 3),
        'net_long_zscore_26w': round(z_score, 3),
        'extreme_positioning': extreme,
        'bias': bias,
        'summary': summary,
        'age_seconds': int(age_seconds),
        'fetched_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def _parse_row(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        date_text = str(
            row.get('report_date_as_yyyy_mm_dd')
            or row.get('report_date')
            or ''
        ).strip()
        if not date_text:
            return None
        report_date = datetime.fromisoformat(date_text.replace('Z', '+00:00'))
        if report_date.tzinfo is None:
            report_date = report_date.replace(tzinfo=timezone.utc)
        mm_long = _safe_int(row.get('m_money_positions_long_all'))
        mm_short = _safe_int(row.get('m_money_positions_short_all'))
        open_interest = _safe_int(row.get('open_interest_all'))
        if mm_long is None or mm_short is None:
            return None
        return {
            'report_date': report_date,
            'mm_long': mm_long,
            'mm_short': mm_short,
            'mm_net': mm_long - mm_short,
            'open_interest': open_interest or 0,
        }
    except Exception as exc:
        logger.debug('[COT] Skipping unparseable row: %s', exc)
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bias_from_positioning(percentile: float) -> str:
    if percentile >= _EXTREME_HIGH_PERCENTILE:
        return 'SELL'  # Crowded long → squeeze risk
    if percentile <= _EXTREME_LOW_PERCENTILE:
        return 'BUY'  # Washed-out positioning → mean-reversion bullish
    return 'NEUTRAL'


def _unavailable_payload(reason: str) -> dict[str, Any]:
    return {
        'status': 'unavailable',
        'available': False,
        'source': 'cftc_disaggregated_futures_only',
        'summary': reason,
    }
