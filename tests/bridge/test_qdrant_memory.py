from __future__ import annotations

from bridge.qdrant_memory import (
    QdrantMemoryConfig,
    build_qdrant_client_kwargs,
    build_memory_documents,
    create_qdrant_client,
    load_qdrant_memory_config,
    retrieve_qdrant_memory_snippets,
)


def test_load_qdrant_memory_config_reads_remote_env(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "secret-key")
    monkeypatch.setenv("QDRANT_COLLECTION", "oracle_memory")
    monkeypatch.setenv("QDRANT_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("QDRANT_PREFER_GRPC", "1")
    monkeypatch.setenv("QDRANT_PORT", "6333")
    monkeypatch.setenv("QDRANT_GRPC_PORT", "6334")
    monkeypatch.setenv("QDRANT_PREFIX", "service/v1")

    config = load_qdrant_memory_config()

    assert config == QdrantMemoryConfig(
        enabled=True,
        collection_name="oracle_memory",
        location=None,
        url="https://qdrant.example.com:6333",
        host=None,
        port=6333,
        grpc_port=6334,
        prefer_grpc=True,
        https=True,
        api_key="secret-key",
        prefix="service/v1",
        timeout_seconds=12,
        path=None,
        force_disable_check_same_thread=False,
        check_compatibility=True,
    )


def test_build_qdrant_client_kwargs_prefers_local_path():
    config = QdrantMemoryConfig(
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
        path="/var/lib/mirofish/qdrant",
        force_disable_check_same_thread=True,
        check_compatibility=False,
    )

    kwargs = build_qdrant_client_kwargs(config)

    assert kwargs["path"] == "/var/lib/mirofish/qdrant"
    assert kwargs["force_disable_check_same_thread"] is True
    assert kwargs["check_compatibility"] is False
    assert "url" not in kwargs


def test_create_qdrant_client_uses_injected_factory():
    config = QdrantMemoryConfig(
        enabled=True,
        collection_name="oracle_memory",
        location=None,
        url="http://localhost:6333",
        host=None,
        port=6333,
        grpc_port=6334,
        prefer_grpc=False,
        https=None,
        api_key=None,
        prefix=None,
        timeout_seconds=5,
        path=None,
        force_disable_check_same_thread=False,
        check_compatibility=True,
    )

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    client = create_qdrant_client(config, client_factory=FakeClient)

    assert isinstance(client, FakeClient)
    assert captured["url"] == "http://localhost:6333"
    assert captured["timeout"] == 5
    assert "path" not in captured


def test_retrieve_qdrant_memory_snippets_is_noop_when_disabled():
    config = QdrantMemoryConfig(
        enabled=False,
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
    )

    called = False

    def client_factory(**kwargs):
        nonlocal called
        called = True
        return object()

    snippets = retrieve_qdrant_memory_snippets(
        config,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        client_factory=client_factory,
    )

    assert snippets == []
    assert called is False


def test_retrieve_qdrant_memory_snippets_uses_fake_client():
    config = QdrantMemoryConfig(
        enabled=True,
        collection_name="oracle_memory",
        location=None,
        url="http://localhost:6333",
        host=None,
        port=6333,
        grpc_port=6334,
        prefer_grpc=False,
        https=None,
        api_key=None,
        prefix=None,
        timeout_seconds=5,
        path=None,
        force_disable_check_same_thread=False,
        check_compatibility=True,
    )

    captured: dict[str, object] = {}

    class FakeClient:
        def search(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "id": "hit-1",
                    "score": 0.89,
                    "payload": {
                        "action": "BUY",
                        "summary": "Dovish Fed note aligned with gold strength.",
                        "source": "journal:alpha",
                    },
                }
            ]

    snippets = retrieve_qdrant_memory_snippets(
        config,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[{"action": "BUY"}],
        client=FakeClient(),
    )

    assert captured["collection_name"] == "oracle_memory"
    assert len(captured["query_vector"]) == 256
    assert snippets[0]["action"] == "BUY"
    assert snippets[0]["summary"].startswith("Dovish Fed note")


def test_build_memory_documents_creates_compact_payloads():
    docs = build_memory_documents(
        asset_symbol="XAUUSD",
        recent_runs=[
            {
                "action": "SELL",
                "pattern": "BEARISH_CONTINUATION_CLOSE",
                "journal": "Fed tone stayed hawkish and gold rolled over from resistance.",
                "pnl": -9.14,
                "close_time_utc": "2026-03-31T12:37:06Z",
            }
        ],
    )

    assert len(docs) == 1
    assert docs[0]["asset_symbol"] == "XAUUSD"
    assert docs[0]["action"] == "SELL"
    assert "hawkish" in docs[0]["text"].lower()


def test_retrieve_qdrant_memory_snippets_upserts_recent_runs_before_search():
    config = QdrantMemoryConfig(
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
        timeout_seconds=5,
        path="/tmp/oracle-qdrant-test",
        force_disable_check_same_thread=False,
        check_compatibility=True,
    )

    captured = {"created": 0, "upserted": 0}

    class FakeClient:
        def collection_exists(self, collection_name):
            return False

        def create_collection(self, **kwargs):
            captured["created"] += 1
            captured["create_kwargs"] = kwargs

        def upsert(self, **kwargs):
            captured["upserted"] += 1
            captured["upsert_kwargs"] = kwargs

        def search(self, **kwargs):
            captured["search_kwargs"] = kwargs
            return [
                {
                    "id": "hit-1",
                    "score": 0.93,
                    "payload": {
                        "action": "SELL",
                        "summary": "Similar bearish London morning setup from prior journal.",
                        "source": "trade_journal",
                    },
                }
            ]

    snippets = retrieve_qdrant_memory_snippets(
        config,
        asset_symbol="XAUUSD",
        context_markdown="# Context\nDollar firm, yields higher.",
        recent_runs=[
            {
                "action": "SELL",
                "pattern": "BEARISH_CONTINUATION_CLOSE",
                "journal": "Dollar firm and yields higher pressured gold lower.",
                "pnl": -3.5,
                "close_time_utc": "2026-04-01T09:00:00Z",
            }
        ],
        client=FakeClient(),
    )

    assert captured["created"] == 1
    assert captured["upserted"] == 1
    assert captured["search_kwargs"]["collection_name"] == "oracle_memory"
    assert snippets[0]["action"] == "SELL"
