"""Local Qdrant-backed memory helpers for the XAUEX signal pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import json
import os
import re
import uuid
from typing import Any, Callable, Mapping


_DEFAULT_COLLECTION_NAME = "xauex_signal_memory"
_DEFAULT_PORT = 6333
_DEFAULT_GRPC_PORT = 6334
_EMBED_DIMENSIONS = 256


@dataclass(frozen=True)
class QdrantMemoryConfig:
    enabled: bool
    collection_name: str
    location: str | None
    url: str | None
    host: str | None
    port: int
    grpc_port: int
    prefer_grpc: bool
    https: bool | None
    api_key: str | None
    prefix: str | None
    timeout_seconds: int | None
    path: str | None
    force_disable_check_same_thread: bool
    check_compatibility: bool


@dataclass(frozen=True)
class _FallbackVectorParams:
    size: int
    distance: str


@dataclass(frozen=True)
class _FallbackPointStruct:
    id: str
    vector: list[float]
    payload: dict[str, Any]


class _FallbackDistance:
    COSINE = "cosine"


def load_qdrant_memory_config(env: Mapping[str, str] | None = None) -> QdrantMemoryConfig:
    source = env or os.environ
    location = _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_LOCATION", "QDRANT_LOCATION"))
    path = _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_PATH", "QDRANT_PATH"))
    url = _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_URL", "QDRANT_URL"))
    host = _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_HOST", "QDRANT_HOST"))
    api_key = _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_API_KEY", "QDRANT_API_KEY"))
    collection_name = (
        _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_COLLECTION", "QDRANT_COLLECTION"))
        or _DEFAULT_COLLECTION_NAME
    )
    prefix = _clean_str(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_PREFIX", "QDRANT_PREFIX"))
    timeout_seconds = _parse_int(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_TIMEOUT_SECONDS", "QDRANT_TIMEOUT_SECONDS"))
    port = _parse_int(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_PORT", "QDRANT_PORT"), default=_DEFAULT_PORT)
    grpc_port = _parse_int(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_GRPC_PORT", "QDRANT_GRPC_PORT"), default=_DEFAULT_GRPC_PORT)
    prefer_grpc = _parse_bool(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_PREFER_GRPC", "QDRANT_PREFER_GRPC"), default=False)
    https = _parse_https_flag(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_HTTPS", "QDRANT_HTTPS"), url=url)
    force_disable_check_same_thread = _parse_bool(
        _env_lookup(source, "XAUEX_SIGNAL_QDRANT_FORCE_DISABLE_CHECK_SAME_THREAD", "QDRANT_FORCE_DISABLE_CHECK_SAME_THREAD"),
        default=False,
    )
    check_compatibility = _parse_bool(
        _env_lookup(source, "XAUEX_SIGNAL_QDRANT_CHECK_COMPATIBILITY", "QDRANT_CHECK_COMPATIBILITY"),
        default=True,
    )

    explicit_enabled = _parse_optional_bool(_env_lookup(source, "XAUEX_SIGNAL_QDRANT_ENABLED", "QDRANT_ENABLED"))
    if explicit_enabled is None:
        enabled = any(
            value is not None
            for value in (location, path, url, host, api_key, prefix, timeout_seconds)
        )
    else:
        enabled = explicit_enabled

    return QdrantMemoryConfig(
        enabled=enabled,
        collection_name=collection_name,
        location=location,
        url=url,
        host=host,
        port=port,
        grpc_port=grpc_port,
        prefer_grpc=prefer_grpc,
        https=https,
        api_key=api_key,
        prefix=prefix,
        timeout_seconds=timeout_seconds,
        path=path,
        force_disable_check_same_thread=force_disable_check_same_thread,
        check_compatibility=check_compatibility,
    )


def build_qdrant_client_kwargs(config: QdrantMemoryConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}

    if config.location is not None:
        kwargs["location"] = config.location
    if config.path is not None:
        kwargs["path"] = config.path
    if config.url is not None:
        kwargs["url"] = config.url
    if config.host is not None:
        kwargs["host"] = config.host
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key
    if config.prefix is not None:
        kwargs["prefix"] = config.prefix
    if config.timeout_seconds is not None:
        kwargs["timeout"] = config.timeout_seconds
    if config.prefer_grpc:
        kwargs["prefer_grpc"] = True
    if config.https is not None:
        kwargs["https"] = config.https
    if config.port != _DEFAULT_PORT:
        kwargs["port"] = config.port
    if config.grpc_port != _DEFAULT_GRPC_PORT:
        kwargs["grpc_port"] = config.grpc_port
    if config.force_disable_check_same_thread:
        kwargs["force_disable_check_same_thread"] = True
    if not config.check_compatibility:
        kwargs["check_compatibility"] = False

    return kwargs


def create_qdrant_client(
    config: QdrantMemoryConfig,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    factory = client_factory or _load_default_client_factory()
    return factory(**build_qdrant_client_kwargs(config))


def retrieve_qdrant_memory_snippets(
    config: QdrantMemoryConfig,
    *,
    asset_symbol: str,
    context_markdown: str,
    recent_runs: list[dict[str, Any]] | None = None,
    limit: int = 4,
    client: Any | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    if not config.enabled:
        return []

    qdrant_client = client or create_qdrant_client(config, client_factory=client_factory)
    documents = build_memory_documents(asset_symbol=asset_symbol, recent_runs=recent_runs or [])
    if documents:
        _sync_memory_documents(qdrant_client, config.collection_name, documents)
    query_text = _build_memory_query(
        asset_symbol=asset_symbol,
        context_markdown=context_markdown,
        recent_runs=recent_runs or [],
    )
    hits = _search_qdrant(qdrant_client, config.collection_name, query_text, limit=limit)
    snippets: list[dict[str, Any]] = []
    for hit in hits:
        normalized = _normalize_memory_hit(hit)
        if normalized:
            snippets.append(normalized)
    return snippets


def build_memory_documents(
    *,
    asset_symbol: str,
    recent_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for index, item in enumerate(recent_runs[-64:]):
        action = str(item.get("action", "UNKNOWN") or "UNKNOWN").upper()
        pattern = str(item.get("pattern", "") or "").strip()
        journal = str(item.get("journal", "") or "").strip()
        pnl = float(item.get("pnl", 0.0) or 0.0)
        close_time = str(item.get("close_time_utc", "") or "").strip()
        text_parts = [asset_symbol, action]
        if pattern:
            text_parts.append(pattern)
        if journal:
            text_parts.append(journal)
        text_parts.append(f"pnl={pnl:.2f}")
        if close_time:
            text_parts.append(close_time)
        text = " | ".join(part for part in text_parts if part)
        docs.append(
            {
                "id": _memory_point_id(asset_symbol, close_time or str(index), journal or pattern or action),
                "asset_symbol": asset_symbol,
                "action": action,
                "pattern": pattern,
                "summary": journal[:220],
                "journal": journal[:400],
                "pnl": pnl,
                "close_time_utc": close_time,
                "source": "trade_journal",
                "label": pattern[:80],
                "text": text[:1200],
            }
        )
    return docs


def describe_qdrant_target(config: QdrantMemoryConfig) -> str:
    if config.path:
        return f"local-path:{config.path}"
    if config.location:
        return f"location:{config.location}"
    if config.url:
        return config.url
    if config.host:
        return f"{config.host}:{config.port}"
    return "qdrant:unconfigured"


def _load_default_client_factory() -> Callable[..., Any]:
    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError(
            "qdrant-client is not installed; add it before enabling the memory layer"
        ) from exc
    return QdrantClient


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_bool(value: str | None, *, default: bool) -> bool:
    parsed = _parse_optional_bool(value)
    return default if parsed is None else parsed


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "":
        return None
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _parse_int(value: str | None, *, default: int | None = None) -> int | None:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_https_flag(value: str | None, *, url: str | None) -> bool | None:
    parsed = _parse_optional_bool(value)
    if parsed is not None:
        return parsed
    if url is None:
        return None
    return url.lower().startswith("https://")


def _env_lookup(env: Mapping[str, str], primary: str, fallback: str) -> str | None:
    return env.get(primary) or env.get(fallback)


def _load_default_vector_params() -> _FallbackVectorParams:
    return _FallbackVectorParams(size=_EMBED_DIMENSIONS, distance=_FallbackDistance.COSINE)


def _load_default_point_struct(*, point_id: str, vector: list[float], payload: dict[str, Any]) -> _FallbackPointStruct:
    return _FallbackPointStruct(id=point_id, vector=vector, payload=payload)


def _memory_point_id(asset_symbol: str, seed: str, extra: str) -> str:
    digest = hashlib.sha1(f"{asset_symbol}|{seed}|{extra}".encode("utf-8")).hexdigest()[:24]
    return str(uuid.UUID(digest.ljust(32, "0")))


def _normalize_memory_hit(hit: Any) -> dict[str, Any] | None:
    payload = getattr(hit, "payload", None) if not isinstance(hit, dict) else hit.get("payload")
    if not isinstance(payload, dict):
        return None
    return {
        "id": str(getattr(hit, "id", hit.get("id", "")) if isinstance(hit, dict) else getattr(hit, "id", "")),
        "score": float(getattr(hit, "score", hit.get("score", 0.0)) if isinstance(hit, dict) else getattr(hit, "score", 0.0) or 0.0),
        "action": str(payload.get("action", "MEMORY") or "MEMORY").upper(),
        "summary": str(payload.get("summary", "") or "")[:220],
        "source": str(payload.get("source", "") or "")[:80],
        "label": str(payload.get("label", "") or "")[:80],
    }


def _build_memory_query(*, asset_symbol: str, context_markdown: str, recent_runs: list[dict[str, Any]]) -> str:
    return f"{asset_symbol}\n{context_markdown[:2000]}\n{json.dumps(recent_runs[-8:], ensure_ascii=False)[:2000]}"


def _sync_memory_documents(qdrant_client: Any, collection_name: str, documents: list[dict[str, Any]]) -> None:
    if hasattr(qdrant_client, "collection_exists") and not qdrant_client.collection_exists(collection_name):
        if hasattr(qdrant_client, "create_collection"):
            qdrant_client.create_collection(collection_name=collection_name, vectors_config=_load_default_vector_params())
    if hasattr(qdrant_client, "upsert"):
        qdrant_client.upsert(collection_name=collection_name, points=[
            _load_default_point_struct(
                point_id=str(doc["id"]),
                vector=_embed_text(str(doc["text"])),
                payload=doc,
            )
            for doc in documents
        ])


def _search_qdrant(qdrant_client: Any, collection_name: str, query_text: str, *, limit: int) -> list[Any]:
    if hasattr(qdrant_client, "search"):
        return qdrant_client.search(
            collection_name=collection_name,
            query_vector=_embed_text(query_text),
            limit=limit,
        )
    return []


def _embed_text(text: str) -> list[float]:
    vector = [0.0] * _EMBED_DIMENSIONS
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return vector
    for token in tokens:
        index = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16) % _EMBED_DIMENSIONS
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
