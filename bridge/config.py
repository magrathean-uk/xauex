"""Bridge configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from bridge.qdrant_memory import QdrantMemoryConfig, load_qdrant_memory_config


@dataclass(frozen=True)
class BridgeConfig:
    mirofish_url: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    parser_llm_api_key: str
    parser_llm_base_url: str
    parser_llm_model: str
    prediction_mode: str
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
    simulation_max_rounds: int | None
    history_fallback_limit: int
    qdrant_memory: QdrantMemoryConfig

    @classmethod
    def from_env(cls) -> 'BridgeConfig':
        llm_api_key = (
            os.getenv('BRIDGE_LLM_API_KEY')
            or os.getenv('LLM_API_KEY')
            or os.getenv('DEEPSEEK_API_KEY')
        )
        if not llm_api_key:
            raise KeyError('BRIDGE_LLM_API_KEY/LLM_API_KEY/DEEPSEEK_API_KEY')

        llm_base_url = (
            os.getenv('BRIDGE_LLM_BASE_URL')
            or os.getenv('LLM_BASE_URL')
            or os.getenv('DEEPSEEK_BASE_URL')
            or 'https://api.deepseek.com/v1'
        )
        llm_model = (
            os.getenv('BRIDGE_LLM_MODEL')
            or os.getenv('LLM_MODEL_NAME')
            or os.getenv('DEEPSEEK_MODEL')
            or 'deepseek-chat'
        )
        parser_llm_api_key = (
            os.getenv('BRIDGE_PARSER_LLM_API_KEY')
            or llm_api_key
        )
        parser_llm_base_url = (
            os.getenv('BRIDGE_PARSER_LLM_BASE_URL')
            or llm_base_url
        )
        parser_llm_model = (
            os.getenv('BRIDGE_PARSER_LLM_MODEL')
            or llm_model
        )

        raw_max_rounds = os.getenv('BRIDGE_MAX_ROUNDS', '').strip()
        simulation_max_rounds = int(raw_max_rounds) if raw_max_rounds else None
        return cls(
            mirofish_url=os.getenv('MIROFISH_URL', 'http://localhost:5001'),
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            parser_llm_api_key=parser_llm_api_key,
            parser_llm_base_url=parser_llm_base_url,
            parser_llm_model=parser_llm_model,
            prediction_mode=os.getenv('BRIDGE_PREDICTION_MODE', 'direct').strip().lower(),
            signal_output_path=os.getenv('SIGNAL_OUTPUT_PATH', '/var/lib/xauex/cmd.json'),
            brief_output_path=os.getenv('BRIDGE_BRIEF_OUTPUT_PATH', '/var/lib/xauex/latest_signal_brief.md'),
            evidence_output_path=os.getenv('BRIDGE_EVIDENCE_OUTPUT_PATH', '/var/lib/xauex/latest_signal_evidence.json'),
            state_file_path=os.getenv('STATE_FILE_PATH', '/var/lib/xauex/state.json'),
            trade_journal_path=os.getenv('TRADE_JOURNAL_PATH', '/var/lib/xauex/trade_journal.json'),
            request_timeout_seconds=float(os.getenv('BRIDGE_REQUEST_TIMEOUT_SECONDS', '120')),
            source_timeout_seconds=float(os.getenv('BRIDGE_SOURCE_TIMEOUT_SECONDS', '20')),
            source_user_agent=os.getenv(
                'BRIDGE_SOURCE_USER_AGENT',
                'MiroFish-XAUEX-Bridge/2.0 (+https://localhost)'
            ),
            context_output_dir=os.getenv('BRIDGE_CONTEXT_OUTPUT_DIR', './bridge/context_out'),
            project_prefix=os.getenv('BRIDGE_PROJECT_PREFIX', 'MiroFish Macro Swarm'),
            simulation_max_rounds=simulation_max_rounds,
            history_fallback_limit=int(os.getenv('BRIDGE_HISTORY_FALLBACK_LIMIT', '20')),
            qdrant_memory=load_qdrant_memory_config(),
        )
