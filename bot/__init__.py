"""Compatibility package exposing ``xauex.bot`` as top-level ``bot``."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[1] / "xauex" / "bot")]
