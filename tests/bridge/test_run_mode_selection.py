from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from bridge.config import BridgeConfig
from bridge.qdrant_memory import QdrantMemoryConfig
import bridge.run as bridge_run
from bridge.run import build_direct_prediction_artifacts


def test_bridge_config_supports_direct_prediction_mode(monkeypatch):
    monkeypatch.setenv("BRIDGE_PREDICTION_MODE", "direct")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    cfg = BridgeConfig.from_env()
    assert cfg.prediction_mode == "direct"


def test_bridge_config_exposes_qdrant_memory_settings(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("BRIDGE_QDRANT_ENABLED", "1")
    monkeypatch.setenv("BRIDGE_QDRANT_COLLECTION", "oracle_memory")
    monkeypatch.setenv("BRIDGE_QDRANT_URL", "https://qdrant.example.com:6333")

    cfg = BridgeConfig.from_env()

    assert cfg.qdrant_memory == QdrantMemoryConfig(
        enabled=True,
        collection_name="oracle_memory",
        location=None,
        url="https://qdrant.example.com:6333",
        host=None,
        port=6333,
        grpc_port=6334,
        prefer_grpc=False,
        https=True,
        api_key=None,
        prefix=None,
        timeout_seconds=None,
        path=None,
        force_disable_check_same_thread=False,
        check_compatibility=True,
    )


def test_bridge_config_disables_qdrant_memory_by_default(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    cfg = BridgeConfig.from_env()

    assert cfg.qdrant_memory.enabled is False
    assert cfg.qdrant_memory.collection_name == "mirofish_oracle_memory"


def test_build_direct_prediction_artifacts_uses_qdrant_memory(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    cfg = BridgeConfig.from_env()
    cfg = replace(
        cfg,
        qdrant_memory=QdrantMemoryConfig(
            enabled=True,
            collection_name="oracle_memory",
            location=None,
            url=None,
            host=None,
            port=6333,
            grpc_port=6334,
            prefer_grpc=False,
            https=None,
            api_key=None,
            prefix=None,
            timeout_seconds=None,
            path=None,
            force_disable_check_same_thread=False,
            check_compatibility=True,
        ),
    )

    calls = {"retrieval": 0, "payload": 0}

    def fake_load_recent_trade_memory(path):
        return [{"action": "BUY", "journal": "Recent trade"}]

    def fake_load_state_snapshot(path):
        return {"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}}

    def fake_retrieve_qdrant_memory_snippets(*args, **kwargs):
        calls["retrieval"] += 1
        return [{"id": "hit-1", "action": "BUY", "summary": "Similar setup", "score": 0.88}]

    def fake_build_prediction_payload(**kwargs):
        calls["payload"] += 1
        assert kwargs["retrieved_memory"][0]["summary"] == "Similar setup"
        return {
            "asset": "XAUUSD",
            "weights": {"price_action": 0.45, "macro_news": 0.35, "recent_memory": 0.20},
            "price_features": {"h1_count": 4, "momentum_3": 1.0, "momentum_6": 1.0, "momentum_12": 1.0, "range_position": "MIDDLE_THIRD", "price_bias": "BUY"},
            "memory_summary": {"trade_count": 1, "net_pnl": 0.0, "buy_count": 1, "sell_count": 0, "wins": 0, "losses": 0, "notes": ["Recent trade"]},
            "context_excerpt": kwargs["context_markdown"],
            "recent_runs": kwargs["recent_runs"],
            "retrieved_memory": kwargs["retrieved_memory"],
            "retrieved_memory_count": len(kwargs["retrieved_memory"]),
        }

    monkeypatch.setattr("bridge.run.load_recent_trade_memory", fake_load_recent_trade_memory)
    monkeypatch.setattr("bridge.run.load_state_snapshot", fake_load_state_snapshot)
    monkeypatch.setattr("bridge.run.retrieve_qdrant_memory_snippets", fake_retrieve_qdrant_memory_snippets)
    monkeypatch.setattr("bridge.run.build_prediction_payload", fake_build_prediction_payload)

    artifacts = build_direct_prediction_artifacts(
        config=cfg,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nFed is dovish.",
    )

    assert calls["retrieval"] == 1
    assert calls["payload"] == 1
    assert artifacts["payload"]["retrieved_memory_count"] == 1
    assert any(action["agent_name"] == "retrieved_qdrant_memory" for action in artifacts["actions"])


def test_build_direct_prediction_artifacts_continues_when_qdrant_fails(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    cfg = BridgeConfig.from_env()
    cfg = replace(
        cfg,
        qdrant_memory=QdrantMemoryConfig(
            enabled=True,
            collection_name="oracle_memory",
            location=None,
            url=None,
            host=None,
            port=6333,
            grpc_port=6334,
            prefer_grpc=False,
            https=None,
            api_key=None,
            prefix=None,
            timeout_seconds=None,
            path=None,
            force_disable_check_same_thread=False,
            check_compatibility=True,
        ),
    )

    def fake_load_recent_trade_memory(path):
        return []

    def fake_load_state_snapshot(path):
        return {}

    def fake_retrieve_qdrant_memory_snippets(*args, **kwargs):
        raise RuntimeError("qdrant unavailable")

    def fake_build_prediction_payload(**kwargs):
        assert kwargs["retrieved_memory"] == []
        return {
            "asset": "XAUUSD",
            "weights": {"price_action": 0.45, "macro_news": 0.35, "recent_memory": 0.20},
            "price_features": {"h1_count": 0, "momentum_3": 0.0, "momentum_6": 0.0, "momentum_12": 0.0, "range_position": "UNKNOWN", "price_bias": "NEUTRAL"},
            "memory_summary": {"trade_count": 0, "net_pnl": 0.0, "buy_count": 0, "sell_count": 0, "wins": 0, "losses": 0, "notes": []},
            "context_excerpt": kwargs["context_markdown"],
            "recent_runs": [],
            "retrieved_memory": [],
            "retrieved_memory_count": 0,
        }

    monkeypatch.setattr("bridge.run.load_recent_trade_memory", fake_load_recent_trade_memory)
    monkeypatch.setattr("bridge.run.load_state_snapshot", fake_load_state_snapshot)
    monkeypatch.setattr("bridge.run.retrieve_qdrant_memory_snippets", fake_retrieve_qdrant_memory_snippets)
    monkeypatch.setattr("bridge.run.build_prediction_payload", fake_build_prediction_payload)

    artifacts = build_direct_prediction_artifacts(
        config=cfg,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nFed is dovish.",
    )

    assert artifacts["payload"]["retrieved_memory"] == []


def test_dry_run_does_not_overwrite_live_brief_or_evidence(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("BRIDGE_PREDICTION_MODE", "direct")
    monkeypatch.setenv("SIGNAL_OUTPUT_PATH", str(tmp_path / "cmd.json"))
    monkeypatch.setenv("BRIDGE_BRIEF_OUTPUT_PATH", str(tmp_path / "latest_signal_brief.md"))
    monkeypatch.setenv("BRIDGE_EVIDENCE_OUTPUT_PATH", str(tmp_path / "latest_signal_evidence.json"))
    monkeypatch.setattr(bridge_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bridge_run,
        "_parse_args",
        lambda: SimpleNamespace(
            asset="XAUUSD",
            news=None,
            news_text="# Context\nFed dovish and gold-sensitive yields softened after the release.",
            auto_context=False,
            lookback_hours=72,
            max_sources=10,
            max_items_per_source=4,
            include_manual_sources=False,
            list_sources=False,
            dump_context=None,
            dry_run=True,
            output=None,
            max_rounds=None,
        ),
    )

    brief_path = tmp_path / "latest_signal_brief.md"
    evidence_path = tmp_path / "latest_signal_evidence.json"
    signal_path = tmp_path / "cmd.json"
    brief_path.write_text("brief-before", encoding="utf-8")
    evidence_path.write_text("{\"before\": true}", encoding="utf-8")
    signal_path.write_text("{\"before\": true}", encoding="utf-8")

    def fake_build_direct_prediction_artifacts(**kwargs):
        return {
            "payload": {
                "context_excerpt": kwargs["context_markdown"],
                "recent_runs": [],
                "weights": {},
                "price_features": {},
            },
            "results": {
                "actions": [{"agent_name": "oracle", "action_type": "report"}],
                "report_markdown": "report",
                "simulation_id": None,
                "report_id": None,
                "fallback_reused": False,
                "prediction_mode": "direct",
            },
        }

    def fake_parse_signal(*, asset, actions, report_markdown, config):
        return {
            "schema_version": 2,
            "symbol": asset.symbol,
            "action": "BUY",
            "confidence": 0.7,
            "reasoning": "Fed backdrop supports gold",
        }

    calls = {"brief": 0, "evidence": 0, "signal": 0}

    def fake_write_brief(**kwargs):
        calls["brief"] += 1
        target = Path(kwargs["output_path"])
        target.write_text("brief-after", encoding="utf-8")
        return {"path": str(target)}

    def fake_write_evidence_pack(**kwargs):
        calls["evidence"] += 1
        kwargs["output_path"].write_text("{\"after\": true}", encoding="utf-8")

    def fake_write_signal(signal, output_path):
        calls["signal"] += 1
        Path(output_path).write_text("{\"after\": true}", encoding="utf-8")

    monkeypatch.setattr(bridge_run, "build_direct_prediction_artifacts", fake_build_direct_prediction_artifacts)
    monkeypatch.setattr("bridge.signal_parser.parse_signal", fake_parse_signal)
    monkeypatch.setattr("bridge.brief_writer.write_brief", fake_write_brief)
    monkeypatch.setattr("bridge.evidence_writer.write_evidence_pack", fake_write_evidence_pack)
    monkeypatch.setattr("bridge.signal_writer.write_signal", fake_write_signal)

    bridge_run.main()

    assert calls == {"brief": 0, "evidence": 0, "signal": 0}
    assert brief_path.read_text(encoding="utf-8") == "brief-before"
    assert evidence_path.read_text(encoding="utf-8") == "{\"before\": true}"
    assert signal_path.read_text(encoding="utf-8") == "{\"before\": true}"
