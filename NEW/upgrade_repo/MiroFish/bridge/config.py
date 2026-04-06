"""Bridge configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeConfig:
    mirofish_url: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    signal_output_path: str
    request_timeout_seconds: float
    source_timeout_seconds: float
    source_user_agent: str
    context_output_dir: str
    project_prefix: str

    @classmethod
    def from_env(cls) -> 'BridgeConfig':
        return cls(
            mirofish_url=os.getenv('MIROFISH_URL', 'http://localhost:5001'),
            deepseek_api_key=os.environ['DEEPSEEK_API_KEY'],
            deepseek_base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1'),
            deepseek_model=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
            signal_output_path=os.getenv('SIGNAL_OUTPUT_PATH', '/var/lib/xauex/cmd.json'),
            request_timeout_seconds=float(os.getenv('BRIDGE_REQUEST_TIMEOUT_SECONDS', '120')),
            source_timeout_seconds=float(os.getenv('BRIDGE_SOURCE_TIMEOUT_SECONDS', '20')),
            source_user_agent=os.getenv(
                'BRIDGE_SOURCE_USER_AGENT',
                'MiroFish-XAUEX-Bridge/2.0 (+https://localhost)'
            ),
            context_output_dir=os.getenv('BRIDGE_CONTEXT_OUTPUT_DIR', './bridge/context_out'),
            project_prefix=os.getenv('BRIDGE_PROJECT_PREFIX', 'MiroFish Macro Swarm'),
        )
