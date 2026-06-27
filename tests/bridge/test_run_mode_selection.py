from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest

from xauex.signal.config import SignalConfig
from xauex.signal.qdrant_memory import QdrantMemoryConfig
import xauex.signal.run as signal_run
from xauex.signal.run import build_direct_prediction_artifacts


def test_signal_config_loads_required_llm_settings(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    assert cfg.llm_api_key == "test-key"


def test_signal_runner_loads_repo_env_explicitly(monkeypatch, capsys):
    captured = {}

    def fake_load_dotenv(path=None, *args, **kwargs):
        captured["path"] = path
        return True

    monkeypatch.setattr(signal_run, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(
        signal_run,
        "_parse_args",
        lambda: SimpleNamespace(
            asset="XAUUSD",
            news=None,
            news_text=None,
            auto_context=False,
            lookback_hours=72,
            max_sources=10,
            max_items_per_source=4,
            include_manual_sources=False,
            list_sources=True,
            dump_context=None,
            dry_run=True,
            output=None,
            window_label=None,
        ),
    )
    monkeypatch.setattr(signal_run, "get_sources", lambda *args, **kwargs: [])

    signal_run.main()

    assert Path(captured["path"]) == Path(signal_run.__file__).resolve().parents[2] / ".env"
    assert capsys.readouterr().out.strip() == "[]"


def test_signal_config_exposes_qdrant_memory_settings(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_QDRANT_ENABLED", "1")
    monkeypatch.setenv("XAUEX_SIGNAL_QDRANT_COLLECTION", "oracle_memory")
    monkeypatch.setenv("XAUEX_SIGNAL_QDRANT_URL", "https://qdrant.example.com:6333")

    cfg = SignalConfig.from_env()

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


def test_signal_config_disables_qdrant_memory_by_default(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")

    cfg = SignalConfig.from_env()

    assert cfg.qdrant_memory.enabled is False
    assert cfg.qdrant_memory.collection_name == "xauex_signal_memory"


def test_signal_config_exposes_validator_and_budget_defaults(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.delenv("XAUEX_SIGNAL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_PARSER_LLM_MODEL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_VALIDATOR_LLM_MODEL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_BRIEF_LLM_MODEL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_DECISION_MODE", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_DEBATE_ANALYST_MODEL", raising=False)
    monkeypatch.delenv("XAUEX_SIGNAL_ARCHIVE_DIR", raising=False)

    cfg = SignalConfig.from_env()

    assert cfg.llm_base_url == "https://api.groq.com/openai/v1"
    assert cfg.llm_model == "llama-3.1-8b-instant"
    assert cfg.parser_llm_model == "openai/gpt-oss-120b"
    assert cfg.validator_llm_model == "llama-3.3-70b-versatile"
    assert cfg.brief_llm_model == "llama-3.1-8b-instant"
    assert cfg.decision_mode == "baseline"
    assert cfg.debate_analyst_model == "llama-3.1-8b-instant"
    assert cfg.archive_dir == "/var/lib/xauex/signal_runs"
    assert cfg.daily_cost_cap_usd > 0


def test_signal_config_accepts_tradingagents_candidate_mode(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_DECISION_MODE", "tradingagents_candidate")

    cfg = SignalConfig.from_env()

    assert cfg.decision_mode == "tradingagents_candidate"


def test_signal_config_rejects_unknown_decision_mode(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_DECISION_MODE", "fully_autonomous")

    with pytest.raises(ValueError, match="baseline, analyst_debate, or tradingagents_candidate"):
        SignalConfig.from_env()


def test_signal_config_exposes_optional_fedwatch_api_settings(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_CME_FEDWATCH_API_URL", "https://api.example.com/fedwatch")
    monkeypatch.setenv("XAUEX_SIGNAL_CME_FEDWATCH_API_KEY", "secret")
    monkeypatch.setenv("XAUEX_SIGNAL_CME_FEDWATCH_API_KEY_HEADER", "X-API-Key")

    cfg = SignalConfig.from_env()

    assert cfg.cme_fedwatch_api_url == "https://api.example.com/fedwatch"
    assert cfg.cme_fedwatch_api_key == "secret"
    assert cfg.cme_fedwatch_api_key_header == "X-API-Key"


def test_signal_config_exposes_optional_polymarket_settings(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("XAUEX_SIGNAL_POLYMARKET_CONTEXT_ENABLED", "1")
    monkeypatch.setenv("XAUEX_SIGNAL_POLYMARKET_WEIGHT", "0.30")
    monkeypatch.setenv("XAUEX_SIGNAL_POLYMARKET_SEARCH_QUERIES", "gold,Fed decision")
    monkeypatch.setenv("XAUEX_SIGNAL_POLYMARKET_MAX_MARKETS", "6")

    cfg = SignalConfig.from_env()

    assert cfg.polymarket_context_enabled is True
    assert cfg.polymarket_weight == 0.30
    assert cfg.polymarket_search_queries == ("gold", "Fed decision")
    assert cfg.polymarket_max_markets == 6


def test_build_direct_prediction_artifacts_uses_qdrant_memory(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
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

    monkeypatch.setattr("xauex.signal.run.load_recent_trade_memory", fake_load_recent_trade_memory)
    monkeypatch.setattr("xauex.signal.run.load_state_snapshot", fake_load_state_snapshot)
    monkeypatch.setattr("xauex.signal.run.retrieve_qdrant_memory_snippets", fake_retrieve_qdrant_memory_snippets)
    monkeypatch.setattr("xauex.signal.run.build_prediction_payload", fake_build_prediction_payload)

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
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
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

    monkeypatch.setattr("xauex.signal.run.load_recent_trade_memory", fake_load_recent_trade_memory)
    monkeypatch.setattr("xauex.signal.run.load_state_snapshot", fake_load_state_snapshot)
    monkeypatch.setattr("xauex.signal.run.retrieve_qdrant_memory_snippets", fake_retrieve_qdrant_memory_snippets)
    monkeypatch.setattr("xauex.signal.run.build_prediction_payload", fake_build_prediction_payload)

    artifacts = build_direct_prediction_artifacts(
        config=cfg,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nFed is dovish.",
    )

    assert artifacts["payload"]["retrieved_memory"] == []


def test_build_direct_prediction_artifacts_preserves_policy_context_in_payload(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    captured = {}

    def fake_load_recent_trade_memory(path):
        return []

    def fake_load_state_snapshot(path):
        return {"recent_h1_closes": [10, 11, 12], "levels": {}}

    def fake_build_market_snapshot(*, asset, config, context_items, window_label):
        return {
            "series": {},
            "fedwatch": {"status": "ok"},
            "event_flags": {},
            "input_freshness": {"summary": "fresh"},
            "overall_bias": "NEUTRAL",
            "missing_series": [],
            "policy_context": {
                "status": "watch",
                "next_fomc_date": "2026-04-29",
                "days_to_fomc": 15,
                "fomc_window_state": "pre",
                "summary": "FOMC watch is active.",
            },
        }

    def fake_build_prediction_payload(**kwargs):
        captured["market_snapshot"] = kwargs["market_snapshot"]
        return {
            "asset": "XAUUSD",
            "weights": {"price_action": 0.45, "macro_news": 0.35, "recent_memory": 0.20},
            "price_features": {"h1_count": 3, "momentum_3": 0.0, "momentum_6": 0.0, "momentum_12": 0.0, "range_position": "MIDDLE_THIRD", "price_bias": "NEUTRAL"},
            "memory_summary": {"trade_count": 0, "net_pnl": 0.0, "buy_count": 0, "sell_count": 0, "wins": 0, "losses": 0, "notes": []},
            "context_excerpt": kwargs["context_markdown"],
            "recent_runs": kwargs["recent_runs"],
            "retrieved_memory": kwargs["retrieved_memory"],
            "retrieved_memory_count": len(kwargs["retrieved_memory"]),
            "market_snapshot": kwargs["market_snapshot"],
        }

    monkeypatch.setattr("xauex.signal.run.load_recent_trade_memory", fake_load_recent_trade_memory)
    monkeypatch.setattr("xauex.signal.run.load_state_snapshot", fake_load_state_snapshot)
    monkeypatch.setattr("xauex.signal.market_snapshot.build_market_snapshot", fake_build_market_snapshot)
    monkeypatch.setattr("xauex.signal.run.build_prediction_payload", fake_build_prediction_payload)

    artifacts = build_direct_prediction_artifacts(
        config=cfg,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nFed is dovish.",
    )

    assert captured["market_snapshot"]["policy_context"]["status"] == "watch"
    assert artifacts["payload"]["market_snapshot"]["policy_context"]["summary"] == "FOMC watch is active."


def test_dry_run_does_not_overwrite_live_brief_or_evidence(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SIGNAL_OUTPUT_PATH", str(tmp_path / "cmd.json"))
    monkeypatch.setenv("XAUEX_SIGNAL_BRIEF_OUTPUT_PATH", str(tmp_path / "latest_signal_brief.md"))
    monkeypatch.setenv("XAUEX_SIGNAL_EVIDENCE_OUTPUT_PATH", str(tmp_path / "latest_signal_evidence.json"))
    monkeypatch.setenv("XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH", str(tmp_path / "directional_state.json"))
    monkeypatch.setattr(signal_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        signal_run,
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

    captured = {}

    def fake_parse_signal(*, asset, actions, report_markdown, config, prediction_payload=None, window_label=None):
        captured["directional_state_path"] = config.directional_state_path
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

    monkeypatch.setattr(signal_run, "build_direct_prediction_artifacts", fake_build_direct_prediction_artifacts)
    monkeypatch.setattr("xauex.signal.signal_parser.parse_signal", fake_parse_signal)
    monkeypatch.setattr("xauex.signal.brief_writer.write_brief", fake_write_brief)
    monkeypatch.setattr("xauex.signal.evidence_writer.write_evidence_pack", fake_write_evidence_pack)
    monkeypatch.setattr("xauex.signal.signal_writer.write_signal", fake_write_signal)

    signal_run.main()

    assert calls == {"brief": 0, "evidence": 0, "signal": 0}
    assert captured["directional_state_path"] == ""
    assert brief_path.read_text(encoding="utf-8") == "brief-before"
    assert evidence_path.read_text(encoding="utf-8") == "{\"before\": true}"
    assert signal_path.read_text(encoding="utf-8") == "{\"before\": true}"
