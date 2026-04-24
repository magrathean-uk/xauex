#!/usr/bin/env python3
"""Run the full XAUEX -> signal -> XAUEX pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from xauex.live_windows import detect_window_label, get_live_window
from xauex.signal.assets import all_symbols, resolve_asset
from xauex.signal.config import SignalConfig
from xauex.signal.direct_predictor import build_prediction_payload, build_recent_actions, render_direct_report
from xauex.signal.history_cache import load_recent_trade_memory, load_state_snapshot
from xauex.signal.source_registry import get_sources
from xauex.signal.qdrant_memory import retrieve_qdrant_memory_snippets

logging.basicConfig(
    level=getattr(logging, __import__("os").environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='XAUEX Signal Pipeline')
    parser.add_argument('--asset', default='XAUUSD', help=f"Asset symbol or alias ({', '.join(all_symbols())})")
    parser.add_argument('--news', type=str, help='Path to a text/markdown file with market news')
    parser.add_argument('--news-text', type=str, help='Inline market context text')
    parser.add_argument('--auto-context', action='store_true', help='Auto-build a context file from the curated source registry')
    parser.add_argument('--lookback-hours', type=int, default=72, help='RSS lookback window when using --auto-context')
    parser.add_argument('--max-sources', type=int, default=10, help='Maximum number of sources to fetch when using --auto-context')
    parser.add_argument('--max-items-per-source', type=int, default=4, help='Maximum number of items to keep per source')
    parser.add_argument('--include-manual-sources', action='store_true', help='Include non-auto-fetch sources in --list-sources output')
    parser.add_argument('--list-sources', action='store_true', help='Print the curated source list for the chosen asset and exit')
    parser.add_argument('--dump-context', type=str, help='Write the normalized context markdown to this path')
    parser.add_argument('--dry-run', action='store_true', help="Parse signal but don't write cmd.json")
    parser.add_argument('--output', type=str, help='Override signal output path')
    parser.add_argument(
        '--window-label',
        choices=('morning', 'midday', 'us_open', 'current'),
        help='Explicit decision window label. Defaults to auto-detection from the shared live-window registry.',
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = _parse_args()
    asset = resolve_asset(args.asset)
    explicit_window_label = getattr(args, "window_label", None)
    if explicit_window_label:
        enforce_schedule = __import__("os").environ.get("XAUEX_ENFORCE_WINDOW_SCHEDULE", "").lower() in {"1", "true", "yes"}
        if enforce_schedule:
            window = get_live_window(window_label=explicit_window_label)
            if window is not None and not window.phase_due(datetime.now(timezone.utc), phase="signal"):
                logger.info("Skipping %s signal run because the shared timer fired outside that window's schedule.", explicit_window_label)
                return

    if args.list_sources:
        rows = [row.to_dict() for row in get_sources(asset.symbol, auto_fetch_only=not args.include_manual_sources)]
        print(json.dumps(rows, indent=2))
        return

    config = SignalConfig.from_env()
    logger.info(
        'Signal config: sim_model=%s parser_model=%s llm_base_url=%s qdrant=%s',
        config.llm_model,
        config.parser_llm_model,
        config.llm_base_url,
        config.qdrant_memory.enabled,
    )
    from xauex.signal.context_builder import ContextBuilder
    from xauex.signal.brief_writer import write_brief
    from xauex.signal.evidence_writer import write_evidence_pack
    from xauex.signal.signal_parser import parse_signal
    from xauex.signal.signal_writer import write_signal
    auto_context = args.auto_context or not (args.news or args.news_text)
    bundle = None

    if args.news and args.news_text:
        logger.error('Use either --news or --news-text, not both.')
        sys.exit(1)

    if args.news:
        news_text = Path(args.news).read_text(encoding='utf-8')
        logger.info('Read %d chars from %s', len(news_text), args.news)
    elif args.news_text:
        news_text = args.news_text
    elif auto_context:
        builder = ContextBuilder(config)
        try:
            bundle = builder.build(
                asset,
                lookback_hours=args.lookback_hours,
                max_sources=args.max_sources,
                max_items_per_source=args.max_items_per_source,
                auto_fetch_only=True,
            )
        finally:
            builder.close()
        news_text = bundle.markdown
        logger.info(
            'Built auto context for %s using %d items from %d sources',
            asset.symbol,
            bundle.item_count,
            bundle.source_count,
        )
        if args.dump_context:
            Path(args.dump_context).write_text(news_text, encoding='utf-8')
            logger.info('Context markdown written to %s', args.dump_context)
    else:
        logger.error('No context provided. Use --news, --news-text or --auto-context.')
        sys.exit(1)

    if not news_text or len(news_text.strip()) < 50:
        logger.error('Context text too short (need at least 50 chars)')
        sys.exit(1)

    context_items = [item.to_dict() for item in bundle.items] if bundle is not None else []
    window_label = explicit_window_label or _window_label()
    artifacts = build_direct_prediction_artifacts(
        config=config,
        asset_symbol=asset.symbol,
        context_markdown=news_text,
        context_items=context_items,
        window_label=window_label,
    )
    payload = artifacts['payload']
    results = artifacts['results']

    logger.info(
        'Prediction input complete for %s: %d actions, report length %d',
        asset.symbol,
        len(results['actions']),
        len(results['report_markdown']),
    )
    if results.get('fallback_reused'):
        logger.warning(
            'History fallback reused for %s: simulation_id=%s report_id=%s created_at=%s reason=%s',
            asset.symbol,
            results.get('simulation_id'),
            results.get('report_id'),
            results.get('fallback_created_at', ''),
            results.get('fallback_reason', ''),
        )

    signal = parse_signal(
        asset=asset,
        actions=results['actions'],
        report_markdown=results['report_markdown'],
        config=config,
        prediction_payload=payload,
        window_label=window_label,
    )
    signal['window_label'] = window_label
    signal['confirm_status'] = str(signal.get('confirm_status') or 'PENDING')
    signal['confirm_reason'] = str(signal.get('confirm_reason') or 'WAITING_FOR_CONFIRM')
    signal['confirm_timestamp_utc'] = signal.get('confirm_timestamp_utc')
    signal['source'] = {
        'mode': (
            'history_fallback'
            if results.get('fallback_reused')
            else 'direct_prediction'
        ),
        'simulation_id': results.get('simulation_id'),
        'report_id': results.get('report_id'),
        'created_at': results.get('fallback_created_at', ''),
        'reason': results.get('fallback_reason', ''),
    }

    logger.info(
        'Signal: %s %s confidence=%.2f - %s',
        signal['symbol'],
        signal['action'],
        signal['confidence'],
        signal['reasoning'],
    )
    print(json.dumps(signal, indent=2))

    if args.dry_run:
        logger.info('DRY RUN - signal/brief/evidence not written')
        return

    brief_meta = write_brief(
        asset=asset,
        signal=signal,
        actions=results['actions'],
        report_markdown=results['report_markdown'],
        simulation_id=results.get('simulation_id'),
        report_id=results.get('report_id'),
        output_path=config.brief_output_path,
        config=config,
    )
    signal['brief'] = brief_meta
    if isinstance(signal.get('llm_usage'), dict):
        signal['llm_usage'] = _merge_usage(signal['llm_usage'], brief_meta.get('usage') or {}, stage_name='brief')

    if payload is not None:
        write_evidence_pack(
            output_path=Path(config.evidence_output_path),
            context_summary=payload['context_excerpt'][:2400],
            recent_runs=payload['recent_runs'],
            weights=payload['weights'],
            price_features=payload['price_features'],
            market_snapshot=payload.get('market_snapshot'),
            input_freshness=(signal.get('decision_packet') or {}).get('input_freshness', payload.get('input_freshness')),
            validator={
                'status': signal.get('validator_status'),
                'consensus_state': signal.get('consensus_state'),
                'summary': signal.get('validator_summary'),
            },
            estimated_total_cost_usd=(signal.get('llm_usage') or {}).get('estimated_total_cost_usd'),
            prediction_mode='direct',
        )

    archive_dir = None
    if signal.get('decision_mode') == 'baseline':
        archive_dir = _archive_signal_run(
            config=config,
            asset=asset,
            context_markdown=news_text,
            context_items=context_items,
            payload=payload,
            results={**results, 'window_label': window_label},
            signal=signal,
            brief_meta=brief_meta,
            evidence_path=Path(config.evidence_output_path),
        )
        signal['source']['archive_dir'] = str(archive_dir)

    output_path = args.output or config.signal_output_path
    write_signal(signal, output_path)
    _append_cost_ledger_entry(config.cost_ledger_path, signal)
    _log_daily_budget(signal, config.daily_cost_cap_usd)
    logger.info('Signal written to %s', output_path)


def build_direct_prediction_artifacts(
    *,
    config: SignalConfig,
    asset_symbol: str,
    context_markdown: str,
    context_items: list[dict[str, object]] | None = None,
    window_label: str = 'current',
) -> dict[str, object]:
    from xauex.signal.assets import resolve_asset
    from xauex.signal.market_snapshot import build_market_snapshot

    asset = resolve_asset(asset_symbol)
    recent_runs = load_recent_trade_memory(Path(config.trade_journal_path))
    state_snapshot = load_state_snapshot(Path(config.state_file_path))
    retrieved_memory: list[dict[str, object]] = []
    if config.qdrant_memory.enabled:
        try:
            retrieved_memory = retrieve_qdrant_memory_snippets(
                config.qdrant_memory,
                asset_symbol=asset.symbol,
                context_markdown=context_markdown,
                recent_runs=recent_runs,
            )
        except Exception as exc:  # pragma: no cover - network/client failures
            logger.warning(
                'Qdrant memory retrieval failed for %s, continuing without it: %s',
                asset.symbol,
                exc,
            )
            retrieved_memory = []

    market_snapshot = build_market_snapshot(
        asset=asset,
        config=config,
        context_items=context_items or [],
        window_label=window_label,
    )
    payload = build_prediction_payload(
        asset=asset,
        context_markdown=context_markdown,
        recent_runs=recent_runs,
        state_snapshot=state_snapshot,
        retrieved_memory=retrieved_memory,
        market_snapshot=market_snapshot,
        context_items=context_items or [],
    )
    results = {
        'asset': asset.symbol,
        'actions': build_recent_actions(payload),
        'report_markdown': render_direct_report(payload),
        'simulation_id': None,
        'report_id': None,
        'fallback_reused': False,
        'prediction_mode': 'direct',
    }
    return {
        'payload': payload,
        'actions': results['actions'],
        'report_markdown': results['report_markdown'],
        'results': results,
    }


def _window_label() -> str:
    return detect_window_label(datetime.now(timezone.utc))


def _merge_usage(existing: dict[str, object], stage_usage: dict[str, object], *, stage_name: str) -> dict[str, object]:
    merged = dict(existing)
    stages = dict(merged.get('stages') or {})
    stages[stage_name] = stage_usage
    merged['stages'] = stages
    merged['prompt_tokens'] = int(merged.get('prompt_tokens', 0) or 0) + int(stage_usage.get('prompt_tokens', 0) or 0)
    merged['completion_tokens'] = int(merged.get('completion_tokens', 0) or 0) + int(stage_usage.get('completion_tokens', 0) or 0)
    merged['total_tokens'] = int(merged.get('total_tokens', 0) or 0) + int(stage_usage.get('total_tokens', 0) or 0)
    merged['estimated_total_cost_usd'] = round(
        float(merged.get('estimated_total_cost_usd', merged.get('estimated_cost_usd', 0.0)) or 0.0)
        + float(stage_usage.get('estimated_cost_usd', 0.0) or 0.0),
        8,
    )
    merged['estimated_cost_usd'] = merged['estimated_total_cost_usd']
    return merged


def _archive_signal_run(
    *,
    config: SignalConfig,
    asset,
    context_markdown: str,
    context_items: list[dict[str, object]],
    payload: dict[str, object],
    results: dict[str, object],
    signal: dict[str, object],
    brief_meta: dict[str, object] | None,
    evidence_path: Path,
) -> Path:
    timestamp = str(signal.get('timestamp_utc') or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    slug = timestamp.replace('-', '').replace(':', '')
    archive_dir = Path(config.archive_dir) / f'{slug}_{asset.symbol.lower()}_{signal.get("decision_mode", "baseline")}'
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / 'context.md').write_text(context_markdown, encoding='utf-8')
    (archive_dir / 'context_items.json').write_text(json.dumps(context_items, indent=2), encoding='utf-8')
    (archive_dir / 'prediction_payload.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    (archive_dir / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    (archive_dir / 'signal.json').write_text(json.dumps(signal, indent=2), encoding='utf-8')
    if brief_meta and brief_meta.get('path'):
        brief_path = Path(str(brief_meta['path']))
        if brief_path.exists():
            (archive_dir / 'brief.md').write_text(brief_path.read_text(encoding='utf-8'), encoding='utf-8')
        brief_meta_path = brief_path.with_suffix('.json')
        if brief_meta_path.exists():
            (archive_dir / 'brief.json').write_text(brief_meta_path.read_text(encoding='utf-8'), encoding='utf-8')
    if evidence_path.exists():
        (archive_dir / 'evidence.json').write_text(evidence_path.read_text(encoding='utf-8'), encoding='utf-8')
    return archive_dir


def _append_cost_ledger_entry(path: str, signal: dict[str, object]) -> None:
    usage = signal.get('llm_usage') or {}
    entry = {
        'timestamp_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'symbol': signal.get('symbol'),
        'action': signal.get('action'),
        'estimated_total_cost_usd': usage.get('estimated_total_cost_usd', usage.get('estimated_cost_usd')),
        'stages': usage.get('stages', {}),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry) + '\n')


def _log_daily_budget(signal: dict[str, object], daily_cost_cap_usd: float) -> None:
    usage = signal.get('llm_usage') or {}
    est = float(usage.get('estimated_total_cost_usd', usage.get('estimated_cost_usd', 0.0)) or 0.0)
    if daily_cost_cap_usd <= 0:
        return
    ratio = est / daily_cost_cap_usd
    if ratio >= 0.8:
        logger.warning(
            'Estimated run cost %.6f USD is at %.0f%% of the configured daily cap %.4f USD',
            est,
            ratio * 100,
            daily_cost_cap_usd,
        )


if __name__ == '__main__':
    main()
