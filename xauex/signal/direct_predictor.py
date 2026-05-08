"""Direct weighted predictor inputs for the Oracle live path."""

from __future__ import annotations

from statistics import mean
from typing import Any, Optional

from xauex.signal.assets import AssetProfile

_BASE_WEIGHTS = {
    'price_action': 0.45,
    'macro_news': 0.35,
    'recent_memory': 0.20,
}


def build_prediction_payload(
    *,
    asset: AssetProfile,
    context_markdown: str,
    recent_runs: list[dict[str, Any]],
    state_snapshot: dict[str, Any] | None = None,
    retrieved_memory: list[dict[str, Any]] | None = None,
    market_snapshot: dict[str, Any] | None = None,
    context_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = state_snapshot or {}
    price_features = _price_features(snapshot)
    memory_summary = _memory_summary(recent_runs)
    compact_memory = _compact_retrieved_memory(retrieved_memory or [])
    market = market_snapshot or {}
    context_excerpt = context_markdown[:12000]
    return {
        'asset': asset.symbol,
        'asset_class': asset.asset_class,
        'weights': _weighting_model(market),
        'context_excerpt': context_excerpt,
        'recent_runs': recent_runs[-8:],
        'price_features': price_features,
        'memory_summary': memory_summary,
        'retrieved_memory': compact_memory,
        'retrieved_memory_count': len(compact_memory),
        'market_snapshot': market,
        'event_flags': dict(market.get('event_flags') or {}),
        'input_freshness': dict(market.get('input_freshness') or {}),
        'context_items': _compact_context_items(context_items or []),
    }


def render_direct_report(payload: dict[str, Any]) -> str:
    weights = payload['weights']
    price = payload['price_features']
    memory = payload['memory_summary']
    lines = [
        f"# Direct Oracle Dossier for {payload['asset']}",
        '',
        '## Objective',
        'Produce a single London-session XAUUSD trade bias using a weighted blend of fresh macro context, price structure, and recent trade memory.',
        '',
        '## Weighting Model',
        f"- Price action / market structure: {int(round(float(weights.get('price_action', 0.0)) * 100))}%",
        f"- Macro / news sentiment: {int(round(float(weights.get('macro_news', 0.0)) * 100))}%",
        f"- Recent oracle / trade memory: {int(round(float(weights.get('recent_memory', 0.0)) * 100))}%",
    ]
    if float(weights.get('prediction_markets', 0.0) or 0.0) > 0:
        lines.append(f"- Prediction markets / Polymarket: {int(round(float(weights['prediction_markets']) * 100))}%")
    lines.extend([
        '',
        '## Price Structure Snapshot',
        f"- Recent H1 close count: {price['h1_count']}",
        f"- H1 momentum (3 bars): {price['momentum_3']:.2f}",
        f"- H1 momentum (6 bars): {price['momentum_6']:.2f}",
        f"- H1 momentum (12 bars): {price['momentum_12']:.2f}",
        f"- Range position within daily high/low: {price['range_position']}",
        f"- Directional bias from price: {price['price_bias']}",
        '',
        '## Recent Trade Memory',
        f"- Recent trade count: {memory['trade_count']}",
        f"- Net recent PnL: {memory.get('net_pnl', 0.0):.2f}",
        f"- BUY trades: {memory['buy_count']} (PnL {memory.get('buy_pnl', 0.0):+.2f})",
        f"- SELL trades: {memory['sell_count']} (PnL {memory.get('sell_pnl', 0.0):+.2f})",
        f"- Winning trades: {memory['wins']}",
        f"- Losing trades: {memory['losses']}",
        f"- Last 3 directions: {', '.join(memory.get('last_3_directions') or []) or 'none'}",
        f"- Consecutive loss streak: {memory.get('consecutive_loss_direction') or 'none'}",
        '',
        '## Retrieved Similar Memory',
    ])
    if payload.get('retrieved_memory'):
        for snippet in payload['retrieved_memory'][:4]:
            score = snippet.get('score', 0.0)
            source = snippet.get('source', '')
            label = snippet.get('label', '')
            label_prefix = f"{label}: " if label else ''
            source_suffix = f" [{source}]" if source else ''
            lines.append(f"- {snippet.get('action', 'MEMORY')} ({score:.2f}) {label_prefix}{snippet.get('summary', '')}{source_suffix}".strip())
    else:
        lines.append('- No retrieved memory available.')
    lines.extend([
        '',
        '## Fresh Market Context',
        *_format_context_excerpt(payload['context_excerpt']),
        '',
        '## Structured Market Snapshot',
    ])
    market_snapshot = payload.get('market_snapshot') or {}
    market_series = (market_snapshot.get('series') or {}) if isinstance(market_snapshot, dict) else {}
    if market_series:
        for key, row in market_series.items():
            lines.append(_render_series_line(key, row))
    else:
        lines.append('- No structured market snapshot available.')
    fedwatch = market_snapshot.get('fedwatch') or {}
    fedwatch_status = str(fedwatch.get('status', '') or '').lower()
    if _is_surfaceable_status(fedwatch_status) and _has_fedwatch_details(fedwatch):
        lines.append('')
        lines.append('## FedWatch Snapshot')
        lines.append(f"- status: {fedwatch.get('status')}")
        if fedwatch.get('meeting_date'):
            lines.append(f"- meeting_date: {fedwatch.get('meeting_date')}")
        if fedwatch.get('current_target_range'):
            lines.append(f"- current_target_range: {fedwatch.get('current_target_range')}")
        if fedwatch.get('cut_probability') is not None:
            lines.append(f"- cut_probability: {fedwatch.get('cut_probability')}")
        if fedwatch.get('hold_probability') is not None:
            lines.append(f"- hold_probability: {fedwatch.get('hold_probability')}")
        if fedwatch.get('hike_probability') is not None:
            lines.append(f"- hike_probability: {fedwatch.get('hike_probability')}")
        if fedwatch.get('expected_change_bps') is not None:
            lines.append(f"- expected_change_bps: {fedwatch.get('expected_change_bps')}")
        if fedwatch.get('bias'):
            lines.append(f"- bias: {fedwatch.get('bias')}")
        if fedwatch.get('summary'):
            lines.append(f"- summary: {fedwatch.get('summary')}")
    policy_context = market_snapshot.get('policy_context') or {}
    policy_context_status = str(policy_context.get('status', '') or '').lower()
    if _is_surfaceable_status(policy_context_status) and _has_policy_context_details(policy_context):
        lines.append('')
        lines.append('## Policy Context')
        lines.append(f"- status: {policy_context.get('status')}")
        if policy_context.get('next_fomc_date') is not None:
            lines.append(f"- next_fomc_date: {policy_context.get('next_fomc_date')}")
        if policy_context.get('days_to_fomc') is not None:
            lines.append(f"- days_to_fomc: {policy_context.get('days_to_fomc')}")
        if policy_context.get('fomc_window_state'):
            lines.append(f"- fomc_window_state: {policy_context.get('fomc_window_state')}")
        if policy_context.get('summary'):
            lines.append(f"- summary: {policy_context.get('summary')}")
    cot = market_snapshot.get('cot') or {}
    cot_status = str(cot.get('status', '') or '').lower()
    if _is_surfaceable_status(cot_status) and cot.get('available'):
        lines.append('')
        lines.append('## CFTC Gold Positioning (Managed Money)')
        lines.append(f"- report_date_utc: {cot.get('report_date_utc')}")
        lines.append(f"- net_long_contracts: {cot.get('managed_money_net_long')}")
        lines.append(f"- net_change_wow: {cot.get('managed_money_net_change_wow')}")
        lines.append(f"- net_long_percentile_26w: {cot.get('net_long_percentile_26w')}")
        lines.append(f"- net_long_zscore_26w: {cot.get('net_long_zscore_26w')}")
        lines.append(f"- extreme_positioning: {cot.get('extreme_positioning')}")
        lines.append(f"- bias: {cot.get('bias')}")
        if cot.get('summary'):
            lines.append(f"- summary: {cot.get('summary')}")
    polymarket = market_snapshot.get('polymarket') or {}
    polymarket_status = str(polymarket.get('status', '') or '').lower()
    if _is_surfaceable_status(polymarket_status) and _has_polymarket_details(polymarket):
        lines.append('')
        lines.append('## Polymarket Prediction Markets')
        lines.append(f"- status: {polymarket.get('status')}")
        lines.append(f"- weight: {polymarket.get('weight')}")
        lines.append(f"- overall_bias: {polymarket.get('overall_bias')}")
        lines.append(f"- money_weighted_score: {polymarket.get('money_weighted_score')}")
        if polymarket.get('summary'):
            lines.append(f"- summary: {polymarket.get('summary')}")
        for row in (polymarket.get('markets') or [])[:5]:
            lines.append(
                "- market: "
                f"{row.get('question')} "
                f"yes_midpoint={row.get('yes_midpoint')} "
                f"bid={row.get('best_bid')} ask={row.get('best_ask')} "
                f"bias={row.get('bias')} "
                f"volume={row.get('volume')} liquidity={row.get('liquidity')}"
            )
    event_flags = payload.get('event_flags') or {}
    if event_flags:
        lines.append('')
        lines.append('## Event Flags')
        for key, value in event_flags.items():
            lines.append(f"- {key}: {bool(value)}")
    freshness = payload.get('input_freshness') or {}
    if freshness:
        lines.append('')
        lines.append('## Input Freshness')
        for key, value in freshness.items():
            lines.append(f"- {key}: {value}")
    lines.extend([
        '',
        '## Memory Notes',
    ])
    if memory['notes']:
        lines.extend(f"- {note}" for note in memory['notes'])
    else:
        lines.append('- No prior trade notes available.')
    return '\n'.join(lines).strip() + '\n'


def build_recent_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in payload.get('recent_runs', [])[-8:]:
        actions.append({
            'agent_name': 'recent_trade_memory',
            'action_type': item.get('action', 'UNKNOWN'),
            'content': item.get('journal', '')[:220],
        })
    for item in payload.get('retrieved_memory', [])[:4]:
        actions.append({
            'agent_name': 'retrieved_qdrant_memory',
            'action_type': item.get('action', 'MEMORY'),
            'content': _format_retrieved_memory_item(item),
        })
    price = payload.get('price_features', {})
    actions.append({
        'agent_name': 'price_structure',
        'action_type': price.get('price_bias', 'NEUTRAL'),
        'content': (
            f"momentum_3={price.get('momentum_3', 0.0):.2f} "
            f"momentum_6={price.get('momentum_6', 0.0):.2f} "
            f"range_position={price.get('range_position', 'UNKNOWN')}"
        ),
    })
    market = payload.get('market_snapshot') or {}
    overall_bias = market.get('overall_bias')
    if overall_bias:
        actions.append({
            'agent_name': 'market_snapshot',
            'action_type': overall_bias,
            'content': _format_market_snapshot(market),
        })
    fedwatch = market.get('fedwatch') or {}
    fedwatch_status = str(fedwatch.get('status', '') or '').lower()
    if _is_surfaceable_status(fedwatch_status) and _has_fedwatch_details(fedwatch):
        actions.append({
            'agent_name': 'fedwatch',
            'action_type': 'NEUTRAL',
            'content': _format_fedwatch(fedwatch),
        })
    policy_context = market.get('policy_context') or {}
    policy_context_status = str(policy_context.get('status', '') or '').lower()
    if _is_surfaceable_status(policy_context_status) and _has_policy_context_details(policy_context):
        actions.append({
            'agent_name': 'policy_context',
            'action_type': 'NEUTRAL',
            'content': _format_policy_context(policy_context),
        })
    cot = market.get('cot') or {}
    if cot.get('available'):
        cot_action = str(cot.get('bias', 'NEUTRAL') or 'NEUTRAL').upper()
        actions.append({
            'agent_name': 'cftc_cot',
            'action_type': cot_action,
            'content': _format_cot(cot),
        })
    polymarket = market.get('polymarket') or {}
    polymarket_status = str(polymarket.get('status', '') or '').lower()
    if _is_surfaceable_status(polymarket_status) and _has_polymarket_details(polymarket):
        actions.append({
            'agent_name': 'polymarket',
            'action_type': str(polymarket.get('overall_bias', 'NEUTRAL') or 'NEUTRAL').upper(),
            'content': _format_polymarket(polymarket),
        })
    return actions


def _calculate_atr(closes: list[float], periods: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    close_moves = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    if not close_moves:
        return 0.0
    if len(close_moves) < periods:
        return mean(close_moves)
    return mean(close_moves[-periods:])


def _price_features(state_snapshot: dict[str, Any]) -> dict[str, Any]:
    closes = [float(value) for value in (state_snapshot.get('recent_h1_closes') or []) if value is not None]
    daily = (state_snapshot.get('levels') or {}).get('daily') or {}
    latest_quote = ((state_snapshot.get('runtime') or {}).get('latest_quote') or {}) if isinstance(state_snapshot, dict) else {}
    trend = (state_snapshot.get('trend') or {}) if isinstance(state_snapshot, dict) else {}
    daily_bias_raw = trend.get('daily_bias') if isinstance(trend, dict) else None
    try:
        daily_bias = int(daily_bias_raw) if daily_bias_raw is not None else None
    except (TypeError, ValueError):
        daily_bias = None
    if len(closes) < 2:
        out = {
            'h1_count': len(closes),
            'momentum_3': 0.0,
            'momentum_6': 0.0,
            'momentum_12': 0.0,
            'momentum_24': None,
            'momentum_60': None,
            'range_position': 'UNKNOWN',
            'price_bias': 'NEUTRAL',
            'atr_14': 0.0,
            'momentum_threshold': 0.75,
            'daily_trend_bias': daily_bias,
            'regime_filter': 'INSUFFICIENT_DATA',
        }
        return _attach_latest_quote(out, latest_quote)
    latest = closes[-1]
    low = float(daily.get('low') or min(closes))
    high = float(daily.get('high') or max(closes))
    span = high - low
    pos = (latest - low) / span if span > 0 else 0.5
    if pos >= 0.66:
        range_position = 'UPPER_THIRD'
    elif pos <= 0.33:
        range_position = 'LOWER_THIRD'
    else:
        range_position = 'MIDDLE_THIRD'
    # Legacy short-window momentum (kept for downstream compatibility).
    momentum_3 = latest - closes[max(0, len(closes) - 4)]
    momentum_6 = latest - closes[max(0, len(closes) - 7)]
    momentum_12 = latest - closes[max(0, len(closes) - 13)]
    # Longer-window momentum used for the price_bias decision. The legacy 20h
    # window over 4720-base gold registered sub-dollar drift as "trending" and
    # produced spurious SELL signals on flat consolidation. 24h and 60h moves
    # require a meaningful directional displacement.
    momentum_24: Optional[float]
    momentum_60: Optional[float]
    if len(closes) >= 25:
        momentum_24 = latest - closes[-25]
    else:
        momentum_24 = None
    if len(closes) >= 61:
        momentum_60 = latest - closes[-61]
    else:
        momentum_60 = None
    long_window_components: list[float] = [momentum_6]
    if momentum_24 is not None:
        long_window_components.append(momentum_24)
    if momentum_60 is not None:
        long_window_components.append(momentum_60)
    avg_momentum = mean(long_window_components)
    atr_14 = _calculate_atr(closes, periods=14)
    # The legacy ATR*0.4 threshold was too sensitive on a base price of ~4700:
    # it triggered a directional bias on noise that did not exceed daily ATR.
    # Require a move at least as large as the 14-period ATR before claiming a
    # directional momentum bias.
    momentum_threshold = atr_14 * 1.0
    if avg_momentum > momentum_threshold:
        price_bias = 'BUY'
    elif avg_momentum < -momentum_threshold:
        price_bias = 'SELL'
    else:
        price_bias = 'NEUTRAL'

    # Regime-aware filter. The legacy filter unconditionally neutralized BUY in
    # the upper third and SELL in the lower third — that killed trend
    # continuation in real uptrends/downtrends. Now we only neutralize when the
    # daily-EMA trend disagrees with the H1 momentum (true mean-reversion
    # failure setup). When the trend agrees, we let trend continuation BUY/SELL
    # through.
    regime_filter = 'NONE'
    if price_bias == 'BUY' and range_position == 'UPPER_THIRD':
        if daily_bias is None:
            price_bias = 'NEUTRAL'
            regime_filter = 'NO_TREND_SIGNAL_NEUTRALIZED_BUY'
        elif daily_bias <= 0:
            price_bias = 'NEUTRAL'
            regime_filter = 'COUNTER_TREND_UPPER_THIRD_NEUTRALIZED_BUY'
        else:
            regime_filter = 'TREND_ALIGNED_UPPER_THIRD_KEPT_BUY'
    elif price_bias == 'SELL' and range_position == 'LOWER_THIRD':
        if daily_bias is None:
            price_bias = 'NEUTRAL'
            regime_filter = 'NO_TREND_SIGNAL_NEUTRALIZED_SELL'
        elif daily_bias >= 0:
            price_bias = 'NEUTRAL'
            regime_filter = 'COUNTER_TREND_LOWER_THIRD_NEUTRALIZED_SELL'
        else:
            regime_filter = 'TREND_ALIGNED_LOWER_THIRD_KEPT_SELL'

    out = {
        'h1_count': len(closes),
        'momentum_3': round(momentum_3, 2),
        'momentum_6': round(momentum_6, 2),
        'momentum_12': round(momentum_12, 2),
        'momentum_24': round(momentum_24, 2) if momentum_24 is not None else None,
        'momentum_60': round(momentum_60, 2) if momentum_60 is not None else None,
        'range_position': range_position,
        'price_bias': price_bias,
        'atr_14': round(atr_14, 4),
        'momentum_threshold': round(momentum_threshold, 4),
        'daily_trend_bias': daily_bias,
        'regime_filter': regime_filter,
    }
    return _attach_latest_quote(out, latest_quote)


def _attach_latest_quote(price_features: dict[str, Any], latest_quote: dict[str, Any]) -> dict[str, Any]:
    out = dict(price_features)
    if not isinstance(latest_quote, dict):
        return out
    for source_key, target_key in (
        ('bid', 'current_bid'),
        ('ask', 'current_ask'),
        ('mid', 'current_mid'),
        ('updated_at_utc', 'quote_updated_at_utc'),
    ):
        value = latest_quote.get(source_key)
        if value is not None:
            out[target_key] = value
    return out


def _memory_summary(recent_runs: list[dict[str, Any]]) -> dict[str, Any]:
    # Window extended from 8 → 12 so the direction-aware view captures fuller
    # streak patterns. The validator's "2+ losses in same direction" rule
    # needs the streak accumulator built below.
    rows = recent_runs[-12:]
    notes = [str(item.get('journal', '')).strip() for item in rows if str(item.get('journal', '')).strip()]
    pnls = [float(item.get('pnl', 0.0) or 0.0) for item in rows]
    actions = [str(item.get('action', '')).upper() for item in rows]
    buy_rows = [item for item in rows if str(item.get('action', '')).upper() == 'BUY']
    sell_rows = [item for item in rows if str(item.get('action', '')).upper() == 'SELL']
    buy_count = len(buy_rows)
    sell_count = len(sell_rows)
    buy_pnl = round(sum(float(item.get('pnl', 0.0) or 0.0) for item in buy_rows), 2)
    sell_pnl = round(sum(float(item.get('pnl', 0.0) or 0.0) for item in sell_rows), 2)
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl < 0)
    last_3_directions = [a for a in actions[-3:] if a]
    consecutive_loss_direction = _consecutive_loss_streak(rows)
    return {
        'trade_count': len(rows),
        'buy_pnl': buy_pnl,
        'sell_pnl': sell_pnl,
        'last_3_directions': last_3_directions,
        'consecutive_loss_direction': consecutive_loss_direction,
        'net_pnl': round(sum(pnls), 2),
        'buy_count': buy_count,
        'sell_count': sell_count,
        'wins': wins,
        'losses': losses,
        'notes': notes[:3],
    }


def _consecutive_loss_streak(rows: list[dict[str, Any]]) -> str:
    """Walk backwards from the most recent trade and count an unbroken run of
    losses in the same direction.

    Returns "" when the most recent trade was a win (no live streak), the
    direction was neutral, or no rows are available. Otherwise returns a
    label like "SELL_3" or "BUY_2" so the validator prompt has explicit
    evidence to bind to.
    """
    streak_direction = ''
    streak_count = 0
    for item in reversed(rows):
        pnl = float(item.get('pnl', 0.0) or 0.0)
        if pnl >= 0:
            break
        direction = str(item.get('action', '') or '').upper()
        if direction not in {'BUY', 'SELL'}:
            break
        if streak_direction == '':
            streak_direction = direction
            streak_count = 1
            continue
        if direction != streak_direction:
            break
        streak_count += 1
    if streak_count == 0 or streak_direction == '':
        return ''
    return f'{streak_direction}_{streak_count}'


def _compact_retrieved_memory(retrieved_memory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in retrieved_memory[:4]:
        compact.append({
            'id': str(item.get('id', '')),
            'score': float(item.get('score', 0.0) or 0.0),
            'action': str(item.get('action', 'MEMORY') or 'MEMORY').upper(),
            'summary': str(item.get('summary', '') or '')[:220],
            'source': str(item.get('source', '') or '')[:80],
            'label': str(item.get('label', '') or '')[:80],
        })
    return compact


def _format_retrieved_memory_item(item: dict[str, Any]) -> str:
    parts: list[str] = []
    label = str(item.get('label', '') or '').strip()
    if label:
        parts.append(label)
    summary = str(item.get('summary', '') or '').strip()
    if summary:
        parts.append(summary)
    source = str(item.get('source', '') or '').strip()
    score = float(item.get('score', 0.0) or 0.0)
    tail = f"score={score:.2f}"
    if source:
        tail = f"{tail} source={source}"
    parts.append(tail)
    return " | ".join(parts)[:260]


def _compact_context_items(context_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in context_items[:6]:
        compact.append({
            'source_id': str(item.get('source_id', '') or '')[:80],
            'source_name': str(item.get('source_name', '') or '')[:120],
            'tier': int(item.get('tier', 0) or 0),
            'freshness_score': float(item.get('freshness_score', 0.0) or 0.0),
            'title': str(item.get('title', '') or '')[:180],
            'summary': str(item.get('summary', '') or '')[:280],
        })
    return compact


def _weighting_model(market_snapshot: dict[str, Any]) -> dict[str, float]:
    polymarket = market_snapshot.get('polymarket') or {}
    if not polymarket.get('available'):
        return dict(_BASE_WEIGHTS)
    prediction_weight = max(0.0, min(0.40, float(polymarket.get('weight', 0.0) or 0.0)))
    if prediction_weight <= 0:
        return dict(_BASE_WEIGHTS)
    base_total = 1.0 - prediction_weight
    return {
        'price_action': round(_BASE_WEIGHTS['price_action'] * base_total, 4),
        'macro_news': round(_BASE_WEIGHTS['macro_news'] * base_total, 4),
        'recent_memory': round(_BASE_WEIGHTS['recent_memory'] * base_total, 4),
        'prediction_markets': round(prediction_weight, 4),
    }


def _format_market_snapshot(market_snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, row in (market_snapshot.get('series') or {}).items():
        gold_signal = _gold_signal_from_bias(row.get('bias'))
        parts.append(f"{key}={row.get('value')}(gold={gold_signal})")
    if market_snapshot.get('event_flags'):
        active = [key for key, value in market_snapshot['event_flags'].items() if value]
        if active:
            parts.append(f"events={','.join(active)}")
    return ' | '.join(parts)[:260]


def _gold_signal_from_bias(bias: Any) -> str:
    """Translate the (gold-perspective) bias label into an unambiguous label.

    The legacy 'BUY'/'SELL' encoding is gold-perspective but the LLM keeps
    misreading 'bias=BUY' on DXY as 'USD bullish'. Render as BULLISH/BEARISH
    so the gold direction is unmistakable.
    """
    text = str(bias or 'NEUTRAL').upper()
    if text == 'BUY':
        return 'BULLISH'
    if text == 'SELL':
        return 'BEARISH'
    return 'NEUTRAL'


def _render_series_line(key: str, row: dict[str, Any]) -> str:
    value = row.get('value')
    change = row.get('change_1d')
    bias = row.get('bias')
    gold_signal = _gold_signal_from_bias(bias)
    label = str(row.get('label', '') or '').strip()
    name_part = f"{key}" + (f" ({label})" if label else "")
    if change is None:
        return f"- {name_part}: value={value} → gold_signal={gold_signal}"
    interpretation = _interpret_for_gold(key, change)
    direction_word = 'rose' if change > 0 else 'fell' if change < 0 else 'flat'
    change_str = f"{abs(float(change)):.4f}".rstrip('0').rstrip('.')
    if not change_str:
        change_str = '0'
    if change == 0:
        change_clause = "unchanged on 1d"
    else:
        change_clause = f"{direction_word} {change_str} on 1d"
    if interpretation:
        return f"- {name_part}: value={value} ({change_clause}) → gold_signal={gold_signal} ({interpretation})"
    return f"- {name_part}: value={value} ({change_clause}) → gold_signal={gold_signal}"


def _interpret_for_gold(key: str, change: float) -> str:
    """Plain-English explanation of why a series move is gold-bullish or bearish.

    We surface this in the rendered report so the brief writer cannot
    accidentally invert the meaning (e.g. 'DXY bias=BUY' → 'USD strengthening').
    """
    if change == 0:
        return ''
    rising = change > 0
    if key in {'usd_broad_index', 'usd_major_index'}:
        # Lower DXY → weaker USD → easier dollar-priced gold bid.
        return 'USD strengthened — gold-pressuring' if rising else 'USD weakened — gold-supportive'
    if key == 'us2y_yield':
        return '2Y yield rose — opportunity-cost up, gold-pressuring' if rising else '2Y yield fell — opportunity-cost down, gold-supportive'
    if key == 'us10y_yield':
        return '10Y yield rose — opportunity-cost up, gold-pressuring' if rising else '10Y yield fell — opportunity-cost down, gold-supportive'
    if key == 'us10y_real_yield':
        return 'Real yield rose — gold-pressuring' if rising else 'Real yield fell — gold-supportive'
    if key in {'us5y_breakeven_inflation', 'us10y_breakeven_inflation'}:
        return 'Inflation expectations rose — gold-supportive (inflation hedge)' if rising else 'Inflation expectations fell — gold-pressuring'
    if key == 'vix':
        return 'VIX rose — risk-off, gold-supportive (safe-haven)' if rising else 'VIX fell — risk-on, gold-pressuring'
    if key == 'wti_oil':
        return 'Oil rose — inflation/commodity bid, gold-supportive' if rising else 'Oil fell — disinflation, gold-pressuring'
    if key == 'btc_usd':
        # Regime-dependent; let the LLM judge directionally without us forcing it.
        return ''
    return ''


def _is_surfaceable_status(status: str) -> bool:
    return status in {'available', 'warning'}


def _has_fedwatch_details(fedwatch: dict[str, Any]) -> bool:
    return bool(
        fedwatch.get('meeting_date')
        or fedwatch.get('current_target_range')
        or fedwatch.get('cut_probability') is not None
        or fedwatch.get('hold_probability') is not None
        or fedwatch.get('hike_probability') is not None
        or fedwatch.get('expected_change_bps') is not None
        or fedwatch.get('bias')
        or fedwatch.get('summary')
    )


def _has_policy_context_details(policy_context: dict[str, Any]) -> bool:
    return bool(
        policy_context.get('next_fomc_date') is not None
        or policy_context.get('days_to_fomc') is not None
        or policy_context.get('fomc_window_state')
        or policy_context.get('summary')
    )


def _has_polymarket_details(polymarket: dict[str, Any]) -> bool:
    return bool(
        polymarket.get('summary')
        or polymarket.get('overall_bias')
        or polymarket.get('money_weighted_score') is not None
        or polymarket.get('markets')
    )


def _format_context_excerpt(context_excerpt: str) -> list[str]:
    if not context_excerpt.strip():
        return ['    (no fresh context excerpt available)']
    return [f"    {line}" if line else '    ' for line in context_excerpt.splitlines()]


def _format_policy_context(policy_context: dict[str, Any]) -> str:
    parts: list[str] = []
    status = str(policy_context.get('status', '') or '').strip()
    if status:
        parts.append(f"status={status}")
    next_fomc_date = policy_context.get('next_fomc_date')
    if next_fomc_date is not None:
        parts.append(f"next_fomc_date={next_fomc_date}")
    days_to_fomc = policy_context.get('days_to_fomc')
    if days_to_fomc is not None:
        parts.append(f"days_to_fomc={days_to_fomc}")
    fomc_window_state = str(policy_context.get('fomc_window_state', '') or '').strip()
    if fomc_window_state:
        parts.append(f"fomc_window_state={fomc_window_state}")
    summary = str(policy_context.get('summary', '') or '').strip()
    if summary:
        parts.append(f"summary={summary}")
    return ' | '.join(parts)[:260]


def _format_cot(cot: dict[str, Any]) -> str:
    parts: list[str] = []
    bias = str(cot.get('bias', '') or '').strip()
    if bias:
        parts.append(f"bias={bias}")
    net_long = cot.get('managed_money_net_long')
    if net_long is not None:
        parts.append(f"mm_net_long={net_long}")
    change = cot.get('managed_money_net_change_wow')
    if change is not None:
        parts.append(f"net_change_wow={change}")
    percentile = cot.get('net_long_percentile_26w')
    if percentile is not None:
        parts.append(f"percentile_26w={percentile}")
    if cot.get('extreme_positioning'):
        parts.append('extreme=true')
    if cot.get('summary'):
        parts.append(str(cot.get('summary')))
    return ' | '.join(parts)[:260]


def _format_fedwatch(fedwatch: dict[str, Any]) -> str:
    parts: list[str] = []
    status = str(fedwatch.get('status', '') or '').strip()
    if status:
        parts.append(f"status={status}")
    bias = str(fedwatch.get('bias', '') or '').strip()
    if bias:
        parts.append(f"bias={bias}")
    meeting_date = fedwatch.get('meeting_date')
    if meeting_date is not None:
        parts.append(f"meeting_date={meeting_date}")
    current_target_range = str(fedwatch.get('current_target_range', '') or '').strip()
    if current_target_range:
        parts.append(f"current_target_range={current_target_range}")
    summary = str(fedwatch.get('summary', '') or '').strip()
    if summary:
        parts.append(f"summary={summary}")
    return ' | '.join(parts)[:260]


def _format_polymarket(polymarket: dict[str, Any]) -> str:
    parts: list[str] = []
    bias = str(polymarket.get('overall_bias', '') or '').strip()
    if bias:
        parts.append(f"bias={bias}")
    score = polymarket.get('money_weighted_score')
    if score is not None:
        parts.append(f"money_weighted_score={score}")
    weight = polymarket.get('weight')
    if weight is not None:
        parts.append(f"weight={weight}")
    summary = str(polymarket.get('summary', '') or '').strip()
    if summary:
        parts.append(f"summary={summary}")
    markets = polymarket.get('markets') or []
    if markets:
        first = markets[0]
        parts.append(
            f"top_market={first.get('question')} "
            f"yes_midpoint={first.get('yes_midpoint')} "
            f"bid={first.get('best_bid')} ask={first.get('best_ask')}"
        )
    return ' | '.join(parts)[:360]
