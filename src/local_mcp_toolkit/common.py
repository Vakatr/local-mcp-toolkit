"""Shared helpers for the local MCP servers.

The helpers here are deliberately small and dependency-free so they can be
tested without importing the MCP runtime.  Environment variables are read at
call time, not at import time, which makes the servers configurable per
request in tests and predictable when the process environment changes.
"""

from __future__ import annotations

import codecs
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

MIB = 1024 * 1024

# Hard limits.  Tool arguments may request smaller values but never larger ones.
MAX_READ_BYTES = 10 * MIB
MAX_WRITE_BYTES = 2 * MIB
MAX_LIST_ENTRIES = 5000
MAX_SEARCH_HITS = 1000
MAX_SEARCH_FILE_BYTES = 10 * MIB
STDOUT_LIMIT = 50_000
STDERR_LIMIT = 20_000
TRUNCATION_MARKER = "\n... [truncated]"

SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
)


def error(message: str) -> dict:
    """Build the conventional error result used by every tool."""
    return {"ok": False, "error": message}


def env_str(name: str, default: str = "", env: Mapping[str, str] | None = None) -> str:
    """Read a trimmed string from the environment, treating blanks as unset."""
    source = os.environ if env is None else env
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Read an integer, falling back to *default* for missing or invalid values."""
    source = os.environ if env is None else env
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def resolve_path(raw: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and resolve symlinks/relative components."""
    return Path(raw).expanduser().resolve()


def root_from_env(name: str, env: Mapping[str, str] | None = None) -> Path | None:
    """Return a resolved root directory from *name*, or ``None`` when unset."""
    raw = env_str(name, env=env)
    if not raw:
        return None
    return resolve_path(raw)


def containment_error(path: Path, root: Path | None, *, action: str = "access") -> str | None:
    """Return an error when *path* escapes *root*; ``None`` when allowed.

    Both arguments are expected to be resolved already.  Using
    ``Path.relative_to`` (instead of a string prefix check) prevents the
    classic ``/allowed-evil`` bypass.
    """
    if root is None:
        return None
    try:
        path.relative_to(root)
    except ValueError:
        return f"{action} outside allowed root {root}: {path}"
    return None


def detect_encoding(raw: bytes) -> str:
    """Guess a text encoding using BOMs, strict UTF-8 and a UTF-16 heuristic."""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"

    # A NUL byte is valid UTF-8 but almost always means UTF-16 text here, and
    # accepting it as UTF-8 would hide the real encoding.
    if b"\x00" not in raw:
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass

    utf16_variant = _utf16_variant(raw)
    if utf16_variant:
        return utf16_variant

    try:
        raw.decode("cp1251")
        return "cp1251"
    except UnicodeDecodeError:
        pass

    # latin-1 decodes every byte sequence, so it is the final fallback.
    return "latin-1"


def _utf16_variant(raw: bytes) -> str | None:
    """Heuristic for BOM-less UTF-16 text: many NULs on one byte lane."""
    if len(raw) < 4 or len(raw) % 2:
        return None
    even_nuls = sum(1 for i in range(0, len(raw), 2) if raw[i] == 0)
    odd_nuls = sum(1 for i in range(1, len(raw), 2) if raw[i] == 0)
    half = len(raw) // 2
    if max(even_nuls, odd_nuls) < half * 0.3:
        return None
    # UTF-16-LE stores the ASCII byte first, so its NULs sit on odd offsets.
    return "utf-16-le" if odd_nuls >= even_nuls else "utf-16-be"


def decode_text(raw: bytes, encoding: str | None = None) -> tuple[str, str]:
    """Decode bytes, returning ``(text, encoding_used)``.

    Raises ``LookupError`` for unknown explicit encodings.
    """
    chosen = encoding or detect_encoding(raw)
    if chosen is None:  # pragma: no cover - defensive, detect_encoding never returns None
        chosen = "utf-8"
    return raw.decode(chosen, errors="replace"), chosen


def truncate_text(text: str, limit: int, marker: str = TRUNCATION_MARKER) -> tuple[str, bool]:
    """Trim *text* to *limit* characters and report whether it was trimmed."""
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    return text[:limit] + marker, True


def normalize_extensions(extensions: Sequence[str] | None) -> set[str]:
    """Normalize ``["py", ".PY"]`` to ``{".py"}``."""
    result: set[str] = set()
    for ext in extensions or ():
        value = str(ext).strip().lower()
        if not value:
            continue
        result.add(value if value.startswith(".") else f".{value}")
    return result


def is_skipped(relative_parts: Sequence[str]) -> bool:
    """True when any path component is a cache/vendor directory."""
    return any(part in SKIP_DIRS for part in relative_parts)
