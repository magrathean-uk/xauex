"""Local Qdrant-backed memory helpers for the oracle bridge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import re
import uuid
from typing import Any, Callable, Mapping


_DEFAULT_COLLECTION_NAME = "mirofish_oracle_memory"
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


def load_qdrant_memory_config(env: Mapping[str, str] | None = None) -> QdrantMemoryConfig:
    source = env or os.environ
    location = _clean_str(_env_lookup(source, "BRIDGE_QDRANT_LOCATION", "QDRANT_LOCATION"))
    path = _clean_str(_env_lookup(source, "BRIDGE_QDRANT_PATH", "QDRANT_PATH"))
    url = _clean_str(_env_lookup(source, "BRIDGE_QDRANT_URL", "QDRANT_URL"))
    host = _clean_str(_env_lookup(source, "BRIDGE_QDRANT_HOST", "QDRANT_HOST"))
    api_key = _clean_str(_env_lookup(source, "BRIDGE_QDRANT_API_KEY", "QDRANT_API_KEY"))
    collection_name = (
        _clean_str(_env_lookup(source, "BRIDGE_QDRANT_COLLECTION", "QDRANT_COLLECTION"))
        or _DEFAULT_COLLECTION_NAME
    )
    prefix = _clean_str(_env_lookup(source, "BRIDGE_QDRANT_PREFIX", "QDRANT_PREFIX"))
    timeout_seconds = _parse_int(_env_lookup(source, "BRIDGE_QDRANT_TIMEOUT_SECONDS", "QDRANT_TIMEOUT_SECONDS"))
    port = _parse_int(_env_lookup(source, "BRIDGE_QDRANT_PORT", "QDRANT_PORT"), default=_DEFAULT_PORT)
    grpc_port = _parse_int(_env_lookup(source, "BRIDGE_QDRANT_GRPC_PORT", "QDRANT_GRPC_PORT"), default=_DEFAULT_GRPC_PORT)
    prefer_grpc = _parse_bool(_env_lookup(source, "BRIDGE_QDRANT_PREFER_GRPC", "QDRANT_PREFER_GRPC"), default=False)
    https = _parse_https_flag(_env_lookup(source, "BRIDGE_QDRANT_HTTPS", "QDRANT_HTTPS"), url=url)
    force_disable_check_same_thread = _parse_bool(
        _env_lookup(source, "BRIDGE_QDRANT_FORCE_DISABLE_CHECK_SAME_THREAD", "QDRANT_FORCE_DISABLE_CHECK_SAME_THREAD"),
        default=False,
    )
    check_compatibility = _parse_bool(
        _env_lookup(source, "BRIDGE_QDRANT_CHECK_COMPATIBILITY", "QDRANT_CHECK_COMPATIBILITY"),
        default=True,
    )

    explicit_enabled = _parse_optional_bool(_env_lookup(source, "BRIDGE_QDRANT_ENABLED", "QDRANT_ENABLED"))
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
    raise ValueError(f"Invalid boolean value for Qdrant config: {value!r}")


def _parse_int(value: str | None, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    normalized = value.strip()
    if normalized == "":
        return default
    return int(normalized)


def _parse_https_flag(value: str | None, *, url: str | None) -> bool | None:
    parsed = _parse_optional_bool(value)
    if parsed is not None:
        return parsed
    if url is None:
        return None
    lowered = url.lower()
    if lowered.startswith("https://"):
        return True
    if lowered.startswith("http://"):
        return False
    return None


def _env_lookup(source: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return None


def _build_memory_query(
    *,
    asset_symbol: str,
    context_markdown: str,
    recent_runs: list[dict[str, Any]],
) -> str:
    recent_actions = [
        str(item.get("action", "")).strip().upper()
        for item in recent_runs[-4:]
        if str(item.get("action", "")).strip()
    ]
    context_excerpt = " ".join(context_markdown.split())[:700]
    parts = [asset_symbol]
    if recent_actions:
        parts.append("recent_actions=" + ",".join(recent_actions))
    if context_excerpt:
        parts.append(context_excerpt)
    return " | ".join(parts)


def _search_qdrant(client: Any, collection_name: str, query_text: str, *, limit: int) -> list[Any]:
    query_vector = _embed_text(query_text)
    if hasattr(client, "search"):
        try:
            return list(client.search(collection_name=collection_name, query_vector=query_vector, limit=limit, with_payload=True))
        except TypeError:
            return list(client.search(collection_name=collection_name, query_text=query_text, limit=limit, with_payload=True))
    if hasattr(client, "query_points"):
        try:
            result = client.query_points(collection_name=collection_name, query=query_vector, limit=limit, with_payload=True)
        except TypeError:
            result = client.query_points(collection_name=collection_name, query_text=query_text, limit=limit, with_payload=True)
        points = getattr(result, "points", result)
        return list(points)
    raise AttributeError("Qdrant client does not expose a supported search method")


def _normalize_memory_hit(hit: Any) -> dict[str, Any]:
    if isinstance(hit, dict):
        payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else hit
        score = hit.get("score")
        hit_id = hit.get("id")
    else:
        payload = getattr(hit, "payload", {}) or {}
        score = getattr(hit, "score", None)
        hit_id = getattr(hit, "id", None)
    if not isinstance(payload, dict):
        payload = {}

    summary = _clean_str(
        payload.get("summary")
        or payload.get("content")
        or payload.get("text")
        or payload.get("journal")
        or payload.get("note")
    ) or ""
    action = (_clean_str(payload.get("action")) or _clean_str(payload.get("direction")) or "MEMORY").upper()
    source = _clean_str(payload.get("source") or payload.get("source_id") or payload.get("origin")) or ""
    label = _clean_str(payload.get("label") or payload.get("title") or payload.get("name")) or ""
    normalized = {
        "id": str(hit_id) if hit_id is not None else "",
        "score": float(score) if score is not None else 0.0,
        "action": action,
        "summary": summary[:220],
        "source": source[:80],
        "label": label[:80],
    }
    return normalized


def _sync_memory_documents(client: Any, collection_name: str, documents: list[dict[str, Any]]) -> None:
    if not hasattr(client, "upsert"):
        return
    if not hasattr(client, "create_collection") and not hasattr(client, "collection_exists"):
        return
    _ensure_collection(client, collection_name)
    points = [_build_point_struct(document) for document in documents]
    client.upsert(collection_name=collection_name, points=points)


def _ensure_collection(client: Any, collection_name: str) -> None:
    exists = False
    if hasattr(client, "collection_exists"):
        exists = bool(client.collection_exists(collection_name))
    if exists:
        return
    create_collection = getattr(client, "create_collection", None)
    if create_collection is None:
        raise AttributeError("Qdrant client does not expose collection creation")
    VectorParams, Distance = _load_qdrant_model_types()
    create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=_EMBED_DIMENSIONS, distance=Distance.COSINE),
    )


def _build_point_struct(document: dict[str, Any]) -> Any:
    PointStruct, _, _ = _load_qdrant_model_types(include_point_struct=True)
    return PointStruct(
        id=document["id"],
        vector=_embed_text(document["text"]),
        payload={key: value for key, value in document.items() if key != "text"},
    )


def _embed_text(text: str) -> list[float]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return [0.0] * _EMBED_DIMENSIONS
    vector = [0.0] * _EMBED_DIMENSIONS
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for offset in range(0, 8, 2):
            slot = int.from_bytes(digest[offset : offset + 2], "big") % _EMBED_DIMENSIONS
            sign = 1.0 if digest[offset + 16] % 2 == 0 else -1.0
            vector[slot] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        return vector
    return [round(value / norm, 6) for value in vector]


def _memory_point_id(asset_symbol: str, close_time: str, journal: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{asset_symbol}|{close_time}|{journal}"))


def _load_qdrant_model_types(*, include_point_struct: bool = False) -> tuple[Any, Any] | tuple[Any, Any, Any]:
    try:
        from qdrant_client.http.models import Distance, PointStruct, VectorParams
    except ImportError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError(
            "qdrant-client is not installed; add it before enabling the memory layer"
        ) from exc
    if include_point_struct:
        return PointStruct, VectorParams, Distance
    return VectorParams, Distance
