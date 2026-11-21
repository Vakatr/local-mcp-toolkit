"""Python runner MCP server: execute local scripts and analyze output.

SECURITY: scripts run without any sandbox.  Set ``MCP_RUNNER_ROOT`` to confine
the script path, the working directory and directory listings to one tree.
``MCP_RUNNER_MAX_TIMEOUT`` caps the per-call timeout (default 300 seconds).
Secret-looking environment variables (``*_KEY``, ``*_TOKEN``, ``*_SECRET``,
``*_PASSWORD``, ``*_CREDENTIALS``) are removed from the child environment unless
``MCP_RUNNER_INHERIT_SECRETS=1`` is set explicitly.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import common

mcp = FastMCP("python-runner")

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_TIMEOUT = 300
MAX_LISTED_FILES = 200

DEFAULT_PATTERNS = ("ERROR", "WARN", "Exception", "Traceback", "FAIL", r"\[OK\]")

_SECRET_ENV_RE = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)(?:$|_)",
    re.IGNORECASE,
)

_TIMESTAMP_RE = re.compile(
    r"^\s*(?:"
    r"\d{2}\.\d{2}\.\d{4}[ T]\d{2}:\d{2}:\d{2}"
    r"|"
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
    r")"
)


def _runner_root() -> Path | None:
    return common.root_from_env("MCP_RUNNER_ROOT")


def _max_timeout() -> int:
    return common.env_int(
        "MCP_RUNNER_MAX_TIMEOUT",
        DEFAULT_MAX_TIMEOUT,
        minimum=1,
        maximum=86_400,
    )


def _effective_timeout(timeout: int | None) -> int:
    if timeout is None:
        value = DEFAULT_TIMEOUT
    else:
        try:
            value = int(timeout)
        except (TypeError, ValueError):
            value = DEFAULT_TIMEOUT
    return max(1, min(value, _max_timeout()))


def _child_env() -> dict[str, str]:
    """Build the child environment, stripping secret-looking variables by default."""
    inherit_secrets = common.env_str("MCP_RUNNER_INHERIT_SECRETS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if inherit_secrets or not _SECRET_ENV_RE.search(key)
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_python_script_impl(
    script_path: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
) -> dict:
    """Run a Python script in a subprocess without a shell."""
    script = common.resolve_path(script_path)
    err = common.containment_error(script, _runner_root(), action="run script")
    if err:
        return common.error(err)
    if not script.is_file():
        return common.error(f"script not found: {script}")

    work_dir = common.resolve_path(cwd) if cwd else script.parent
    err = common.containment_error(work_dir, _runner_root(), action="use cwd")
    if err:
        return common.error(err)
    if not work_dir.is_dir():
        return common.error(f"cwd not found: {work_dir}")

    if args is None:
        cmd_args: list[str] = []
    elif isinstance(args, (list, tuple)):
        cmd_args = [str(arg) for arg in args]
    else:
        return common.error("args must be a list of strings")

    effective_timeout = _effective_timeout(timeout)
    env = _child_env()

    start = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - deliberate local execution
            [sys.executable, str(script), *cmd_args],
            cwd=str(work_dir),
            capture_output=True,
            timeout=effective_timeout,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        stdout, _ = common.truncate_text(_as_text(e.stdout), common.STDOUT_LIMIT)
        stderr, _ = common.truncate_text(_as_text(e.stderr), common.STDERR_LIMIT)
        return {
            "ok": False,
            "error": f"timeout after {effective_timeout}s",
            "timed_out": True,
            "returncode": None,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "stdout": stdout,
            "stderr": stderr,
            "script": str(script),
        }
    except OSError as e:
        return {
            "ok": False,
            "error": f"spawn failed: {e}",
            "timed_out": False,
            "returncode": None,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "stdout": "",
            "stderr": "",
            "script": str(script),
        }

    raw_out = completed.stdout or ""
    raw_err = completed.stderr or ""
    stdout, stdout_truncated = common.truncate_text(raw_out, common.STDOUT_LIMIT)
    stderr, stderr_truncated = common.truncate_text(raw_err, common.STDERR_LIMIT)

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "duration_ms": int((time.monotonic() - start) * 1000),
        "stdout": stdout,
        "stderr": stderr,
        "script": str(script),
        "timed_out": False,
        "truncated": stdout_truncated or stderr_truncated,
    }


def analyze_output_impl(
    text: str,
    patterns: list[str] | None = None,
) -> dict:
    """Count lines, timestamped lines and regex matches in *text*."""
    selected = list(DEFAULT_PATTERNS if patterns is None else patterns)
    lines = text.splitlines()
    non_empty = sum(1 for line in lines if line.strip())
    timestamp_lines = sum(1 for line in lines if _TIMESTAMP_RE.match(line))

    pattern_counts: dict[str, int] = {}
    pattern_examples: dict[str, list[str]] = {}
    invalid_patterns: list[str] = []

    for pattern in selected:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            pattern_counts[pattern] = -1
            pattern_examples[pattern] = []
            invalid_patterns.append(pattern)
            continue

        hits: list[str] = []
        count = 0
        for line in lines:
            if rx.search(line):
                count += 1
                if len(hits) < 3:
                    hits.append(line.strip()[:200])
        pattern_counts[pattern] = count
        pattern_examples[pattern] = hits

    return {
        "line_count": len(lines),
        "non_empty": non_empty,
        "timestamp_lines": timestamp_lines,
        "pattern_counts": pattern_counts,
        "pattern_examples": pattern_examples,
        "invalid_patterns": invalid_patterns,
    }


def run_and_analyze_impl(
    script_path: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    patterns: list[str] | None = None,
) -> dict:
    """Run a script and analyze both output streams."""
    run_result = run_python_script_impl(script_path, args=args, cwd=cwd, timeout=timeout)
    return {
        "run": run_result,
        "stdout_analysis": analyze_output_impl(run_result.get("stdout", ""), patterns),
        "stderr_analysis": analyze_output_impl(run_result.get("stderr", ""), patterns),
    }


def list_python_files_impl(directory: str, max_files: int | None = None) -> dict:
    """List ``*.py`` files under *directory*, skipping caches and virtualenvs."""
    root = common.resolve_path(directory)
    err = common.containment_error(root, _runner_root(), action="list files in")
    if err:
        return {**common.error(err), "directory": str(root), "count": 0, "files": []}
    if not root.is_dir():
        return {
            "ok": False,
            "directory": str(root),
            "count": 0,
            "files": [],
            "error": "not a directory",
        }

    try:
        raw_limit = MAX_LISTED_FILES if max_files is None else max_files
        limit = max(1, min(int(raw_limit), common.MAX_LIST_ENTRIES))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "directory": str(root),
            "count": 0,
            "files": [],
            "error": "max_files must be an integer",
        }

    files: list[str] = []
    truncated = False
    try:
        for path in root.rglob("*.py"):
            rel = path.relative_to(root)
            if common.is_skipped(rel.parts):
                continue
            try:
                path.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            files.append(rel.as_posix())
            if len(files) > limit:
                truncated = True
                break
    except OSError as e:
        return {
            "ok": False,
            "directory": str(root),
            "count": 0,
            "files": [],
            "error": f"list failed: {e}",
        }

    if truncated:
        files = files[:limit]
    return {
        "ok": True,
        "directory": str(root),
        "count": len(files),
        "files": sorted(files),
        "truncated": truncated,
    }


@mcp.tool()
async def run_python_script(
    script_path: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
) -> dict:
    """Run a Python script and capture stdout/stderr.

    Args:
        script_path: Path to a .py file.
        args: Command-line arguments.
        cwd: Working directory; defaults to the script's folder.
        timeout: Seconds before the process is killed (capped by
            ``MCP_RUNNER_MAX_TIMEOUT``, 300 by default).
    """
    return await asyncio.to_thread(run_python_script_impl, script_path, args, cwd, timeout)


@mcp.tool()
def analyze_output(text: str, patterns: list[str] | None = None) -> dict:
    """Analyze text: line counts, timestamps and regex pattern hits.

    Args:
        text: Text to inspect.
        patterns: Regex patterns; common error markers are used by default.
    """
    return analyze_output_impl(text, patterns)


@mcp.tool()
async def run_and_analyze(
    script_path: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    patterns: list[str] | None = None,
) -> dict:
    """Run a script and return both the result and output analyses."""
    return await asyncio.to_thread(run_and_analyze_impl, script_path, args, cwd, timeout, patterns)


@mcp.tool()
def list_python_files(directory: str, max_files: int | None = None) -> dict:
    """Recursively list .py files, skipping virtualenvs and caches."""
    return list_python_files_impl(directory, max_files)


def main() -> None:
    """Run the Python runner MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
