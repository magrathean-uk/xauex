#!/usr/bin/env python3
"""Run the full MiroFish -> signal -> XAUEX pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from bridge.assets import all_symbols, resolve_asset
from bridge.config import BridgeConfig
from bridge.source_registry import get_sources

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
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
    from bridge.context_builder import ContextBuilder
    from bridge.market_oracle import MarketOracle
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

    oracle = MarketOracle(config, asset)
    try:
        results = oracle.run(news_text)
    finally:
        oracle.close()

    logger.info(
        'Simulation complete for %s: %d actions, report length %d',
        asset.symbol,
        len(results['actions']),
        len(results['report_markdown']),
    )

    signal = parse_signal(
        asset=asset,
        actions=results['actions'],
        report_markdown=results['report_markdown'],
        config=config,
    )

    logger.info(
        'Signal: %s %s confidence=%.2f - %s',
        signal['symbol'],
        signal['action'],
        signal['confidence'],
        signal['reasoning'],
    )
    print(json.dumps(signal, indent=2))

    if args.dry_run:
        logger.info('DRY RUN - signal not written')
        return

    output_path = args.output or config.signal_output_path
    write_signal(signal, output_path)
    logger.info('Signal written to %s', output_path)


if __name__ == '__main__':
    main()
