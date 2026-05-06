"""Replay archived baseline signal runs through all offline variants."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from xauex.signal.assets import resolve_asset
from xauex.signal.compare import load_archived_run, run_variant_comparison
from xauex.signal.config import SignalConfig

_VARIANTS = ("baseline", "analyst_debate", "tradingagents_candidate")


def run_archive_replay(
    *,
    archive_root: Path | str,
    config: SignalConfig,
    max_runs: int | None = None,
    output_root: Path | str | None = None,
    hold_band_usd: float = 2.0,
) -> dict[str, Any]:
    archives = _baseline_archives(Path(archive_root))
    if max_runs is not None:
        archives = archives[: max(0, int(max_runs))]
    output_base = Path(output_root) if output_root is not None else Path(config.archive_dir) / "replay_eval"
    summary = _empty_summary()

    for archive_dir in archives:
        frozen = load_archived_run(archive_dir)
        asset = resolve_asset(str((frozen.get("payload") or {}).get("asset") or "XAUUSD"))
        comparison = run_variant_comparison(
            config=config,
            asset=asset,
            context_markdown=str(frozen["context_markdown"]),
            context_items=list(frozen["context_items"]),
            payload=dict(frozen["payload"]),
            results=dict(frozen["results"]),
            output_dir=output_base / archive_dir.name,
            full_run=False,
            window_label=str((frozen["results"] or {}).get("window_label") or "current"),
        )
        _merge_comparison_summary(
            summary=summary,
            comparison=comparison,
            move_usd=_extract_move_usd(frozen),
            hold_band_usd=hold_band_usd,
        )
        summary["runs"].append(
            {
                "archive": str(archive_dir),
                "comparison_dir": str(output_base / archive_dir.name),
            }
        )
    summary["runs_evaluated"] = len(archives)
    summary["totals"]["estimated_total_cost_usd"] = round(summary["totals"]["estimated_total_cost_usd"], 8)
    return summary


def _baseline_archives(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and path.name.endswith("_baseline"))


def _empty_summary() -> dict[str, Any]:
    return {
        "runs_evaluated": 0,
        "variants": {
            variant: {
                "correct": 0,
                "incorrect": 0,
                "correctness_evaluated": 0,
                "blocked": 0,
                "degraded": 0,
                "total_tokens": 0,
                "estimated_total_cost_usd": 0.0,
            }
            for variant in _VARIANTS
        },
        "action_changes": {
            "analyst_debate_vs_baseline": 0,
            "tradingagents_candidate_vs_baseline": 0,
        },
        "totals": {
            "total_tokens": 0,
            "estimated_total_cost_usd": 0.0,
        },
        "runs": [],
    }


def _merge_comparison_summary(
    *,
    summary: dict[str, Any],
    comparison: dict[str, Any],
    move_usd: float | None,
    hold_band_usd: float,
) -> None:
    baseline_action = str((comparison.get("baseline") or {}).get("action") or "").upper()
    for variant in _VARIANTS:
        row = dict(comparison.get(variant) or {})
        bucket = summary["variants"][variant]
        action = str(row.get("action") or "").upper()
        if action == "HOLD" or str(row.get("consensus_state") or "").lower() == "blocked":
            bucket["blocked"] += 1
        if variant == "tradingagents_candidate" and bool((row.get("candidate_graph") or {}).get("degraded")):
            bucket["degraded"] += 1
        usage_tokens = int(row.get("total_tokens") or 0)
        usage_cost = float(row.get("estimated_total_cost_usd") or 0.0)
        bucket["total_tokens"] += usage_tokens
        bucket["estimated_total_cost_usd"] = round(float(bucket["estimated_total_cost_usd"]) + usage_cost, 8)
        summary["totals"]["total_tokens"] += usage_tokens
        summary["totals"]["estimated_total_cost_usd"] += usage_cost
        if move_usd is not None:
            bucket["correctness_evaluated"] += 1
            if _action_is_correct(action, move_usd, hold_band_usd):
                bucket["correct"] += 1
            else:
                bucket["incorrect"] += 1
        if variant != "baseline" and action != baseline_action:
            summary["action_changes"][f"{variant}_vs_baseline"] += 1


def _extract_move_usd(frozen: dict[str, Any]) -> float | None:
    candidates = [
        ((frozen.get("payload") or {}).get("evaluation") or {}).get("move_usd"),
        ((frozen.get("results") or {}).get("evaluation") or {}).get("move_usd"),
        (frozen.get("payload") or {}).get("future_move_2h_usd"),
        (frozen.get("results") or {}).get("future_move_2h_usd"),
    ]
    for value in candidates:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _action_is_correct(action: str, move_usd: float, hold_band_usd: float) -> bool:
    if action == "BUY":
        return move_usd > 0
    if action == "SELL":
        return move_usd < 0
    if action == "HOLD":
        return abs(move_usd) <= hold_band_usd
    return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay archived XAUEX baseline runs through all signal variants")
    parser.add_argument("--archive-root", default="/var/lib/xauex/signal_runs")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--hold-band-usd", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = _parse_args()
    summary = run_archive_replay(
        archive_root=Path(args.archive_root),
        config=SignalConfig.from_env(),
        max_runs=args.max_runs,
        output_root=Path(args.output_root) if args.output_root else None,
        hold_band_usd=float(args.hold_band_usd),
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
