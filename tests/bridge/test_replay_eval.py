import json
from dataclasses import replace

from xauex.signal.config import SignalConfig
from xauex.signal.replay_eval import run_archive_replay


def _write_archive(root, name, *, move_usd):
    archive = root / name
    archive.mkdir(parents=True)
    (archive / "context.md").write_text("# Context\nFrozen macro packet.", encoding="utf-8")
    (archive / "context_items.json").write_text(json.dumps([{"source_name": "Fed"}]), encoding="utf-8")
    (archive / "prediction_payload.json").write_text(
        json.dumps(
            {
                "asset": "XAUUSD",
                "price_features": {"current_mid": 4781.30},
                "market_snapshot": {"series": {}},
                "evaluation": {"move_usd": move_usd},
            }
        ),
        encoding="utf-8",
    )
    (archive / "results.json").write_text(
        json.dumps(
            {
                "actions": [{"agent_name": "price_structure", "action_type": "SELL", "content": "negative momentum"}],
                "report_markdown": "# Report\nFrozen report",
                "window_label": "morning",
            }
        ),
        encoding="utf-8",
    )
    return archive


def test_run_archive_replay_summarizes_three_variants(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = replace(SignalConfig.from_env(), archive_dir=str(tmp_path / "archive"))
    _write_archive(tmp_path, "20260415T070007Z_xauusd_baseline", move_usd=-8.2)
    _write_archive(tmp_path, "20260415T103007Z_xauusd_baseline", move_usd=5.1)
    (tmp_path / "20260415T103007Z_xauusd_analyst_debate").mkdir()

    def fake_comparison(**kwargs):
        return {
            "baseline": {"action": "SELL", "confidence": 0.52, "total_tokens": 100, "estimated_total_cost_usd": 0.001},
            "analyst_debate": {"action": "BUY", "confidence": 0.61, "total_tokens": 180, "estimated_total_cost_usd": 0.002},
            "tradingagents_candidate": {
                "action": "SELL",
                "confidence": 0.66,
                "total_tokens": 240,
                "estimated_total_cost_usd": 0.003,
                "candidate_graph": {"degraded": False},
            },
            "deltas": {
                "analyst_debate": {"action_changed": True},
                "tradingagents_candidate": {"action_changed": False},
            },
        }

    monkeypatch.setattr("xauex.signal.replay_eval.run_variant_comparison", fake_comparison)

    summary = run_archive_replay(
        archive_root=tmp_path,
        config=cfg,
        max_runs=1,
        output_root=tmp_path / "replay",
    )

    assert summary["runs_evaluated"] == 1
    assert summary["variants"]["baseline"]["correct"] == 1
    assert summary["variants"]["analyst_debate"]["correct"] == 0
    assert summary["variants"]["tradingagents_candidate"]["correct"] == 1
    assert summary["variants"]["tradingagents_candidate"]["degraded"] == 0
    assert summary["action_changes"]["tradingagents_candidate_vs_baseline"] == 0
    assert summary["totals"]["total_tokens"] == 520
    assert summary["totals"]["estimated_total_cost_usd"] == 0.006
