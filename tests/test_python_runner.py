"""Tests for the Python runner server."""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from local_mcp_toolkit import common, python_runner


def _script(tmp_path: Path, body: str, name: str = "script.py") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_run_success_with_args(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        "import sys\nprint('hello')\nprint(sys.argv[1:])\n",
    )
    result = python_runner.run_python_script_impl(str(script), args=["--flag", "value"])
    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["timed_out"] is False
    assert result["stdout"].splitlines() == ["hello", "['--flag', 'value']"]
    assert result["stderr"] == ""
    assert result["script"] == str(script.resolve())
    assert isinstance(result["duration_ms"], int)


def test_run_defaults_to_script_directory(tmp_path: Path) -> None:
    script = _script(tmp_path, "import os\nprint(os.getcwd())\n")
    result = python_runner.run_python_script_impl(str(script))
    assert result["ok"] is True
    assert Path(result["stdout"].strip()).resolve() == tmp_path.resolve()


def test_run_custom_cwd(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    script = _script(tmp_path, "import os\nprint(os.getcwd())\n")
    result = python_runner.run_python_script_impl(str(script), cwd=str(work))
    assert result["ok"] is True
    assert Path(result["stdout"].strip()).resolve() == work.resolve()


def test_run_nonzero_exit(tmp_path: Path) -> None:
    script = _script(tmp_path, "raise SystemExit(3)\n")
    result = python_runner.run_python_script_impl(str(script))
    assert result["ok"] is False
    assert result["returncode"] == 3
    assert result["timed_out"] is False


def test_run_missing_script(tmp_path: Path) -> None:
    result = python_runner.run_python_script_impl(str(tmp_path / "missing.py"))
    assert result["ok"] is False
    assert "script not found" in result["error"]


def test_run_missing_cwd(tmp_path: Path) -> None:
    script = _script(tmp_path, "print('x')\n")
    result = python_runner.run_python_script_impl(str(script), cwd=str(tmp_path / "missing"))
    assert result["ok"] is False
    assert "cwd not found" in result["error"]


def test_run_invalid_args(tmp_path: Path) -> None:
    script = _script(tmp_path, "print('x')\n")
    result = python_runner.run_python_script_impl(
        str(script),
        args="--flag",  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert "args must be a list" in result["error"]


def test_secret_environment_is_not_inherited(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DELEGATE_API_KEY", "top-secret")
    script = _script(
        tmp_path,
        "import os\nprint(os.environ.get('DELEGATE_API_KEY', '<missing>'))\n",
    )
    result = python_runner.run_python_script_impl(str(script))
    assert result["ok"] is True
    assert result["stdout"].strip() == "<missing>"


def test_secret_environment_can_be_inherited_explicitly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DELEGATE_API_KEY", "top-secret")
    monkeypatch.setenv("MCP_RUNNER_INHERIT_SECRETS", "1")
    script = _script(
        tmp_path,
        "import os\nprint(os.environ.get('DELEGATE_API_KEY', '<missing>'))\n",
    )
    result = python_runner.run_python_script_impl(str(script))
    assert result["ok"] is True
    assert result["stdout"].strip() == "top-secret"


def test_run_root_rejects_script_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = _script(tmp_path, "print('x')\n", name="outside.py")
    monkeypatch.setenv("MCP_RUNNER_ROOT", str(allowed))
    result = python_runner.run_python_script_impl(str(outside))
    assert result["ok"] is False
    assert "run script outside allowed root" in result["error"]


def test_run_root_rejects_cwd_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    script = _script(allowed, "print('x')\n")
    monkeypatch.setenv("MCP_RUNNER_ROOT", str(allowed))
    result = python_runner.run_python_script_impl(str(script), cwd=str(tmp_path))
    assert result["ok"] is False
    assert "use cwd outside allowed root" in result["error"]


def test_run_timeout(tmp_path: Path) -> None:
    script = _script(tmp_path, "import time\ntime.sleep(2)\n")
    result = python_runner.run_python_script_impl(str(script), timeout=1)
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert result["error"] == "timeout after 1s"


def test_effective_timeout_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    assert python_runner._effective_timeout(None) == python_runner.DEFAULT_TIMEOUT
    assert python_runner._effective_timeout(0) == 1
    assert python_runner._effective_timeout("nope") == python_runner.DEFAULT_TIMEOUT  # type: ignore[arg-type]
    monkeypatch.setenv("MCP_RUNNER_MAX_TIMEOUT", "7")
    assert python_runner._effective_timeout(100) == 7
    assert python_runner._max_timeout() == 7


def test_run_truncates_output(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        "import sys\nprint('a' * 60000)\nprint('b' * 30000, file=sys.stderr)\n",
    )
    result = python_runner.run_python_script_impl(str(script))
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["stdout"]) == common.STDOUT_LIMIT + len(common.TRUNCATION_MARKER)
    assert result["stdout"].startswith("a")
    assert result["stdout"].endswith(common.TRUNCATION_MARKER)
    assert result["stderr"].endswith(common.TRUNCATION_MARKER)


def test_run_spawn_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script(tmp_path, "print('x')\n")

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("spawn denied")

    monkeypatch.setattr(python_runner.subprocess, "run", broken)
    result = python_runner.run_python_script_impl(str(script))
    assert result["ok"] is False
    assert "spawn failed" in result["error"]
    assert result["returncode"] is None


def test_analyze_output_defaults() -> None:
    text = (
        "2024-01-02 03:04:05 ERROR boom\n"
        "01.02.2024 03:04:05 warn low\n"
        "INFO ok\n"
        "Traceback (most recent call last)\n"
        "[OK] done\n"
    )
    result = python_runner.analyze_output_impl(text)
    assert result["line_count"] == 5
    assert result["non_empty"] == 5
    assert result["timestamp_lines"] == 2
    assert result["pattern_counts"]["ERROR"] == 1
    assert result["pattern_counts"]["WARN"] == 1
    assert result["pattern_counts"]["Traceback"] == 1
    assert result["pattern_counts"][r"\[OK\]"] == 1
    assert result["pattern_examples"]["ERROR"] == ["2024-01-02 03:04:05 ERROR boom"]
    assert result["invalid_patterns"] == []


def test_analyze_output_custom_and_invalid_patterns() -> None:
    result = python_runner.analyze_output_impl(
        "Error one\nERROR two\nfine\n", patterns=["error", "["]
    )
    assert result["pattern_counts"]["error"] == 2
    assert result["pattern_examples"]["error"] == ["Error one", "ERROR two"]
    assert result["pattern_counts"]["["] == -1
    assert result["invalid_patterns"] == ["["]


def test_analyze_output_empty() -> None:
    result = python_runner.analyze_output_impl("", patterns=[])
    assert result["line_count"] == 0
    assert result["non_empty"] == 0
    assert result["timestamp_lines"] == 0
    assert result["pattern_counts"] == {}


def test_run_and_analyze(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        "import sys\nprint('ERROR visible')\nprint('WARN hidden', file=sys.stderr)\n",
    )
    result = python_runner.run_and_analyze_impl(str(script))
    assert result["run"]["ok"] is True
    assert result["stdout_analysis"]["pattern_counts"]["ERROR"] == 1
    assert result["stderr_analysis"]["pattern_counts"]["WARN"] == 1


def test_list_python_files(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("", encoding="utf-8")

    result = python_runner.list_python_files_impl(str(tmp_path))
    assert result["ok"] is True
    assert result["files"] == ["a.py", "pkg/b.py"]
    assert result["truncated"] is False

    limited = python_runner.list_python_files_impl(str(tmp_path), max_files=1)
    assert limited["count"] == 1
    assert limited["truncated"] is True

    zero = python_runner.list_python_files_impl(str(tmp_path), max_files=0)
    assert zero["count"] == 1
    assert zero["truncated"] is True


def test_list_python_files_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_path = tmp_path / "a.py"
    file_path.write_text("", encoding="utf-8")
    assert python_runner.list_python_files_impl(str(file_path))["ok"] is False
    assert (
        python_runner.list_python_files_impl(str(tmp_path), max_files="x")["ok"]  # type: ignore[arg-type]
        is False
    )

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("MCP_RUNNER_ROOT", str(allowed))
    restricted = python_runner.list_python_files_impl(str(tmp_path))
    assert restricted["ok"] is False
    assert "list files in outside allowed root" in restricted["error"]


def test_async_mcp_wrappers(tmp_path: Path) -> None:
    script = _script(tmp_path, "print('wrapper')\n")
    run_result = asyncio.run(python_runner.run_python_script(str(script)))
    assert run_result["ok"] is True
    assert "wrapper" in run_result["stdout"]

    combined = asyncio.run(python_runner.run_and_analyze(str(script)))
    assert combined["run"]["ok"] is True


def test_analyze_wrapper() -> None:
    result = python_runner.analyze_output("ERROR x")
    assert result["pattern_counts"]["ERROR"] == 1
    listed = python_runner.list_python_files(str(Path(__file__).parent))
    assert listed["ok"] is True


def test_main_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(python_runner, "mcp", types.SimpleNamespace(run=lambda: calls.append(True)))
    python_runner.main()
    assert calls == [True]
