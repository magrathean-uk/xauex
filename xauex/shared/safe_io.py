"""Secure runtime file helpers for XAUEX."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_JSON_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TEXT_MAX_BYTES = 2 * 1024 * 1024


class SafeIOError(RuntimeError):
    """Base class for safe file I/O errors."""


class UnsafePathError(SafeIOError):
    """Raised when a path is unsafe for execution-sensitive I/O."""


class FileTooLargeError(SafeIOError):
    """Raised when a file exceeds the configured maximum size."""


class JsonLoadError(SafeIOError):
    """Raised when JSON cannot be loaded safely."""


def _path(value: str | Path) -> Path:
    return Path(value).expanduser()


def ensure_parent_dir(path: str | Path, *, mode: int = 0o700) -> Path:
    target = _path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, mode)
    except PermissionError:
        # Existing system directories may be managed outside this process.
        pass
    return parent


def refuse_symlink(path: str | Path) -> None:
    target = _path(path)
    try:
        os.lstat(target)
    except FileNotFoundError:
        return
    if os.path.islink(target):
        raise UnsafePathError(f"Refusing to use symlink path: {target}")
    if not os.path.isfile(target) and not os.path.isdir(target):
        raise UnsafePathError(f"Refusing to use non-regular path: {target}")


def _fsync_dir(parent: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(
    path: str | Path,
    payload: bytes,
    *,
    mode: int = 0o600,
    parent_mode: int = 0o700,
    fsync_file: bool = True,
    fsync_parent: bool = True,
) -> None:
    """Atomically write bytes with restrictive permissions."""
    target = _path(path)
    parent = ensure_parent_dir(target, mode=parent_mode)
    refuse_symlink(target)

    fd: int | None = None
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(payload)
            if fsync_file:
                handle.flush()
                os.fsync(handle.fileno())
        refuse_symlink(target)
        os.replace(tmp_name, target)
        os.chmod(target, mode)
        if fsync_parent:
            _fsync_dir(parent)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def atomic_write_text(path: str | Path, text: str, *, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def atomic_write_json(
    path: str | Path,
    payload: Any,
    *,
    mode: int = 0o600,
    indent: int | None = 2,
    sort_keys: bool = False,
) -> None:
    data = json.dumps(payload, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
    if indent is not None:
        data += "\n"
    atomic_write_text(path, data, mode=mode)


def safe_read_bytes(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_TEXT_MAX_BYTES,
    refuse_symlinks: bool = True,
) -> bytes:
    target = _path(path)
    if refuse_symlinks:
        refuse_symlink(target)
    st = os.stat(target)
    if st.st_size > max_bytes:
        raise FileTooLargeError(f"{target} is {st.st_size} bytes, limit is {max_bytes}")

    flags = os.O_RDONLY
    if refuse_symlinks and hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags)
    try:
        data = os.read(fd, max_bytes + 1)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise FileTooLargeError(f"{target} exceeded read limit {max_bytes}")
    return data


def safe_read_text(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_TEXT_MAX_BYTES,
    default: str | None = None,
) -> str:
    try:
        return safe_read_bytes(path, max_bytes=max_bytes).decode("utf-8")
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def safe_load_json(
    path: str | Path,
    default: Any = None,
    *,
    max_bytes: int = DEFAULT_JSON_MAX_BYTES,
    allow_missing: bool = True,
) -> Any:
    try:
        raw = safe_read_bytes(path, max_bytes=max_bytes)
    except FileNotFoundError:
        if allow_missing:
            return default
        raise
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonLoadError(f"Could not parse JSON from {path}: {exc}") from exc


def file_stat_key(path: str | Path) -> tuple[int, int] | None:
    target = _path(path)
    try:
        st = os.stat(target)
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)
