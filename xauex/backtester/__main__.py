"""
backtester CLI entrypoint.

Usage:
    python -m backtester --from 2022-01-01 --to 2026-02-28
    python -m backtester --from 2022-01-01 --to 2026-02-28 --output report.json
    python -m backtester --from 2022-01-01 --to 2026-02-28 --data-dir /path/to/csvs
"""

import argparse
import json
import sys
from pathlib import Path

from backtester.engine import BacktestEngine
from backtester.loader import TickLoader
from backtester.report import BacktestReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m backtester",
        description="XAUEX backtester — replay XAUUSD tick data through the live bot logic.",
    )
    parser.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD",
                        help="Start date (inclusive)")
    parser.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD",
                        help="End date (inclusive)")
    parser.add_argument("--data-dir", default="data/dukascopy", metavar="DIR",
                        help="Directory containing Dukascopy CSV files (default: data/dukascopy)")
    parser.add_argument("--output", metavar="FILE",
                        help="Write JSON metrics to this file in addition to stdout")
    parser.add_argument("--config", default=".env", metavar="FILE",
                        help="Path to .env config file (default: .env)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)

    if not data_dir.exists():
        print(f"ERROR: data directory not found: {data_dir}", file=sys.stderr)
        print("  Download tick data first:  python data/download.py --help", file=sys.stderr)
        sys.exit(1)

    print(f"XAUEX Backtester — {args.date_from} to {args.date_to}")
    print(f"Data dir : {data_dir.resolve()}")
    print(f"Config   : {args.config}")
    print()

    loader = TickLoader(data_dir)

    print("Loading tick data…")
    bars = loader.load_bars(args.date_from, args.date_to)
    if not bars:
        print("ERROR: No bars loaded. Check data directory and date range.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(bars):,} H1 bars ({args.date_from} → {args.date_to})")
    print("Running backtest…")
    print()

    engine = BacktestEngine(bars, env_file=args.config)
    metrics = engine.run()

    report = BacktestReport(metrics, args.date_from, args.date_to)
    report.print_report()

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(metrics, indent=2, default=str))
        print(f"\nMetrics written to {out_path}")


if __name__ == "__main__":
    main()
