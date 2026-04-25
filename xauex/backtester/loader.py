"""Dukascopy tick data loader.

Uses the Rust `tick_parser` extension (built with maturin) when available
for ~20× faster CSV parsing. Falls back to pure Python automatically.
"""

import csv
import glob
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Iterator

logger = logging.getLogger(__name__)

# Try to import the Rust extension; pure-Python fallback if .so not present
try:
    import tick_parser as _tick_parser_rs
    _RUST_AVAILABLE = True
    logger.debug("[LOADER] Rust tick_parser %s loaded", _tick_parser_rs.__version__)
except ImportError:
    _tick_parser_rs = None
    _RUST_AVAILABLE = False
    logger.debug("[LOADER] Rust tick_parser not available — using pure-Python path")


@dataclass
class Tick:
    """Tick-level bid/ask data."""
    timestamp: datetime
    bid: float
    ask: float
    mid: float           # (bid + ask) / 2
    spread: float        # ask - bid


@dataclass
class OHLCBar:
    """Hourly OHLC bar resampled from ticks."""
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    max_spread: float
    tick_count: int
    ticks: List[Tick] = field(default_factory=list)


def _bar_open_time(ts: datetime) -> datetime:
    """Truncate to H1 boundary."""
    return ts.replace(minute=0, second=0, microsecond=0)


