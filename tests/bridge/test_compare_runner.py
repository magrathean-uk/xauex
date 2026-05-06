import json
from dataclasses import replace
from pathlib import Path

from xauex.signal.assets import resolve_asset
from xauex.signal.run import _archive_signal_run
from xauex.signal.config import SignalConfig
from xauex.signal.compare import load_archived_run, run_variant_comparison


def test_run_variant_comparison_writes_isolated_outputs(monkeypatch, tmp_path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    cfg = replace(cfg, archive_dir=str(tmp_path / "archive"))
    asset = resolve_asset("XAUUSD")
    payload = {
        "asset": "XAUUSD",
        "weights": {"price_action": 0.45, "macro_news": 0.35, "recent_memory": 0.20},
        "price_features": {"price_bias": "SELL"},
        "memory_summary": {"trade_count": 1},
        "context_excerpt": "Context body",
        "recent_runs": [{"action": "SELL"}],
        "market_snapshot": {"series": {"usd_broad_index": {"value": 120.1, "bias": "SELL"}}},
        "event_flags": {"fed_event_recent": False},
        "input_freshness": {"market_snapshot_state": "warning"},
        "context_items": [{"source_name": "Fed", "title": "Hawkish update"}],
    }
    results = {
        "actions": [{"agent_name": "price_structure", "action_type": "SELL", "content": "negative momentum"}],
        "report_markdown": "# Report\nNegative momentum and firm USD.",
        "simulation_id": None,
        "report_id": None,
        "fallback_reused": False,
        "fallback_created_at": "",
        "fallback_reason": "",
    }
    parse_calls = []

    def fake_parse_signal(*, asset, actions, report_markdown, config, prediction_payload, window_label, decision_mode=None):
        actions_by_mode = {
            "baseline": "SELL",
            "analyst_debate": "BUY",
            "tradingagents_candidate": "SELL",
        }
        confidence_by_mode = {
            "baseline": 0.52,
            "analyst_debate": 0.61,
            "tradingagents_candidate": 0.66,
        }
        parse_calls.append(
            {
                "mode": decision_mode,
                "payload": prediction_payload,
                "actions": actions,
                "report_markdown": report_markdown,
                "window_label": window_label,
            }
        )
        return {
            "schema_version": 2,
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "action": actions_by_mode[decision_mode],
            "confidence": confidence_by_mode[decision_mode],
            "reasoning": f"{decision_mode} reasoning",
            "stop_loss_distance": 12.0,
            "take_profit_distance": 24.0,
            "distance_unit": "usd",
            "timestamp_utc": "2026-04-15T00:00:00Z",
            "execution_supported": True,
            "validator_status": "reviewed",
            "consensus_state": "aligned",
            "decision_mode": decision_mode,
            "debate": (
                {
                    "mode": "analyst_debate",
                    "degraded": False,
                    "summary": "Debate complete.",
                    "bull_case": {"summary": "Bull case"},
                    "bear_case": {"summary": "Bear case"},
                }
                if decision_mode == "analyst_debate"
                else None
            ),
            "candidate_graph": (
                {
                    "mode": "tradingagents_candidate",
                    "degraded": False,
                    "summary": "Candidate complete.",
                    "scratchpad_path": str(tmp_path / "compare" / "candidate_scratchpad.jsonl"),
                    "final_decision": {"action": "SELL", "confidence": 0.66},
                }
                if decision_mode == "tradingagents_candidate"
                else None
            ),
            "llm_usage": {
                "estimated_total_cost_usd": {"baseline": 0.001, "analyst_debate": 0.0018, "tradingagents_candidate": 0.0021}[decision_mode],
                "total_tokens": {"baseline": 1000, "analyst_debate": 1800, "tradingagents_candidate": 2200}[decision_mode],
                "stages": {
                    "parser": {"model": "openai/gpt-oss-120b", "prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550, "estimated_cost_usd": 0.0007},
                },
            },
        }

    def fake_write_brief(*, output_path, signal, **kwargs):
        Path(output_path).write_text(f"# {signal['decision_mode']} brief\n", encoding="utf-8")
        meta = {
            "title": f"{signal['decision_mode']} brief",
            "path": output_path,
            "usage": {
                "model": "llama-3.1-8b-instant",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "estimated_cost_usd": 0.00001,
            },
        }
        Path(output_path).with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    monkeypatch.setattr("xauex.signal.compare.parse_signal", fake_parse_signal)
    monkeypatch.setattr("xauex.signal.compare.write_brief", fake_write_brief)

    comparison = run_variant_comparison(
        config=cfg,
        asset=asset,
        context_markdown="# Context\nFed and yields matter.",
        context_items=payload["context_items"],
        payload=payload,
        results=results,
        output_dir=tmp_path / "compare",
        full_run=True,
        window_label="morning",
    )

    assert [call["mode"] for call in parse_calls] == ["baseline", "analyst_debate", "tradingagents_candidate"]
    assert parse_calls[0]["payload"] == payload
    assert parse_calls[1]["payload"] == payload
    assert parse_calls[2]["payload"] == payload
    assert comparison["delta"]["action_changed"] is True
    assert comparison["deltas"]["tradingagents_candidate"]["action_changed"] is False
    assert comparison["deltas"]["tradingagents_candidate"]["confidence_delta"] == 0.14
    assert comparison["baseline"]["action"] == "SELL"
    assert comparison["analyst_debate"]["action"] == "BUY"
    assert comparison["tradingagents_candidate"]["action"] == "SELL"
    assert comparison["tradingagents_candidate"]["candidate_graph"]["summary"] == "Candidate complete."
    assert (tmp_path / "compare" / "baseline_signal.json").exists()
    assert (tmp_path / "compare" / "debate_signal.json").exists()
    assert (tmp_path / "compare" / "candidate_signal.json").exists()
    assert (tmp_path / "compare" / "baseline_brief.md").exists()
    assert (tmp_path / "compare" / "debate_brief.md").exists()
    assert (tmp_path / "compare" / "candidate_brief.md").exists()
    assert (tmp_path / "compare" / "candidate_evidence.json").exists()
    assert (tmp_path / "compare" / "comparison.json").exists()


def test_load_archived_run_reads_frozen_inputs(tmp_path):
    archive_dir = tmp_path / "20260415T000000Z_baseline"
    archive_dir.mkdir(parents=True)
    (archive_dir / "context.md").write_text("# Context\nFrozen context", encoding="utf-8")
    (archive_dir / "context_items.json").write_text(json.dumps([{"source_name": "Fed"}]), encoding="utf-8")
    (archive_dir / "prediction_payload.json").write_text(json.dumps({"asset": "XAUUSD", "market_snapshot": {"series": {}}}), encoding="utf-8")
    (archive_dir / "results.json").write_text(
        json.dumps(
            {
                "actions": [{"agent_name": "price_structure", "action_type": "SELL", "content": "negative momentum"}],
                "report_markdown": "# Report\nFrozen report",
                "window_label": "midday",
            }
        ),
        encoding="utf-8",
    )

    frozen = load_archived_run(archive_dir)

    assert frozen["context_markdown"] == "# Context\nFrozen context"
    assert frozen["context_items"] == [{"source_name": "Fed"}]
    assert frozen["payload"]["asset"] == "XAUUSD"
    assert frozen["results"]["window_label"] == "midday"


def test_archive_signal_run_writes_frozen_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    cfg = replace(cfg, archive_dir=str(tmp_path / "archive"))
    asset = resolve_asset("XAUUSD")
    brief_path = tmp_path / "brief.md"
    brief_path.write_text("# Brief\n", encoding="utf-8")
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"prediction_mode": "direct"}), encoding="utf-8")
    signal = {
        "schema_version": 2,
        "symbol": "XAUUSD",
        "asset_class": "metals",
        "action": "SELL",
        "confidence": 0.66,
        "reasoning": "Negative momentum.",
        "stop_loss_distance": 12.0,
        "take_profit_distance": 24.0,
        "distance_unit": "usd",
        "timestamp_utc": "2026-04-15T00:00:00Z",
        "execution_supported": True,
        "decision_mode": "baseline",
        "llm_usage": {"estimated_total_cost_usd": 0.0012},
    }
    payload = {
        "asset": "XAUUSD",
        "context_excerpt": "Context excerpt",
        "market_snapshot": {"series": {"usd_broad_index": {"value": 120.0}}},
        "context_items": [{"source_name": "Fed"}],
    }
    results = {
        "actions": [{"agent_name": "price_structure", "action_type": "SELL", "content": "negative momentum"}],
        "report_markdown": "# Report\nFrozen report",
        "window_label": "morning",
    }

    archived = _archive_signal_run(
        config=cfg,
        asset=asset,
        context_markdown="# Context\nFrozen context",
        context_items=[{"source_name": "Fed"}],
        payload=payload,
        results=results,
        signal=signal,
        brief_meta={"path": str(brief_path)},
        evidence_path=evidence_path,
    )

    assert archived.exists()
    assert json.loads((archived / "prediction_payload.json").read_text())["asset"] == "XAUUSD"
    assert json.loads((archived / "results.json").read_text())["window_label"] == "morning"
    assert (archived / "context.md").read_text() == "# Context\nFrozen context"
    assert (archived / "brief.md").read_text() == "# Brief\n"
    assert json.loads((archived / "evidence.json").read_text())["prediction_mode"] == "direct"
