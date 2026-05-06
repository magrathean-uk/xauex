"""Run isolated XAUEX signal variant comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from xauex.signal.assets import resolve_asset
from xauex.signal.brief_writer import write_brief
from xauex.signal.config import SignalConfig
from xauex.signal.evidence_writer import write_evidence_pack
from xauex.signal.run import _merge_usage, _window_label, build_direct_prediction_artifacts
from xauex.signal.signal_parser import parse_signal


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Compare XAUEX baseline, analyst-debate, and candidate signal modes')
    parser.add_argument('--asset', default='XAUUSD')
    parser.add_argument('--news', type=str, help='Path to a text/markdown file with market news')
    parser.add_argument('--news-text', type=str, help='Inline market context text')
    parser.add_argument('--auto-context', action='store_true', help='Auto-build a context file from the curated source registry')
    parser.add_argument('--lookback-hours', type=int, default=72)
    parser.add_argument('--max-sources', type=int, default=10)
    parser.add_argument('--max-items-per-source', type=int, default=4)
    parser.add_argument('--archive-run', type=str, help='Replay from a frozen archived baseline run directory')
    parser.add_argument('--output-dir', type=str, help='Directory for isolated comparison artifacts')
    parser.add_argument('--full-run', action='store_true', help='Write signal, brief, and evidence artifacts for all variants')
    return parser.parse_args()


def load_archived_run(path: Path | str) -> dict[str, Any]:
    archive_dir = Path(path)
    return {
        'context_markdown': (archive_dir / 'context.md').read_text(encoding='utf-8'),
        'context_items': json.loads((archive_dir / 'context_items.json').read_text(encoding='utf-8')),
        'payload': json.loads((archive_dir / 'prediction_payload.json').read_text(encoding='utf-8')),
        'results': json.loads((archive_dir / 'results.json').read_text(encoding='utf-8')),
    }


def run_variant_comparison(
    *,
    config: SignalConfig,
    asset,
    context_markdown: str,
    context_items: list[dict[str, Any]],
    payload: dict[str, Any],
    results: dict[str, Any],
    output_dir: Path,
    full_run: bool,
    window_label: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_signal = _run_variant(
        mode='baseline',
        config=config,
        asset=asset,
        payload=payload,
        results=results,
        output_dir=output_dir,
        full_run=full_run,
    )
    debate_signal = _run_variant(
        mode='analyst_debate',
        config=config,
        asset=asset,
        payload=payload,
        results=results,
        output_dir=output_dir,
        full_run=full_run,
    )
    candidate_signal = _run_variant(
        mode='tradingagents_candidate',
        config=replace(config, archive_dir=str(output_dir)),
        asset=asset,
        payload=payload,
        results=results,
        output_dir=output_dir,
        full_run=full_run,
    )
    comparison = _build_comparison(
        asset_symbol=asset.symbol,
        context_markdown=context_markdown,
        payload=payload,
        window_label=window_label,
        baseline_signal=baseline_signal,
        debate_signal=debate_signal,
        candidate_signal=candidate_signal,
    )
    (output_dir / 'comparison.json').write_text(json.dumps(comparison, indent=2), encoding='utf-8')
    return comparison


def _run_variant(
    *,
    mode: str,
    config: SignalConfig,
    asset,
    payload: dict[str, Any],
    results: dict[str, Any],
    output_dir: Path,
    full_run: bool,
) -> dict[str, Any]:
    signal = parse_signal(
        asset=asset,
        actions=results['actions'],
        report_markdown=results['report_markdown'],
        config=config,
        prediction_payload=payload,
        window_label=results.get('window_label', 'current'),
        decision_mode=mode,
    )
    signal['source'] = {
        'mode': 'compare_runner',
        'simulation_id': results.get('simulation_id'),
        'report_id': results.get('report_id'),
        'created_at': results.get('fallback_created_at', ''),
        'reason': results.get('fallback_reason', ''),
    }

    prefix = _variant_prefix(mode)
    signal_path = output_dir / f'{prefix}_signal.json'

    if full_run:
        brief_path = output_dir / f'{prefix}_brief.md'
        brief_meta = write_brief(
            asset=asset,
            signal=signal,
            actions=results['actions'],
            report_markdown=results['report_markdown'],
            simulation_id=results.get('simulation_id'),
            report_id=results.get('report_id'),
            output_path=str(brief_path),
            config=config,
        )
        signal['brief'] = brief_meta
        if isinstance(signal.get('llm_usage'), dict):
            signal['llm_usage'] = _merge_usage(signal['llm_usage'], brief_meta.get('usage') or {}, stage_name='brief')
        write_evidence_pack(
            output_path=output_dir / f'{prefix}_evidence.json',
            context_summary=str(payload.get('context_excerpt') or '')[:2400],
            recent_runs=list(payload.get('recent_runs') or []),
            weights=payload.get('weights'),
            price_features=payload.get('price_features'),
            market_snapshot=payload.get('market_snapshot'),
            input_freshness=(signal.get('decision_packet') or {}).get('input_freshness', payload.get('input_freshness')),
            validator={
                'status': signal.get('validator_status'),
                'consensus_state': signal.get('consensus_state'),
                'summary': signal.get('validator_summary'),
            },
            estimated_total_cost_usd=(signal.get('llm_usage') or {}).get('estimated_total_cost_usd'),
            prediction_mode=mode,
        )

    signal_path.write_text(json.dumps(signal, indent=2), encoding='utf-8')
    return signal


def _build_comparison(
    *,
    asset_symbol: str,
    context_markdown: str,
    payload: dict[str, Any],
    window_label: str,
    baseline_signal: dict[str, Any],
    debate_signal: dict[str, Any],
    candidate_signal: dict[str, Any],
) -> dict[str, Any]:
    baseline_usage = baseline_signal.get('llm_usage') or {}
    debate_usage = debate_signal.get('llm_usage') or {}
    candidate_usage = candidate_signal.get('llm_usage') or {}
    debate_delta = _variant_delta(baseline_signal, debate_signal)
    candidate_delta = _variant_delta(baseline_signal, candidate_signal)
    debate_delta['total_tokens_delta'] = int((debate_usage.get('total_tokens') or 0) - (baseline_usage.get('total_tokens') or 0))
    debate_delta['estimated_total_cost_usd_delta'] = _cost_delta(baseline_usage, debate_usage)
    candidate_delta['total_tokens_delta'] = int((candidate_usage.get('total_tokens') or 0) - (baseline_usage.get('total_tokens') or 0))
    candidate_delta['estimated_total_cost_usd_delta'] = _cost_delta(baseline_usage, candidate_usage)
    comparison = {
        'generated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'input_identity': {
            'asset': asset_symbol,
            'window_label': window_label,
            'context_hash': _sha256_text(context_markdown),
            'market_snapshot_hash': _sha256_json(payload.get('market_snapshot') or {}),
        },
        'baseline': _signal_summary(baseline_signal),
        'analyst_debate': _signal_summary(debate_signal),
        'tradingagents_candidate': _signal_summary(candidate_signal),
        'deltas': {
            'analyst_debate': debate_delta,
            'tradingagents_candidate': candidate_delta,
        },
        'delta': debate_delta,
    }
    return comparison


def _signal_summary(signal: dict[str, Any]) -> dict[str, Any]:
    usage = signal.get('llm_usage') or {}
    return {
        'decision_mode': signal.get('decision_mode'),
        'action': signal.get('action'),
        'confidence': signal.get('confidence'),
        'consensus_state': signal.get('consensus_state'),
        'validator_status': signal.get('validator_status'),
        'validator_summary': signal.get('validator_summary'),
        'reasoning': signal.get('reasoning'),
        'stop_loss_distance': signal.get('stop_loss_distance'),
        'take_profit_distance': signal.get('take_profit_distance'),
        'estimated_total_cost_usd': usage.get('estimated_total_cost_usd', usage.get('estimated_cost_usd')),
        'total_tokens': usage.get('total_tokens'),
        'stages': usage.get('stages', {}),
        'debate': signal.get('debate'),
        'candidate_graph': signal.get('candidate_graph'),
    }


def _variant_prefix(mode: str) -> str:
    if mode == 'baseline':
        return 'baseline'
    if mode == 'analyst_debate':
        return 'debate'
    if mode == 'tradingagents_candidate':
        return 'candidate'
    return mode.replace('-', '_')


def _variant_delta(baseline_signal: dict[str, Any], variant_signal: dict[str, Any]) -> dict[str, Any]:
    return {
        'action_changed': baseline_signal.get('action') != variant_signal.get('action'),
        'confidence_delta': round(float((variant_signal.get('confidence') or 0.0)) - float((baseline_signal.get('confidence') or 0.0)), 4),
        'stop_loss_delta': round(float((variant_signal.get('stop_loss_distance') or 0.0)) - float((baseline_signal.get('stop_loss_distance') or 0.0)), 4),
        'take_profit_delta': round(float((variant_signal.get('take_profit_distance') or 0.0)) - float((baseline_signal.get('take_profit_distance') or 0.0)), 4),
        'consensus_state_changed': baseline_signal.get('consensus_state') != variant_signal.get('consensus_state'),
        'validator_status_changed': baseline_signal.get('validator_status') != variant_signal.get('validator_status'),
    }


def _cost_delta(baseline_usage: dict[str, Any], variant_usage: dict[str, Any]) -> float:
    return round(
        float((variant_usage.get('estimated_total_cost_usd') or variant_usage.get('estimated_cost_usd') or 0.0))
        - float((baseline_usage.get('estimated_total_cost_usd') or baseline_usage.get('estimated_cost_usd') or 0.0)),
        8,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


def _resolve_live_inputs(args: argparse.Namespace, config: SignalConfig, asset) -> dict[str, Any]:
    from xauex.signal.context_builder import ContextBuilder

    if args.news and args.news_text:
        raise SystemExit('Use either --news or --news-text, not both.')

    bundle = None
    auto_context = args.auto_context or not (args.news or args.news_text)
    if args.news:
        context_markdown = Path(args.news).read_text(encoding='utf-8')
        context_items: list[dict[str, Any]] = []
    elif args.news_text:
        context_markdown = args.news_text
        context_items = []
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
        context_markdown = bundle.markdown
        context_items = [item.to_dict() for item in bundle.items]
    else:
        raise SystemExit('No context provided. Use --news, --news-text or --auto-context.')

    window_label = _window_label()
    artifacts = build_direct_prediction_artifacts(
        config=config,
        asset_symbol=asset.symbol,
        context_markdown=context_markdown,
        context_items=context_items,
        window_label=window_label,
    )
    results = dict(artifacts['results'])
    results['window_label'] = window_label
    return {
        'context_markdown': context_markdown,
        'context_items': context_items,
        'payload': artifacts['payload'],
        'results': results,
        'window_label': window_label,
    }


def main() -> None:
    load_dotenv()
    args = _parse_args()
    config = SignalConfig.from_env()
    asset = resolve_asset(args.asset)
    if args.archive_run:
        frozen = load_archived_run(Path(args.archive_run))
        context_markdown = frozen['context_markdown']
        context_items = frozen['context_items']
        payload = frozen['payload']
        results = frozen['results']
        window_label = results.get('window_label', 'current')
    else:
        frozen = _resolve_live_inputs(args, config, asset)
        context_markdown = frozen['context_markdown']
        context_items = frozen['context_items']
        payload = frozen['payload']
        results = frozen['results']
        window_label = frozen['window_label']

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output_dir = Path(args.output_dir) if args.output_dir else Path('/var/lib/xauex/comparisons') / f'{timestamp}_{asset.symbol.lower()}'
    comparison = run_variant_comparison(
        config=config,
        asset=asset,
        context_markdown=context_markdown,
        context_items=context_items,
        payload=payload,
        results=results,
        output_dir=output_dir,
        full_run=args.full_run,
        window_label=window_label,
    )
    print(json.dumps(comparison, indent=2))


if __name__ == '__main__':
    main()
