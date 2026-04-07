"""Direct weighted predictor inputs for the Oracle live path."""

from __future__ import annotations

from statistics import mean
from typing import Any

from bridge.assets import AssetProfile


def build_prediction_payload(
    *,
    asset: AssetProfile,
    context_markdown: str,
    recent_runs: list[dict[str, Any]],
    state_snapshot: dict[str, Any] | None = None,
    retrieved_memory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = state_snapshot or {}
    price_features = _price_features(snapshot)
    memory_summary = _memory_summary(recent_runs)
    compact_memory = _compact_retrieved_memory(retrieved_memory or [])
    context_excerpt = context_markdown[:12000]
    return {
        'asset': asset.symbol,
        'asset_class': asset.asset_class,
        'weights': {
            'price_action': 0.45,
            'macro_news': 0.35,
            'recent_memory': 0.20,
        },
        'context_excerpt': context_excerpt,
        'recent_runs': recent_runs[-8:],
        'price_features': price_features,
        'memory_summary': memory_summary,
        'retrieved_memory': compact_memory,
        'retrieved_memory_count': len(compact_memory),
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
        f"- Price action / market structure: {int(weights['price_action'] * 100)}%",
        f"- Macro / news sentiment: {int(weights['macro_news'] * 100)}%",
        f"- Recent oracle / trade memory: {int(weights['recent_memory'] * 100)}%",
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
        f"- Net recent PnL: {memory['net_pnl']:.2f}",
        f"- Buy count: {memory['buy_count']}",
        f"- Sell count: {memory['sell_count']}",
        f"- Winning trades: {memory['wins']}",
        f"- Losing trades: {memory['losses']}",
        '',
        '## Retrieved Similar Memory',
    ]
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
        payload['context_excerpt'],
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
    return actions


def _price_features(state_snapshot: dict[str, Any]) -> dict[str, Any]:
    closes = [float(value) for value in (state_snapshot.get('recent_h1_closes') or []) if value is not None]
    daily = (state_snapshot.get('levels') or {}).get('daily') or {}
    if len(closes) < 2:
        return {
            'h1_count': len(closes),
            'momentum_3': 0.0,
            'momentum_6': 0.0,
            'momentum_12': 0.0,
            'range_position': 'UNKNOWN',
            'price_bias': 'NEUTRAL',
        }
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
    momentum_3 = latest - closes[max(0, len(closes) - 4)]
    momentum_6 = latest - closes[max(0, len(closes) - 7)]
    momentum_12 = latest - closes[max(0, len(closes) - 13)]
    avg_momentum = mean([momentum_3, momentum_6, momentum_12])
    if avg_momentum > 0.75:
        price_bias = 'BUY'
    elif avg_momentum < -0.75:
        price_bias = 'SELL'
    else:
        price_bias = 'NEUTRAL'
    return {
        'h1_count': len(closes),
        'momentum_3': round(momentum_3, 2),
        'momentum_6': round(momentum_6, 2),
        'momentum_12': round(momentum_12, 2),
        'range_position': range_position,
        'price_bias': price_bias,
    }


def _memory_summary(recent_runs: list[dict[str, Any]]) -> dict[str, Any]:
    rows = recent_runs[-8:]
    notes = [str(item.get('journal', '')).strip() for item in rows if str(item.get('journal', '')).strip()]
    pnls = [float(item.get('pnl', 0.0) or 0.0) for item in rows]
    buy_count = sum(1 for item in rows if str(item.get('action', '')).upper() == 'BUY')
    sell_count = sum(1 for item in rows if str(item.get('action', '')).upper() == 'SELL')
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl < 0)
    return {
        'trade_count': len(rows),
        'net_pnl': round(sum(pnls), 2),
        'buy_count': buy_count,
        'sell_count': sell_count,
        'wins': wins,
        'losses': losses,
        'notes': notes[:3],
    }


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
