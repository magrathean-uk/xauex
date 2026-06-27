"""XAUEX signal configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from xauex.signal.dsa_sidecar import DsaSidecarConfig
from xauex.signal.qdrant_memory import QdrantMemoryConfig, load_qdrant_memory_config


@dataclass(frozen=True)
class SignalConfig:
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    decision_mode: str
    parser_llm_api_key: str
    parser_llm_base_url: str
    parser_llm_model: str
    debate_analyst_model: str
    validator_llm_api_key: str
    validator_llm_base_url: str
    validator_llm_model: str
    brief_llm_api_key: str
    brief_llm_base_url: str
    brief_llm_model: str
    signal_output_path: str
    brief_output_path: str
    evidence_output_path: str
    state_file_path: str
    trade_journal_path: str
    request_timeout_seconds: float
    source_timeout_seconds: float
    source_user_agent: str
    context_output_dir: str
    project_prefix: str
    daily_cost_cap_usd: float
    cost_ledger_path: str
    archive_dir: str
    directional_state_path: str
    cme_fedwatch_api_url: str | None
    cme_fedwatch_api_key: str | None
    cme_fedwatch_api_key_header: str
    polymarket_context_enabled: bool
    polymarket_weight: float
    polymarket_search_queries: tuple[str, ...]
    polymarket_max_markets: int
    polymarket_gamma_base_url: str
    polymarket_clob_base_url: str
    dsa_sidecar: DsaSidecarConfig
    qdrant_memory: QdrantMemoryConfig

    @classmethod
    def from_env(cls) -> 'SignalConfig':
        llm_api_key = (
            os.getenv('XAUEX_SIGNAL_LLM_API_KEY')
            or os.getenv('LLM_API_KEY')
        )
        if not llm_api_key:
            raise KeyError('XAUEX_SIGNAL_LLM_API_KEY/LLM_API_KEY')

        llm_base_url = (
            os.getenv('XAUEX_SIGNAL_LLM_BASE_URL')
            or os.getenv('LLM_BASE_URL')
            or 'https://api.groq.com/openai/v1'
        )
        llm_model = (
            os.getenv('XAUEX_SIGNAL_LLM_MODEL')
            or os.getenv('LLM_MODEL_NAME')
            or 'llama-3.1-8b-instant'
        )
        decision_mode = (
            os.getenv('XAUEX_SIGNAL_DECISION_MODE')
            or 'baseline'
        ).strip().lower()
        if decision_mode not in {'baseline', 'analyst_debate', 'tradingagents_candidate'}:
            raise ValueError('XAUEX_SIGNAL_DECISION_MODE must be baseline, analyst_debate, or tradingagents_candidate')
        parser_llm_api_key = (
            os.getenv('XAUEX_SIGNAL_PARSER_LLM_API_KEY')
            or llm_api_key
        )
        parser_llm_base_url = (
            os.getenv('XAUEX_SIGNAL_PARSER_LLM_BASE_URL')
            or llm_base_url
        )
        parser_llm_model = (
            os.getenv('XAUEX_SIGNAL_PARSER_LLM_MODEL')
            or 'openai/gpt-oss-120b'
        )
        debate_analyst_model = (
            os.getenv('XAUEX_SIGNAL_DEBATE_ANALYST_MODEL')
            or 'llama-3.1-8b-instant'
        )
        validator_llm_api_key = (
            os.getenv('XAUEX_SIGNAL_VALIDATOR_LLM_API_KEY')
            or parser_llm_api_key
        )
        validator_llm_base_url = (
            os.getenv('XAUEX_SIGNAL_VALIDATOR_LLM_BASE_URL')
            or parser_llm_base_url
        )
        validator_llm_model = (
            os.getenv('XAUEX_SIGNAL_VALIDATOR_LLM_MODEL')
            or 'llama-3.3-70b-versatile'
        )
        brief_llm_api_key = (
            os.getenv('XAUEX_SIGNAL_BRIEF_LLM_API_KEY')
            or llm_api_key
        )
        brief_llm_base_url = (
            os.getenv('XAUEX_SIGNAL_BRIEF_LLM_BASE_URL')
            or llm_base_url
        )
        brief_llm_model = (
            os.getenv('XAUEX_SIGNAL_BRIEF_LLM_MODEL')
            or 'llama-3.1-8b-instant'
        )

        return cls(
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            decision_mode=decision_mode,
            parser_llm_api_key=parser_llm_api_key,
            parser_llm_base_url=parser_llm_base_url,
            parser_llm_model=parser_llm_model,
            debate_analyst_model=debate_analyst_model,
            validator_llm_api_key=validator_llm_api_key,
            validator_llm_base_url=validator_llm_base_url,
            validator_llm_model=validator_llm_model,
            brief_llm_api_key=brief_llm_api_key,
            brief_llm_base_url=brief_llm_base_url,
            brief_llm_model=brief_llm_model,
            signal_output_path=os.getenv('SIGNAL_OUTPUT_PATH', '/var/lib/xauex/cmd.json'),
            brief_output_path=os.getenv('XAUEX_SIGNAL_BRIEF_OUTPUT_PATH', '/var/lib/xauex/latest_signal_brief.md'),
            evidence_output_path=os.getenv('XAUEX_SIGNAL_EVIDENCE_OUTPUT_PATH', '/var/lib/xauex/latest_signal_evidence.json'),
            state_file_path=os.getenv('STATE_FILE_PATH', '/var/lib/xauex/state.json'),
            trade_journal_path=os.getenv('TRADE_JOURNAL_PATH', '/var/lib/xauex/trade_journal.json'),
            request_timeout_seconds=float(os.getenv('XAUEX_SIGNAL_REQUEST_TIMEOUT_SECONDS', '120')),
            source_timeout_seconds=float(os.getenv('XAUEX_SIGNAL_SOURCE_TIMEOUT_SECONDS', '20')),
            source_user_agent=os.getenv(
                'XAUEX_SIGNAL_SOURCE_USER_AGENT',
                'XAUEX-Signal/2.0 (+https://localhost)'
            ),
            context_output_dir=os.getenv('XAUEX_SIGNAL_CONTEXT_OUTPUT_DIR', './xauex/signal/context_out'),
            project_prefix=os.getenv('XAUEX_SIGNAL_PROJECT_PREFIX', 'XAUEX Macro Swarm'),
            daily_cost_cap_usd=float(os.getenv('XAUEX_SIGNAL_DAILY_COST_CAP_USD', '0.20')),
            cost_ledger_path=os.getenv('XAUEX_SIGNAL_COST_LEDGER_PATH', '/var/lib/xauex/signal_costs.jsonl'),
            archive_dir=os.getenv('XAUEX_SIGNAL_ARCHIVE_DIR', '/var/lib/xauex/signal_runs'),
            directional_state_path=os.getenv('XAUEX_SIGNAL_DIRECTIONAL_STATE_PATH', ''),
            cme_fedwatch_api_url=os.getenv('XAUEX_SIGNAL_CME_FEDWATCH_API_URL') or None,
            cme_fedwatch_api_key=os.getenv('XAUEX_SIGNAL_CME_FEDWATCH_API_KEY') or None,
            cme_fedwatch_api_key_header=os.getenv('XAUEX_SIGNAL_CME_FEDWATCH_API_KEY_HEADER', 'Authorization'),
            polymarket_context_enabled=_env_bool('XAUEX_SIGNAL_POLYMARKET_CONTEXT_ENABLED', default=False),
            polymarket_weight=_bounded_float(os.getenv('XAUEX_SIGNAL_POLYMARKET_WEIGHT', '0.25'), default=0.25, lower=0.0, upper=0.40),
            polymarket_search_queries=_split_csv_env(
                os.getenv('XAUEX_SIGNAL_POLYMARKET_SEARCH_QUERIES'),
                default=('gold', 'Fed decision'),
            ),
            polymarket_max_markets=max(1, int(os.getenv('XAUEX_SIGNAL_POLYMARKET_MAX_MARKETS', '8') or '8')),
            polymarket_gamma_base_url=os.getenv('XAUEX_SIGNAL_POLYMARKET_GAMMA_BASE_URL', 'https://gamma-api.polymarket.com').rstrip('/'),
            polymarket_clob_base_url=os.getenv('XAUEX_SIGNAL_POLYMARKET_CLOB_BASE_URL', 'https://clob.polymarket.com').rstrip('/'),
            dsa_sidecar=DsaSidecarConfig.from_env(),
            qdrant_memory=load_qdrant_memory_config(),
        )


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _bounded_float(raw: str | None, *, default: float, lower: float, upper: float) -> float:
    try:
        value = float(raw if raw is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _split_csv_env(raw: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    values = tuple(part.strip() for part in raw.split(',') if part.strip())
    return values or default
