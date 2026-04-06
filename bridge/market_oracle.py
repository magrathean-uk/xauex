"""Generic MiroFish simulation driver for supported assets."""

from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timezone

import httpx

from bridge.assets import AssetProfile
from bridge.config import BridgeConfig

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5
_MAX_WAIT = 5400


class MarketOracle:
    """Drive a complete MiroFish simulation run via REST API."""

    def __init__(self, config: BridgeConfig, asset: AssetProfile):
        self.base = config.mirofish_url.rstrip('/')
        self.config = config
        self.asset = asset
        self.client = httpx.Client(timeout=config.request_timeout_seconds)

    def run(self, news_text: str) -> dict:
        logger.info('[ORACLE] Starting %s simulation pipeline', self.asset.symbol)
        try:
            project_id = self._generate_ontology(news_text)
            logger.info('[ORACLE] Ontology generated, project_id=%s', project_id)

            graph_id = self._build_graph(project_id)
            logger.info('[ORACLE] Graph built, graph_id=%s', graph_id)

            sim_id = self._create_simulation(project_id, graph_id)
            logger.info('[ORACLE] Simulation created, sim_id=%s', sim_id)

            self._prepare_simulation(sim_id)
            logger.info('[ORACLE] Simulation prepared')

            self._start_simulation(sim_id)
            logger.info('[ORACLE] Simulation completed')

            report_id = self._generate_report(sim_id)
            logger.info('[ORACLE] Report generated, report_id=%s', report_id)

            actions = self._get_actions(sim_id)
            report_md = self._get_report(report_id)
            logger.info('[ORACLE] Collected %d actions + report', len(actions))

            return {
                'asset': self.asset.symbol,
                'actions': actions,
                'report_markdown': report_md,
                'simulation_id': sim_id,
                'report_id': report_id,
                'fallback_reused': False,
            }
        except Exception as exc:
            if not self._is_history_fallback_candidate(exc):
                raise
            logger.warning('[ORACLE] Live pipeline failed, attempting history fallback: %s', exc)
            return self._reuse_recent_completed_run(str(exc))

    def _generate_ontology(self, news_text: str) -> str:
        filename = f'{self.asset.symbol.lower()}_context.md'
        files = {'files': (filename, io.BytesIO(news_text.encode('utf-8')), 'text/markdown')}
        data = {
            'simulation_requirement': self.asset.simulation_requirement,
            'project_name': f'{self.config.project_prefix} - {self.asset.symbol}',
        }
        resp = self.client.post(f'{self.base}/api/graph/ontology/generate', files=files, data=data)
        resp.raise_for_status()
        body = resp.json()
        if not body.get('success'):
            raise RuntimeError(f"Ontology generation failed: {body.get('error')}")
        return body['data']['project_id']

    def _build_graph(self, project_id: str) -> str:
        resp = self.client.post(
            f'{self.base}/api/graph/build',
            json={'project_id': project_id},
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get('success'):
            raise RuntimeError(f"Graph build start failed: {body.get('error')}")
        task_id = body['data']['task_id']
        graph_id = body['data'].get('graph_id')

        self._poll_task(task_id)
        if not graph_id:
            resp2 = self.client.get(f'{self.base}/api/graph/task/{task_id}')
            graph_id = resp2.json().get('data', {}).get('graph_id')
        return graph_id

    def _poll_task(self, task_id: str) -> None:
        deadline = time.monotonic() + _MAX_WAIT
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            resp = self.client.get(f'{self.base}/api/graph/task/{task_id}')
            data = resp.json().get('data', {})
            status = data.get('status', '')
            if status in ('completed', 'complete', 'done'):
                return
            if status in ('failed', 'error'):
                raise RuntimeError(f'Task {task_id} failed: {data}')
            logger.debug('[ORACLE] Task %s status: %s', task_id, status)
        raise TimeoutError(f'Task {task_id} did not complete within {_MAX_WAIT}s')

    def _create_simulation(self, project_id: str, graph_id: str) -> str:
        resp = self.client.post(
            f'{self.base}/api/simulation/create',
            json={'project_id': project_id, 'graph_id': graph_id},
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get('success'):
            raise RuntimeError(f"Simulation create failed: {body.get('error')}")
        return body['data']['simulation_id']

    def _prepare_simulation(self, sim_id: str) -> None:
        resp = self.client.post(
            f'{self.base}/api/simulation/prepare',
            json={'simulation_id': sim_id},
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get('success'):
            raise RuntimeError(f"Simulation prepare failed: {body.get('error')}")

        deadline = time.monotonic() + _MAX_WAIT
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            resp = self.client.post(
                f'{self.base}/api/simulation/prepare/status',
                json={'simulation_id': sim_id},
            )
            data = resp.json().get('data', {})
            status = data.get('status', '')
            if status in ('ready', 'completed'):
                return
            if status in ('failed', 'error'):
                raise RuntimeError(f'Prepare failed: {data}')
            logger.debug('[ORACLE] Prepare status: %s', status)
        raise TimeoutError(f'Prepare did not complete within {_MAX_WAIT}s')

    def _start_simulation(self, sim_id: str) -> None:
        payload = {'simulation_id': sim_id, 'platform': 'parallel'}
        if self.config.simulation_max_rounds is not None:
            payload['max_rounds'] = self.config.simulation_max_rounds

        resp = self.client.post(
            f'{self.base}/api/simulation/start',
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get('success'):
            raise RuntimeError(f"Simulation start failed: {body.get('error')}")

        deadline = time.monotonic() + _MAX_WAIT
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            resp = self.client.get(f'{self.base}/api/simulation/{sim_id}/run-status')
            data = resp.json().get('data', {})
            status = data.get('runner_status', data.get('status', ''))
            if status in ('completed', 'done', 'finished'):
                return
            if status in ('failed', 'error'):
                raise RuntimeError(f'Simulation run failed: {data}')
            logger.debug('[ORACLE] Run status: %s (round %s/%s)',
                         status, data.get('current_round'), data.get('total_rounds'))
        raise TimeoutError(f'Simulation did not complete within {_MAX_WAIT}s')

    def _generate_report(self, sim_id: str) -> str:
        resp = self.client.post(
            f'{self.base}/api/report/generate',
            json={'simulation_id': sim_id},
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get('success'):
            raise RuntimeError(f"Report generate failed: {body.get('error')}")

        report_id = body['data'].get('report_id')
        task_id = body['data'].get('task_id')
        if report_id and body['data'].get('status') == 'completed':
            return report_id

        deadline = time.monotonic() + _MAX_WAIT
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            payload = {'simulation_id': sim_id}
            if task_id:
                payload['task_id'] = task_id
            resp = self.client.post(
                f'{self.base}/api/report/generate/status',
                json=payload,
            )
            data = resp.json().get('data', {})
            status = data.get('status', '')
            if status in ('completed', 'done'):
                return data.get('report_id', report_id)
            if status in ('failed', 'error'):
                raise RuntimeError(f'Report generation failed: {data}')
            logger.debug('[ORACLE] Report status: %s', status)
        raise TimeoutError(f'Report did not complete within {_MAX_WAIT}s')

    def _get_actions(self, sim_id: str) -> list:
        resp = self.client.get(
            f'{self.base}/api/simulation/{sim_id}/actions',
            params={'limit': 500},
        )
        resp.raise_for_status()
        return resp.json().get('data', {}).get('actions', [])

    def _get_report(self, report_id: str) -> str:
        resp = self.client.get(f'{self.base}/api/report/{report_id}')
        resp.raise_for_status()
        data = resp.json().get('data', {})
        return data.get('content', data.get('markdown', ''))

    def close(self) -> None:
        self.client.close()

    def _is_history_fallback_candidate(self, exc: Exception) -> bool:
        message = str(exc).lower()
        markers = (
            'episode usage limit',
            'zep',
            'graph build',
            'build failed',
            '429',
            'rate limit',
            'quota',
            'forbidden',
        )
        return any(marker in message for marker in markers)

    def _reuse_recent_completed_run(self, failure_reason: str) -> dict:
        resp = self.client.get(
            f'{self.base}/api/simulation/history',
            params={'limit': self.config.history_fallback_limit},
        )
        resp.raise_for_status()
        payload = resp.json()
        candidates = payload.get('data', [])
        chosen = self._select_history_candidate(candidates)
        if chosen is None:
            raise RuntimeError(f'No reusable history fallback found after failure: {failure_reason}')

        sim_id = chosen['simulation_id']
        report_id = chosen['report_id']
        actions = self._get_actions(sim_id)
        report_md = self._get_report(report_id)
        created_at = chosen.get('created_at', '')
        logger.warning(
            '[ORACLE] Reusing simulation=%s report=%s created_at=%s because live pipeline failed: %s',
            sim_id,
            report_id,
            created_at,
            failure_reason,
        )
        return {
            'asset': self.asset.symbol,
            'actions': actions,
            'report_markdown': report_md,
            'simulation_id': sim_id,
            'report_id': report_id,
            'fallback_reused': True,
            'fallback_reason': failure_reason,
            'fallback_created_at': created_at,
        }

    def _select_history_candidate(self, candidates: list[dict]) -> dict | None:
        compatible: list[tuple[tuple[int, int, str], dict]] = []
        for candidate in candidates:
            report_id = candidate.get('report_id')
            simulation_id = candidate.get('simulation_id')
            if not report_id or not simulation_id:
                continue
            requirement = str(candidate.get('simulation_requirement', '') or '')
            haystack = f"{candidate.get('project_id', '')} {requirement}".lower()
            if self.asset.symbol.lower() not in haystack:
                continue

            same_mode = self._requirement_mode_score(requirement)
            same_day = self._created_today_score(candidate.get('created_at'))
            created_at = str(candidate.get('created_at', '') or '')
            compatible.append(((same_mode, same_day, created_at), candidate))

        if not compatible:
            return None

        compatible.sort(key=lambda item: item[0], reverse=True)
        return compatible[0][1]

    def _requirement_mode_score(self, requirement: str) -> int:
        normalized = requirement.lower()
        if 'london' in self.asset.simulation_requirement.lower():
            return int('london' in normalized and ('morning' in normalized or 'late-morning' in normalized))
        return 1

    def _created_today_score(self, created_at: str | None) -> int:
        if not created_at:
            return 0
        try:
            created_dt = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
        except ValueError:
            return 0
        now_utc = datetime.now(timezone.utc)
        return int(created_dt.date() == now_utc.date())
