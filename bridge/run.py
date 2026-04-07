#!/usr/bin/env python3
"""Run the full MiroFish -> signal -> XAUEX pipeline."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from bridge.assets import all_symbols, resolve_asset
from bridge.config import BridgeConfig
from bridge.direct_predictor import build_prediction_payload, build_recent_actions, render_direct_report
from bridge.history_cache import load_recent_trade_memory, load_state_snapshot
from bridge.market_oracle import MarketOracle
from bridge.source_registry import get_sources
from bridge.qdrant_memory import retrieve_qdrant_memory_snippets

logging.basicConfig(
    level=getattr(logging, __import__("os").environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='MiroFish -> XAUEX Bridge')
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
    parser.add_argument('--max-rounds', type=int, help='Override backend simulation round cap')
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = _parse_args()
    asset = resolve_asset(args.asset)

    if args.list_sources:
        rows = [row.to_dict() for row in get_sources(asset.symbol, auto_fetch_only=not args.include_manual_sources)]
        print(json.dumps(rows, indent=2))
        return

    config = BridgeConfig.from_env()
    if args.max_rounds is not None:
        config = replace(config, simulation_max_rounds=args.max_rounds)
    logger.info(
        'Bridge config: mode=%s sim_model=%s parser_model=%s llm_base_url=%s max_rounds=%s',
        config.prediction_mode,
        config.llm_model,
        config.parser_llm_model,
        config.llm_base_url,
        config.simulation_max_rounds if config.simulation_max_rounds is not None else 'auto',
    )
    from bridge.context_builder import ContextBuilder
    from bridge.brief_writer import write_brief
    from bridge.evidence_writer import write_evidence_pack
    from bridge.signal_parser import parse_signal
    from bridge.signal_writer import write_signal
    auto_context = args.auto_context or not (args.news or args.news_text)

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

    payload: dict | None = None
    if config.prediction_mode == 'direct':
        artifacts = build_direct_prediction_artifacts(
            config=config,
            asset_symbol=asset.symbol,
            context_markdown=news_text,
        )
        payload = artifacts['payload']
        results = artifacts['results']
    else:
        oracle = MarketOracle(config, asset)
        try:
            results = oracle.run(news_text)
        finally:
            oracle.close()

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
    )
    signal['source'] = {
        'mode': (
            'history_fallback'
            if results.get('fallback_reused')
            else 'direct_prediction'
            if results.get('prediction_mode') == 'direct'
            else 'live_pipeline'
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

    if payload is not None:
        write_evidence_pack(
            output_path=Path(config.evidence_output_path),
            context_summary=payload['context_excerpt'][:2400],
            recent_runs=payload['recent_runs'],
            weights=payload['weights'],
            price_features=payload['price_features'],
            prediction_mode='direct',
        )

    if args.dry_run:
        logger.info('DRY RUN - signal not written')
        return

    output_path = args.output or config.signal_output_path
    write_signal(signal, output_path)
    logger.info('Signal written to %s', output_path)


def build_direct_prediction_artifacts(
    *,
    config: BridgeConfig,
    asset_symbol: str,
    context_markdown: str,
) -> dict[str, object]:
    from bridge.assets import resolve_asset

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

    payload = build_prediction_payload(
        asset=asset,
        context_markdown=context_markdown,
        recent_runs=recent_runs,
        state_snapshot=state_snapshot,
        retrieved_memory=retrieved_memory,
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


if __name__ == '__main__':
    main()
