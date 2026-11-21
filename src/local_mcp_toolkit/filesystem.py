"""Filesystem MCP server with encoding detection and configurable roots.

Reads can be confined with ``MCP_FS_READ_ROOT``; writes with ``MCP_FS_ROOT``.
When a root is unset the corresponding operation is unrestricted, which is
convenient for local use but should be configured explicitly for shared
machines.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import common

mcp = FastMCP("filesystem")


def _read_root() -> Path | None:
    return common.root_from_env("MCP_FS_READ_ROOT")


def _write_root() -> Path | None:
    return common.root_from_env("MCP_FS_ROOT")


def _bounded(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def read_text_file_impl(
    path: str,
    encoding: str | None = None,
    max_bytes: int | None = None,
) -> dict:
    """Read a text file, detecting its encoding when not specified."""
    p = common.resolve_path(path)
    err = common.containment_error(p, _read_root(), action="read")
    if err:
        return common.error(err)
    if not p.is_file():
        return common.error(f"not a file: {p}")

    try:
        limit = _bounded(max_bytes, common.MAX_READ_BYTES, common.MAX_READ_BYTES)
    except (TypeError, ValueError):
        return common.error("max_bytes must be an integer")

    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            raw = fh.read(limit + 1)
    except OSError as e:
        return common.error(f"read failed: {e}")

    truncated = size > limit or len(raw) > limit
    raw = raw[:limit]

    try:
        content, used_encoding = common.decode_text(raw, encoding)
    except LookupError:
        return common.error(f"unknown encoding: {encoding}")
    except (TypeError, ValueError) as e:
        return common.error(f"decode failed: {e}")

    return {
        "ok": True,
        "path": str(p),
        "size_bytes": size,
        "encoding": used_encoding,
        "content": content,
        "truncated": truncated,
    }


def list_directory_impl(
    path: str,
    pattern: str | None = None,
    recursive: bool = False,
    max_entries: int | None = None,
) -> dict:
    """List files and directories, optionally filtered by a glob pattern."""
    p = common.resolve_path(path)
    err = common.containment_error(p, _read_root(), action="list")
    if err:
        return common.error(err)
    if not p.is_dir():
        return common.error(f"not a directory: {p}")

    try:
        limit = _bounded(max_entries, common.MAX_LIST_ENTRIES, common.MAX_LIST_ENTRIES)
    except (TypeError, ValueError):
        return common.error("max_entries must be an integer")

    entries: list[dict] = []
    truncated = False
    try:
        iterator = p.rglob(pattern or "*") if recursive else p.glob(pattern or "*")

        for item in iterator:
            try:
                rel = item.relative_to(p)
            except ValueError:
                continue
            if common.is_skipped(rel.parts):
                continue
            # Symlinks are resolved; never follow one that leaves the directory.
            try:
                item.resolve().relative_to(p)
            except (OSError, ValueError):
                continue

            try:
                stat = item.stat()
                entries.append(
                    {
                        "name": item.name,
                        "path": rel.as_posix(),
                        "type": "dir" if item.is_dir() else "file",
                        "size": stat.st_size if item.is_file() else 0,
                    }
                )
            except OSError:
                continue
            if len(entries) > limit:
                truncated = True
                break
    except (OSError, ValueError, NotImplementedError) as e:
        return common.error(f"list failed: {e}")

    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    if truncated:
        entries = entries[:limit]
    return {
        "ok": True,
        "path": str(p),
        "count": len(entries),
        "entries": entries,
        "truncated": truncated,
    }


def search_in_files_impl(
    directory: str,
    regex: str,
    extensions: list[str] | None = None,
    max_hits: int | None = None,
) -> dict:
    """Search file contents with a Python regular expression."""
    root = common.resolve_path(directory)
    err = common.containment_error(root, _read_root(), action="search")
    if err:
        return common.error(err)
    if not root.is_dir():
        return common.error(f"not a directory: {root}")

    try:
        rx = re.compile(regex)
    except re.error as e:
        return common.error(f"bad regex: {e}")

    try:
        limit = _bounded(max_hits, common.MAX_SEARCH_HITS, common.MAX_SEARCH_HITS)
    except (TypeError, ValueError):
        return common.error("max_hits must be an integer")

    exts = common.normalize_extensions(extensions)
    hits: list[dict] = []

    try:
        files = root.rglob("*")
        for file in files:
            if not file.is_file():
                continue
            rel = file.relative_to(root)
            if common.is_skipped(rel.parts):
                continue
            if exts and file.suffix.lower() not in exts:
                continue
            try:
                file.resolve().relative_to(root)
                if file.stat().st_size > common.MAX_SEARCH_FILE_BYTES:
                    continue
                raw = file.read_bytes()
            except (OSError, ValueError):
                continue

            try:
                text, _ = common.decode_text(raw)
            except (LookupError, UnicodeDecodeError):
                continue

            for line_no, line in enumerate(text.splitlines(), 1):
                if not rx.search(line):
                    continue
                hits.append(
                    {
                        "file": rel.as_posix(),
                        "line_no": line_no,
                        "line": line.strip()[:300],
                    }
                )
                if len(hits) > limit:
                    break
            if len(hits) > limit:
                break
    except OSError as e:
        return common.error(f"search failed: {e}")

    truncated = len(hits) > limit
    if truncated:
        hits = hits[:limit]
    return {
        "ok": True,
        "pattern": regex,
        "count": len(hits),
        "hits": hits,
        "truncated": truncated,
    }


def file_info_impl(path: str) -> dict:
    """Return size, line count, encoding and a short SHA-256 digest."""
    p = common.resolve_path(path)
    err = common.containment_error(p, _read_root(), action="inspect")
    if err:
        return common.error(err)
    if not p.is_file():
        return common.error(f"not a file: {p}")

    try:
        size = p.stat().st_size
        hasher = hashlib.sha256()
        with p.open("rb") as fh:
            head = fh.read(common.MAX_READ_BYTES)
            hasher.update(head)
            while chunk := fh.read(common.MIB):
                hasher.update(chunk)
    except OSError as e:
        return common.error(f"read failed: {e}")

    encoding = common.detect_encoding(head)
    text = head.decode(encoding, errors="replace")
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)

    return {
        "ok": True,
        "path": str(p),
        "size_bytes": size,
        "lines": lines,
        "lines_truncated": size > len(head),
        "encoding": encoding,
        "sha256_short": hasher.hexdigest()[:12],
    }


def write_text_file_impl(
    path: str,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> dict:
    """Write text to a file, enforcing the optional write root and size cap."""
    p = common.resolve_path(path)
    err = common.containment_error(p, _write_root(), action="write")
    if err:
        return common.error(err)
    if not isinstance(content, str):
        return common.error("content must be a string")

    try:
        data = content.encode(encoding)
    except LookupError:
        return common.error(f"unknown encoding: {encoding}")
    except (UnicodeEncodeError, TypeError) as e:
        return common.error(f"encode failed: {e}")

    if len(data) > common.MAX_WRITE_BYTES:
        return common.error(
            f"content too large: {len(data)} bytes > {common.MAX_WRITE_BYTES} bytes"
        )

    try:
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        elif not p.parent.is_dir():
            return common.error(f"parent directory does not exist: {p.parent}")
        p.write_bytes(data)
    except OSError as e:
        return common.error(f"write failed: {e}")

    return {
        "ok": True,
        "path": str(p),
        "bytes_written": len(data),
        "encoding": encoding,
    }


@mcp.tool()
def read_text_file(
    path: str,
    encoding: str | None = None,
    max_bytes: int | None = None,
) -> dict:
    """Read a text file with automatic utf-8/cp1251/utf-16/latin-1 detection.

    Args:
        path: File path, absolute or relative.
        encoding: Force an encoding instead of auto-detecting.
        max_bytes: Maximum bytes to return (capped at 10 MiB).
    """
    return read_text_file_impl(path, encoding=encoding, max_bytes=max_bytes)


@mcp.tool()
def list_directory(
    path: str,
    pattern: str | None = None,
    recursive: bool = False,
    max_entries: int | None = None,
) -> dict:
    """List directory entries, optionally filtered by a glob pattern.

    Args:
        path: Directory to list.
        pattern: Glob such as ``*.py`` or ``**/*.log``.
        recursive: Walk subdirectories.
        max_entries: Cap on returned entries (capped at 5000).
    """
    return list_directory_impl(path, pattern=pattern, recursive=recursive, max_entries=max_entries)


@mcp.tool()
def search_in_files(
    directory: str,
    regex: str,
    extensions: list[str] | None = None,
    max_hits: int | None = None,
) -> dict:
    """Search file contents with a regular expression.

    Args:
        directory: Root folder to search.
        regex: Python regular expression.
        extensions: Only inspect these extensions, e.g. ``[".py", ".log"]``.
        max_hits: Stop after this many matches (capped at 1000).
    """
    return search_in_files_impl(directory, regex, extensions=extensions, max_hits=max_hits)


@mcp.tool()
def file_info(path: str) -> dict:
    """Return metadata for a file: size, lines, encoding and SHA-256 prefix."""
    return file_info_impl(path)


@mcp.tool()
def write_text_file(
    path: str,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> dict:
    """Write a text file (maximum 2 MiB of encoded content).

    Args:
        path: Target path.
        content: Text to write.
        encoding: Output encoding, utf-8 by default.
        create_dirs: Create missing parent directories.
    """
    return write_text_file_impl(path, content, encoding=encoding, create_dirs=create_dirs)


def main() -> None:
    """Run the filesystem MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
