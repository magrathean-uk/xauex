from __future__ import annotations

import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services import zep_graph_memory_updater as module


class _DummyGraph:
    def add(self, **kwargs):
        return None


class _DummyZep:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.graph = _DummyGraph()


def _install_fake_zep(monkeypatch) -> None:
    pkg = types.ModuleType("zep_cloud")
    client = types.ModuleType("zep_cloud.client")
    client.Zep = _DummyZep
    monkeypatch.setitem(sys.modules, "zep_cloud", pkg)
    monkeypatch.setitem(sys.modules, "zep_cloud.client", client)
    monkeypatch.setattr(module.Config, "ZEP_API_KEY", "test-key")


def _activity(agent_id: int, platform: str = "twitter") -> module.AgentActivity:
    return module.AgentActivity(
        platform=platform,
        agent_id=agent_id,
        agent_name=f"agent-{agent_id}",
        action_type="CREATE_POST",
        action_args={"content": f"post-{agent_id}"},
        round_num=1,
        timestamp="2026-04-07T00:00:00Z",
    )


def test_add_activity_drops_oldest_when_queue_is_full(monkeypatch):
    _install_fake_zep(monkeypatch)
    monkeypatch.setattr(module.ZepGraphMemoryUpdater, "MAX_QUEUE_SIZE", 2)
    updater = module.ZepGraphMemoryUpdater("graph-1")

    updater.add_activity(_activity(1))
    updater.add_activity(_activity(2))
    updater.add_activity(_activity(3))

    assert updater._activity_queue.qsize() == 2
    assert updater._dropped_count == 1
    kept = [updater._activity_queue.get_nowait().agent_id for _ in range(2)]
    assert kept == [2, 3]


def test_platform_buffer_keeps_recent_items_with_cap(monkeypatch):
    _install_fake_zep(monkeypatch)
    monkeypatch.setattr(module.ZepGraphMemoryUpdater, "MAX_PLATFORM_BUFFER_SIZE", 2)
    updater = module.ZepGraphMemoryUpdater("graph-2")

    updater._append_to_platform_buffer(_activity(1))
    updater._append_to_platform_buffer(_activity(2))
    updater._append_to_platform_buffer(_activity(3))

    assert updater._dropped_count == 1
    assert [item.agent_id for item in updater._platform_buffers["twitter"]] == [2, 3]
