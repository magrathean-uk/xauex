"""Cached runtime-file store for the XAUEX dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xauex.shared.safe_io import JsonLoadError, file_stat_key, safe_load_json, safe_read_text


@dataclass(frozen=True)
class CacheStats:
    hits: int
    misses: int


@dataclass
class _CacheEntry:
    key: tuple[int, int] | None
    value: Any


class RuntimeFileStore:
    """mtime/size keyed cache for dashboard runtime files."""

    def __init__(self):
        self._json_cache: dict[Path, _CacheEntry] = {}
        self._text_cache: dict[Path, _CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> CacheStats:
        return CacheStats(hits=self._hits, misses=self._misses)

    def clear(self) -> None:
        self._json_cache.clear()
        self._text_cache.clear()
        self._hits = 0
        self._misses = 0

    def exists(self, path: str | Path) -> bool:
        return Path(path).exists()

    def load_json(self, path: str | Path, default: Any, *, max_bytes: int = 5 * 1024 * 1024) -> Any:
        target = Path(path)
        key = file_stat_key(target)
        if key is None:
            return default
        cached = self._json_cache.get(target)
        if cached and cached.key == key:
            self._hits += 1
            return cached.value
        try:
            value = safe_load_json(target, default=default, max_bytes=max_bytes)
        except JsonLoadError:
            value = default
        self._json_cache[target] = _CacheEntry(key=key, value=value)
        self._misses += 1
        return value

    def load_text(self, path: str | Path, default: str = "", *, max_bytes: int = 2 * 1024 * 1024) -> str:
        target = Path(path)
        key = file_stat_key(target)
        if key is None:
            return default
        cached = self._text_cache.get(target)
        if cached and cached.key == key:
            self._hits += 1
            return str(cached.value)
        try:
            value = safe_read_text(target, max_bytes=max_bytes, default=default)
        except Exception:
            value = default
        self._text_cache[target] = _CacheEntry(key=key, value=value)
        self._misses += 1
        return value

    def invalidate(self, path: str | Path) -> None:
        target = Path(path)
        self._json_cache.pop(target, None)
        self._text_cache.pop(target, None)