class TickLoader:
    """
    Load Dukascopy CSV tick files from directory.

    Expected CSV columns (duka output):
        Timestamp,Ask,Bid,AskVolume,BidVolume
    or:
        DateTime,Bid,Ask,BidVolume,AskVolume

    Both orderings are handled. Timestamps may be in ms-since-epoch
    (integer) or ISO-8601 string formats.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def load_bars(self, start_date: str, end_date: str) -> List[OHLCBar]:
        """
        Load and resample H1 bars from the given date range.
        start_date / end_date: "YYYY-MM-DD"
        Returns chronologically sorted list of H1 OHLCBar objects.

        Uses the Rust tick_parser extension when available (~20× faster).
        """
        start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        end_dt = (
            datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
            + timedelta(days=1)  # inclusive end date
        )

        if _RUST_AVAILABLE:
            return self._load_bars_rust(start_dt, end_dt, start_date, end_date)
        return self._load_bars_python(start_dt, end_dt, start_date, end_date)

    def _load_bars_rust(
        self,
        start_dt: datetime,
        end_dt: datetime,
        start_date: str,
        end_date: str,
    ) -> List[OHLCBar]:
        """Fast path: Rust extension parses CSV and resamples to H1."""
        csv_files = sorted(
            glob.glob(str(self.data_dir / "**" / "*.csv"), recursive=True)
        ) or sorted(glob.glob(str(self.data_dir / "*.csv")))

        bars_by_time: dict[datetime, OHLCBar] = {}
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()

        for path in csv_files:
            try:
                raw_bars = _tick_parser_rs.parse_to_bars(path, start_iso, end_iso)
                for b in raw_bars:
                    t = datetime.fromisoformat(b["open_time"]).replace(tzinfo=timezone.utc)
                    if t in bars_by_time:
                        # Merge bars from different files (unlikely but safe)
                        ex = bars_by_time[t]
                        ex.high = max(ex.high, b["high"])
                        ex.low  = min(ex.low,  b["low"])
                        ex.close = b["close"]
                        ex.max_spread = max(ex.max_spread, b["max_spread"])
                        ex.tick_count += b["tick_count"]
                    else:
                        bars_by_time[t] = OHLCBar(
                            open_time=t,
                            open=b["open"],
                            high=b["high"],
                            low=b["low"],
                            close=b["close"],
                            max_spread=b["max_spread"],
                            tick_count=b["tick_count"],
                        )
            except Exception as exc:
                logger.warning("[LOADER/RUST] Skipping %s: %s", path, exc)

        result = sorted(bars_by_time.values(), key=lambda b: b.open_time)
        if not result:
            logger.warning(
                "[LOADER] No ticks found in %s for %s–%s", self.data_dir, start_date, end_date
            )
        return result

    def _load_bars_python(
        self,
        start_dt: datetime,
        end_dt: datetime,
        start_date: str,
        end_date: str,
    ) -> List[OHLCBar]:
        """Pure-Python fallback path."""
        ticks = list(self._iter_ticks(start_dt, end_dt))
        if not ticks:
            logger.warning(
                "[LOADER] No ticks found in %s for %s–%s", self.data_dir, start_date, end_date
            )
            return []
        return self._resample(ticks)

    # ──────────────────────────────────────────────────────────────
    # Tick parsing
    # ──────────────────────────────────────────────────────────────

    def _iter_ticks(self, start: datetime, end: datetime) -> Iterator[Tick]:
        """Yield ticks from all CSV files in data_dir, filtered by range."""
        csv_files = sorted(
            glob.glob(str(self.data_dir / "**" / "*.csv"), recursive=True)
        )
        if not csv_files:
            csv_files = sorted(glob.glob(str(self.data_dir / "*.csv")))

        for path in csv_files:
            try:
                yield from self._parse_csv(path, start, end)
            except Exception as exc:
                logger.warning("[LOADER] Skipping %s: %s", path, exc)

    def _parse_csv(self, path: str, start: datetime, end: datetime) -> Iterator[Tick]:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            original_fields = reader.fieldnames or []
            # Build lowercase → original-case mapping for column detection
            lower_to_orig = {h.strip().lower(): h.strip() for h in original_fields}
            headers = list(lower_to_orig.keys())

            # Detect column names (lowercase key → original key)
            ts_key   = next((h for h in headers if "time" in h or "date" in h), None)
            bid_key  = next((h for h in headers if "bid" in h and "vol" not in h), None)
            ask_key  = next((h for h in headers if "ask" in h and "vol" not in h), None)

            if not all([ts_key, bid_key, ask_key]):
                logger.warning("[LOADER] Cannot detect columns in %s: %s", path, headers)
                return

            ts_col  = lower_to_orig[ts_key]
            bid_col = lower_to_orig[bid_key]
            ask_col = lower_to_orig[ask_key]

            for row in reader:
                try:
                    ts_raw = row[ts_col].strip()
                    # Handle ms epoch integer
                    if ts_raw.isdigit():
                        ts = datetime.fromtimestamp(int(ts_raw) / 1000, tz=timezone.utc)
                    else:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)

                    if ts < start or ts >= end:
                        continue

                    bid = float(row[bid_col])
                    ask = float(row[ask_col])
                    mid = (bid + ask) / 2.0
                    spread = ask - bid

                    yield Tick(timestamp=ts, bid=bid, ask=ask, mid=mid, spread=spread)
                except (ValueError, KeyError):
                    continue

    # ──────────────────────────────────────────────────────────────
    # Resampling
    # ──────────────────────────────────────────────────────────────

    def _resample(self, ticks: List[Tick]) -> List[OHLCBar]:
        """Group ticks into H1 bars using mid price."""
        bars: List[OHLCBar] = []
        current_open_time: datetime | None = None
        bar_ticks: List[Tick] = []

        for tick in sorted(ticks, key=lambda t: t.timestamp):
            bt = _bar_open_time(tick.timestamp)
            if current_open_time is None:
                current_open_time = bt

            if bt != current_open_time:
                if bar_ticks:
                    bars.append(self._build_bar(current_open_time, bar_ticks))
                bar_ticks = []
                current_open_time = bt

            bar_ticks.append(tick)

        if bar_ticks and current_open_time is not None:
            bars.append(self._build_bar(current_open_time, bar_ticks))

        return bars

    def _build_bar(self, open_time: datetime, ticks: List[Tick]) -> OHLCBar:
        mids = [t.mid for t in ticks]
        return OHLCBar(
            open_time=open_time,
            open=mids[0],
            high=max(mids),
            low=min(mids),
            close=mids[-1],
            max_spread=max(t.spread for t in ticks),
            tick_count=len(ticks),
            ticks=ticks,
        )
