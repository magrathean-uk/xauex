"""Shadow-trial helpers for baseline vs debate comparison logging."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from xauex.config import load_config
from xauex.signal.compare import load_archived_run, run_variant_comparison
from xauex.signal.config import SignalConfig
from xauex.signal.history_cache import load_state_snapshot
from xauex.signal.assets import resolve_asset


DEFAULT_SHADOW_ROOT = '/var/lib/xauex/shadow_trials'
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MIN_HISTORY_DAYS = 6
DEFAULT_HOLD_BAND_USD = 2.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='XAUEX shadow-trial helpers')
    subparsers = parser.add_subparsers(dest='command', required=True)

    compare = subparsers.add_parser('compare', help='Run the analyst debate shadow compare from the latest archived baseline run')
    compare.add_argument('--archive-root', default=os.getenv('XAUEX_SIGNAL_ARCHIVE_DIR', '/var/lib/xauex/signal_runs'))
    compare.add_argument('--shadow-root', default=os.getenv('XAUEX_SHADOW_TRIAL_DIR', DEFAULT_SHADOW_ROOT))
    compare.add_argument('--report-email', default=os.getenv('XAUEX_REPORT_EMAIL', 'bolyki@bolyki.eu'))

    evaluate = subparsers.add_parser('evaluate', help='Resolve any due shadow trials using broker M1 bars')
    evaluate.add_argument('--shadow-root', default=os.getenv('XAUEX_SHADOW_TRIAL_DIR', DEFAULT_SHADOW_ROOT))
    evaluate.add_argument('--hold-band-usd', type=float, default=float(os.getenv('XAUEX_SHADOW_HOLD_BAND_USD', str(DEFAULT_HOLD_BAND_USD))))

    report = subparsers.add_parser('report', help='Email a summary of recent shadow-trial results')
    report.add_argument('--shadow-root', default=os.getenv('XAUEX_SHADOW_TRIAL_DIR', DEFAULT_SHADOW_ROOT))
    report.add_argument('--recipient', default=os.getenv('XAUEX_REPORT_EMAIL', 'bolyki@bolyki.eu'))
    report.add_argument('--lookback-days', type=int, default=int(os.getenv('XAUEX_SHADOW_REPORT_LOOKBACK_DAYS', str(DEFAULT_LOOKBACK_DAYS))))
    report.add_argument('--min-history-days', type=int, default=int(os.getenv('XAUEX_SHADOW_REPORT_MIN_HISTORY_DAYS', str(DEFAULT_MIN_HISTORY_DAYS))))

    return parser.parse_args()


def find_latest_baseline_archive(root: Path | str, *, max_age_minutes: int | None = None) -> Path | None:
    base = Path(root)
    if not base.exists():
        return None
    candidates = [path for path in base.iterdir() if path.is_dir() and path.name.endswith('_baseline')]
    if max_age_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        filtered: list[Path] = []
        for path in candidates:
            try:
                timestamp_text = path.name.split('_', 1)[0]
                timestamp = _parse_utc(timestamp_text.replace('T', 'T').replace('Z', 'Z'))
            except ValueError:
                continue
            if timestamp >= cutoff:
                filtered.append(path)
        candidates = filtered
    if not candidates:
        return None
    return sorted(candidates)[-1]


def build_shadow_trial_record(
    *,
    archive_dir: Path,
    comparison_dir: Path,
    comparison: dict[str, Any],
    archived_signal: dict[str, Any],
    prediction_payload: dict[str, Any],
    compare_quote: dict[str, Any] | None,
    report_email: str,
) -> dict[str, Any]:
    signal_timestamp = _parse_utc(str(archived_signal.get('timestamp_utc') or ''))
    signal_mid_price = _signal_mid_price(prediction_payload)
    compare_quote = compare_quote or {}
    trial_id = f"{signal_timestamp.strftime('%Y%m%dT%H%M%SZ')}_{str(archived_signal.get('symbol') or 'xauusd').lower()}"
    return {
        'trial_id': trial_id,
        'archive_run': str(archive_dir),
        'comparison_dir': str(comparison_dir),
        'created_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'signal_timestamp_utc': signal_timestamp.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'evaluation_due_at_utc': (signal_timestamp + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'report_email': report_email,
        'signal_mid_price': signal_mid_price,
        'compare_mid_price': round(_safe_float(compare_quote.get('mid')), 2) if compare_quote.get('mid') is not None else None,
        'compare_quote_updated_at_utc': str(compare_quote.get('updated_at_utc') or ''),
        'baseline': dict(comparison.get('baseline') or {}),
        'analyst_debate': dict(comparison.get('analyst_debate') or {}),
        'tradingagents_candidate': dict(comparison.get('tradingagents_candidate') or {}),
        'delta': dict(comparison.get('delta') or {}),
        'deltas': dict(comparison.get('deltas') or {}),
        'outcome': {'status': 'pending'},
    }


def evaluate_shadow_trial_record(
    record: dict[str, Any],
    *,
    evaluation_price: float,
    evaluation_price_timestamp_utc: str,
    hold_band_usd: float,
) -> dict[str, Any]:
    signal_price = _safe_float(record.get('signal_mid_price'))
    move_usd = round(float(evaluation_price) - signal_price, 2)
    baseline_action = str((record.get('baseline') or {}).get('action') or '').upper()
    debate_action = str((record.get('analyst_debate') or {}).get('action') or '').upper()
    candidate = record.get('tradingagents_candidate') or {}
    candidate_action = str(candidate.get('action') or '').upper()
    baseline_correct = _action_is_correct(baseline_action, move_usd, hold_band_usd)
    debate_correct = _action_is_correct(debate_action, move_usd, hold_band_usd)
    candidate_correct = _action_is_correct(candidate_action, move_usd, hold_band_usd) if candidate else None
    winner = _shadow_winner(
        baseline_correct=baseline_correct,
        debate_correct=debate_correct,
        candidate_correct=candidate_correct,
    )
    updated = dict(record)
    outcome = {
        'status': 'completed',
        'evaluation_price': round(float(evaluation_price), 2),
        'evaluation_price_timestamp_utc': evaluation_price_timestamp_utc,
        'move_usd': move_usd,
        'baseline_correct': baseline_correct,
        'analyst_debate_correct': debate_correct,
        'winner': winner,
    }
    if candidate_correct is not None:
        outcome['tradingagents_candidate_correct'] = candidate_correct
    updated['outcome'] = outcome
    return updated


def render_shadow_trial_report(rows: list[dict[str, Any]], *, recipient: str) -> tuple[str, str]:
    completed = [row for row in rows if str((row.get('outcome') or {}).get('status') or '') == 'completed']
    baseline_wins = sum(1 for row in completed if (row.get('outcome') or {}).get('winner') == 'baseline')
    debate_wins = sum(1 for row in completed if (row.get('outcome') or {}).get('winner') == 'analyst_debate')
    candidate_wins = sum(1 for row in completed if (row.get('outcome') or {}).get('winner') == 'tradingagents_candidate')
    both_correct = sum(1 for row in completed if _winner_is_two_correct(str((row.get('outcome') or {}).get('winner') or '')))
    all_correct = sum(1 for row in completed if (row.get('outcome') or {}).get('winner') == 'all')
    neither_correct = sum(1 for row in completed if (row.get('outcome') or {}).get('winner') == 'neither')
    lines = [
        'XAUEX shadow trial results',
        '',
        f'Recipient: {recipient}',
        f'Completed trials: {len(completed)}',
        f'Baseline wins: {baseline_wins}',
        f'Debate wins: {debate_wins}',
        f'Candidate wins: {candidate_wins}',
        f'Both correct: {both_correct}',
        f'All correct: {all_correct}',
        f'Neither correct: {neither_correct}',
        '',
        'Trials:',
    ]
    for row in completed:
        outcome = row.get('outcome') or {}
        candidate_text = ''
        if row.get('tradingagents_candidate'):
            candidate_text = f" candidate={row.get('tradingagents_candidate', {}).get('action')}"
        lines.append(
            f"- {row.get('signal_timestamp_utc')} baseline={row.get('baseline', {}).get('action')} "
            f"debate={row.get('analyst_debate', {}).get('action')}{candidate_text} "
            f"move={outcome.get('move_usd')} winner={outcome.get('winner')}"
        )
    subject = f'XAUEX shadow trial results ({len(completed)} completed)'
    return subject, '\n'.join(lines)


def create_shadow_trial(
    *,
    archive_root: Path | str,
    shadow_root: Path | str,
    report_email: str,
) -> Path | None:
    archive_dir = find_latest_baseline_archive(archive_root, max_age_minutes=60)
    if archive_dir is None:
        return None
    shadow_root = Path(shadow_root)
    if _find_existing_trial_for_archive(shadow_root, archive_dir) is not None:
        return None

    signal_config = SignalConfig.from_env()
    frozen = load_archived_run(archive_dir)
    archived_signal = read_json(archive_dir / 'signal.json')
    asset = resolve_asset(str(archived_signal.get('symbol') or frozen.get('payload', {}).get('asset') or 'XAUUSD'))
    signal_timestamp = _parse_utc(str(archived_signal.get('timestamp_utc') or ''))
    trial_id = f"{signal_timestamp.strftime('%Y%m%dT%H%M%SZ')}_{asset.symbol.lower()}"
    trial_dir = shadow_root / trial_id
    comparison = run_variant_comparison(
        config=signal_config,
        asset=asset,
        context_markdown=str(frozen['context_markdown']),
        context_items=list(frozen['context_items']),
        payload=dict(frozen['payload']),
        results=dict(frozen['results']),
        output_dir=trial_dir,
        full_run=True,
        window_label=str((frozen['results'] or {}).get('window_label') or 'current'),
    )
    compare_quote = _latest_quote_snapshot(Path(signal_config.state_file_path))
    record = build_shadow_trial_record(
        archive_dir=archive_dir,
        comparison_dir=trial_dir,
        comparison=comparison,
        archived_signal=archived_signal,
        prediction_payload=dict(frozen['payload']),
        compare_quote=compare_quote,
        report_email=report_email,
    )
    write_json(trial_dir / 'trial.json', record)
    _append_trial_event(shadow_root, {'event': 'created', **record})
    return trial_dir


async def resolve_due_shadow_trials(
    *,
    shadow_root: Path | str,
    hold_band_usd: float,
) -> list[Path]:
    shadow_root = Path(shadow_root)
    updated: list[Path] = []
    due_paths = [
        path for path in _iter_trial_paths(shadow_root)
        if _trial_is_due(read_json(path))
    ]
    if not due_paths:
        return updated
    config = load_config()
    from xauex.bot.api.client import ApiClient

    client = ApiClient(config)
    await client.connect()
    try:
        await client.get_symbol_spec("XAUUSD")
        for path in due_paths:
            record = read_json(path)
            evaluation_due = _parse_utc(str(record.get('evaluation_due_at_utc') or ''))
            evaluation_price, evaluation_price_ts = await _fetch_evaluation_price(client, evaluation_due)
            resolved = evaluate_shadow_trial_record(
                record,
                evaluation_price=evaluation_price,
                evaluation_price_timestamp_utc=evaluation_price_ts,
                hold_band_usd=hold_band_usd,
            )
            write_json(path, resolved)
            _append_trial_event(shadow_root, {'event': 'evaluated', 'trial_id': resolved['trial_id'], 'outcome': resolved['outcome']})
            updated.append(path)
    finally:
        await client.disconnect()
    return updated


def send_shadow_trial_report(
    *,
    shadow_root: Path | str,
    recipient: str,
    lookback_days: int,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
) -> tuple[str, str]:
    rows = load_recent_trials(shadow_root, lookback_days=lookback_days)
    if not shadow_report_has_enough_history(rows, min_history_days=min_history_days):
        subject = 'XAUEX shadow trial results skipped'
        body = (
            f'XAUEX shadow trial results skipped: insufficient history for {min_history_days} day(s).\n'
            f'Loaded trials in {lookback_days}-day lookback: {len(rows)}'
        )
        return subject, body
    subject, body = render_shadow_trial_report(rows, recipient=recipient)
    _send_mail(recipient, subject, body)
    return subject, body


def shadow_report_has_enough_history(
    rows: list[dict[str, Any]],
    *,
    min_history_days: int,
    now: datetime | None = None,
) -> bool:
    if min_history_days <= 0:
        return True
    completed_times: list[datetime] = []
    for row in rows:
        if str((row.get('outcome') or {}).get('status') or '') != 'completed':
            continue
        try:
            completed_times.append(_parse_utc(str(row.get('signal_timestamp_utc') or '')))
        except ValueError:
            continue
    if not completed_times:
        return False
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    oldest = min(completed_times)
    return oldest <= now - timedelta(days=min_history_days)


def load_recent_trials(shadow_root: Path | str, *, lookback_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows: list[dict[str, Any]] = []
    for path in _iter_trial_paths(Path(shadow_root)):
        row = read_json(path)
        try:
            signal_time = _parse_utc(str(row.get('signal_timestamp_utc') or ''))
        except ValueError:
            continue
        if signal_time >= cutoff:
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get('signal_timestamp_utc') or ''))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _signal_mid_price(prediction_payload: dict[str, Any]) -> float:
    price_features = dict(prediction_payload.get('price_features') or {})
    if price_features.get('current_mid') is not None:
        return round(_safe_float(price_features.get('current_mid')), 2)
    bid = price_features.get('current_bid')
    ask = price_features.get('current_ask')
    if bid is not None and ask is not None:
        return round((_safe_float(bid) + _safe_float(ask)) / 2.0, 2)
    raise ValueError('Prediction payload does not include a signal-time quote.')


def _action_is_correct(action: str, move_usd: float, hold_band_usd: float) -> bool:
    if action == 'BUY':
        return move_usd > 0
    if action == 'SELL':
        return move_usd < 0
    if action == 'HOLD':
        return abs(move_usd) <= hold_band_usd
    return False


def _shadow_winner(
    *,
    baseline_correct: bool,
    debate_correct: bool,
    candidate_correct: bool | None,
) -> str:
    if candidate_correct is None:
        if baseline_correct and debate_correct:
            return 'both'
        if baseline_correct:
            return 'baseline'
        if debate_correct:
            return 'analyst_debate'
        return 'neither'

    correct = []
    if baseline_correct:
        correct.append('baseline')
    if debate_correct:
        correct.append('analyst_debate')
    if candidate_correct:
        correct.append('tradingagents_candidate')
    if len(correct) == 3:
        return 'all'
    if len(correct) == 2:
        return '_and_'.join(correct)
    if len(correct) == 1:
        return correct[0]
    return 'neither'


def _winner_is_two_correct(winner: str) -> bool:
    if winner == 'both':
        return True
    return '_and_' in winner and winner != 'all'


def _parse_utc(value: str) -> datetime:
    text = str(value or '').strip()
    if not text:
        raise ValueError('Empty UTC timestamp')
    if len(text) == 16 and text.endswith('Z') and text[8] == 'T':
        return datetime.strptime(text, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)


def _safe_float(value: Any) -> float:
    return float(value or 0.0)


def _latest_quote_snapshot(state_file_path: Path) -> dict[str, Any]:
    snapshot = load_state_snapshot(state_file_path)
    runtime = snapshot.get('runtime') or {}
    quote = runtime.get('latest_quote') or {}
    return quote if isinstance(quote, dict) else {}


def _iter_trial_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.glob('*/trial.json') if path.is_file())


def _find_existing_trial_for_archive(root: Path, archive_dir: Path) -> Path | None:
    for path in _iter_trial_paths(root):
        row = read_json(path)
        if str(row.get('archive_run') or '') == str(archive_dir):
            return path
    return None


def _trial_is_due(row: dict[str, Any]) -> bool:
    outcome = row.get('outcome') or {}
    if str(outcome.get('status') or '') == 'completed':
        return False
    due_text = str(row.get('evaluation_due_at_utc') or '')
    if not due_text:
        return False
    return _parse_utc(due_text) <= datetime.now(timezone.utc)


async def _fetch_evaluation_price(client, target_timestamp: datetime) -> tuple[float, str]:
    bars = await client.get_trendbar("M1", 240)
    if not bars:
        raise RuntimeError('No M1 bars returned from broker API.')
    target = target_timestamp.astimezone(timezone.utc)
    chosen = None
    for bar in bars:
        close_time = bar['open_time'].astimezone(timezone.utc) + timedelta(minutes=1)
        if close_time >= target:
            chosen = (round(float(bar['close']), 2), close_time.strftime('%Y-%m-%dT%H:%M:%SZ'))
            break
    if chosen is None:
        bar = bars[-1]
        close_time = bar['open_time'].astimezone(timezone.utc) + timedelta(minutes=1)
        chosen = (round(float(bar['close']), 2), close_time.strftime('%Y-%m-%dT%H:%M:%SZ'))
    return chosen


def _append_trial_event(root: Path, payload: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    ledger = root / 'shadow_trials.jsonl'
    with ledger.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload) + '\n')


def _send_mail(recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["To"] = recipient
    message["From"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    subprocess.run(
        ["/usr/sbin/sendmail", "-t", "-oi"],
        input=message.as_bytes(),
        check=True,
    )


def main() -> int:
    load_dotenv()
    args = _parse_args()
    if args.command == 'compare':
        created = create_shadow_trial(
            archive_root=Path(args.archive_root),
            shadow_root=Path(args.shadow_root),
            report_email=str(args.report_email),
        )
        print(str(created) if created is not None else 'NOOP')
        return 0
    if args.command == 'evaluate':
        import asyncio

        updated = asyncio.run(
            resolve_due_shadow_trials(
                shadow_root=Path(args.shadow_root),
                hold_band_usd=float(args.hold_band_usd),
            )
        )
        print(json.dumps([str(path) for path in updated], indent=2))
        return 0
    if args.command == 'report':
        subject, body = send_shadow_trial_report(
            shadow_root=Path(args.shadow_root),
            recipient=str(args.recipient),
            lookback_days=int(args.lookback_days),
            min_history_days=int(args.min_history_days),
        )
        print(subject)
        print(body)
        return 0
    raise SystemExit(f'Unknown command: {args.command}')


if __name__ == '__main__':
    raise SystemExit(main())
