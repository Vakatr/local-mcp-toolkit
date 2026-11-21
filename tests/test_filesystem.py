"""Tests for the filesystem server."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from local_mcp_toolkit import common, filesystem


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_bytes(b"hello\nworld\n")
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    (tmp_path / "sub" / "c.py").write_text("print('ERROR')\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hidden", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01")
    return tmp_path


def test_read_utf8(tree: Path) -> None:
    result = filesystem.read_text_file_impl(str(tree / "a.txt"))
    assert result["ok"] is True
    assert result["content"] == "hello\nworld\n"
    assert result["encoding"] == "utf-8"
    assert result["truncated"] is False
    assert result["size_bytes"] == 12


def test_read_cp1251(tree: Path) -> None:
    path = tree / "cp1251.txt"
    path.write_bytes("Привет, мир!".encode("cp1251"))
    result = filesystem.read_text_file_impl(str(path))
    assert result["ok"] is True
    assert result["content"] == "Привет, мир!"
    assert result["encoding"] == "cp1251"


def test_read_utf16(tree: Path) -> None:
    path = tree / "utf16.txt"
    path.write_text("hello", encoding="utf-16")
    result = filesystem.read_text_file_impl(str(path))
    assert result["ok"] is True
    assert result["content"] == "hello"
    assert result["encoding"] == "utf-16"


def test_read_latin1(tree: Path) -> None:
    path = tree / "latin1.txt"
    path.write_bytes(b"caf\xe9 \x98")
    result = filesystem.read_text_file_impl(str(path))
    assert result["ok"] is True
    assert result["encoding"] == "latin-1"
    assert "\xe9" in result["content"]


def test_read_forced_encoding(tree: Path) -> None:
    path = tree / "forced.txt"
    path.write_bytes("Привет".encode("cp1251"))
    result = filesystem.read_text_file_impl(str(path), encoding="cp1251")
    assert result["content"] == "Привет"
    assert result["encoding"] == "cp1251"


def test_read_unknown_encoding(tree: Path) -> None:
    result = filesystem.read_text_file_impl(str(tree / "a.txt"), encoding="nope")
    assert result["ok"] is False
    assert "unknown encoding" in result["error"]


def test_read_missing_and_directory(tree: Path) -> None:
    missing = filesystem.read_text_file_impl(str(tree / "missing.txt"))
    assert missing["ok"] is False
    assert "not a file" in missing["error"]
    directory = filesystem.read_text_file_impl(str(tree))
    assert directory["ok"] is False


def test_read_max_bytes_truncates(tree: Path) -> None:
    result = filesystem.read_text_file_impl(str(tree / "a.txt"), max_bytes=5)
    assert result["ok"] is True
    assert result["content"] == "hello"
    assert result["truncated"] is True
    # size_bytes still reports the real file size.
    assert result["size_bytes"] == 12


def test_read_max_bytes_is_capped(tree: Path) -> None:
    result = filesystem.read_text_file_impl(
        str(tree / "a.txt"), max_bytes=common.MAX_READ_BYTES * 10
    )
    assert result["ok"] is True
    assert result["truncated"] is False


def test_read_invalid_max_bytes(tree: Path) -> None:
    result = filesystem.read_text_file_impl(str(tree / "a.txt"), max_bytes="x")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "max_bytes" in result["error"]


def test_read_root_allows_inside_and_rejects_outside(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_FS_READ_ROOT", str(tree))
    inside = filesystem.read_text_file_impl(str(tree / "a.txt"))
    assert inside["ok"] is True

    outside = tree.parent / f"{tree.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = filesystem.read_text_file_impl(str(outside))
    assert result["ok"] is False
    assert "outside allowed root" in result["error"]

    # A relative path that escapes the root must be rejected as well.
    traversal = filesystem.read_text_file_impl(f"../{outside.name}")
    assert traversal["ok"] is False


def test_write_and_create_directories(tree: Path) -> None:
    target = tree / "new" / "nested" / "out.txt"
    result = filesystem.write_text_file_impl(str(target), "данные")
    assert result["ok"] is True
    assert result["bytes_written"] == len("данные".encode())
    assert target.read_text(encoding="utf-8") == "данные"


def test_write_respects_root(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = tree / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("MCP_FS_ROOT", str(allowed))

    inside = filesystem.write_text_file_impl(str(allowed / "ok.txt"), "ok")
    assert inside["ok"] is True

    outside = tree.parent / f"{tree.name}-write-outside.txt"
    result = filesystem.write_text_file_impl(str(outside), "nope")
    assert result["ok"] is False
    assert "write outside allowed root" in result["error"]
    assert not outside.exists()


def test_write_rejects_oversized_content(tree: Path) -> None:
    content = "x" * (common.MAX_WRITE_BYTES + 1)
    result = filesystem.write_text_file_impl(str(tree / "big.txt"), content)
    assert result["ok"] is False
    assert "too large" in result["error"]
    assert not (tree / "big.txt").exists()


def test_write_without_parent_creation(tree: Path) -> None:
    target = tree / "missing" / "out.txt"
    result = filesystem.write_text_file_impl(str(target), "x", create_dirs=False)
    assert result["ok"] is False
    assert "parent directory" in result["error"]


def test_write_unknown_encoding(tree: Path) -> None:
    result = filesystem.write_text_file_impl(str(tree / "out.txt"), "x", encoding="not-a-codec")
    assert result["ok"] is False
    assert "unknown encoding" in result["error"]


def test_write_requires_string_content(tree: Path) -> None:
    result = filesystem.write_text_file_impl(
        str(tree / "out.txt"),
        b"bytes",  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert "content must be a string" in result["error"]


def test_list_directory_sorted_and_filtered(tree: Path) -> None:
    result = filesystem.list_directory_impl(str(tree))
    assert result["ok"] is True
    names = [entry["name"] for entry in result["entries"]]
    assert names == ["sub", "a.txt", "b.txt"]
    assert result["entries"][0]["type"] == "dir"
    assert result["truncated"] is False


def test_list_directory_pattern(tree: Path) -> None:
    result = filesystem.list_directory_impl(str(tree), pattern="*.txt")
    assert [entry["name"] for entry in result["entries"]] == ["a.txt", "b.txt"]


def test_list_directory_recursive(tree: Path) -> None:
    result = filesystem.list_directory_impl(str(tree), pattern="*.py", recursive=True)
    paths = [entry["path"] for entry in result["entries"]]
    assert paths == ["sub/c.py"]


def test_list_directory_max_entries(tree: Path) -> None:
    result = filesystem.list_directory_impl(str(tree), max_entries=2)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["truncated"] is True


def test_list_directory_invalid_arguments(tree: Path) -> None:
    assert filesystem.list_directory_impl(str(tree / "a.txt"))["ok"] is False
    invalid = filesystem.list_directory_impl(str(tree), max_entries="x")  # type: ignore[arg-type]
    assert invalid["ok"] is False


def test_list_directory_respects_read_root(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_FS_READ_ROOT", str(tree / "sub"))
    result = filesystem.list_directory_impl(str(tree))
    assert result["ok"] is False


def test_search_hits_and_extension_filter(tree: Path) -> None:
    (tree / "sub" / "error.log").write_text("ERROR in log\n", encoding="utf-8")
    result = filesystem.search_in_files_impl(str(tree), "ERROR")
    assert result["ok"] is True
    assert result["count"] == 2
    assert {hit["file"] for hit in result["hits"]} == {"sub/c.py", "sub/error.log"}

    only_python = filesystem.search_in_files_impl(str(tree), "ERROR", extensions=["py"])
    assert only_python["count"] == 1
    assert only_python["hits"][0]["file"] == "sub/c.py"


def test_search_cp1251_and_line_numbers(tree: Path) -> None:
    path = tree / "sub" / "russian.log"
    path.write_bytes("первая\nОшибка ERROR\n".encode("cp1251"))
    result = filesystem.search_in_files_impl(str(tree / "sub"), "ERROR", extensions=[".log"])
    assert result["ok"] is True
    assert result["hits"][0]["line_no"] == 2
    assert "Ошибка" in result["hits"][0]["line"]


def test_search_skips_cache_directories(tree: Path) -> None:
    (tree / "__pycache__" / "cached.py").write_text("ERROR", encoding="utf-8")
    result = filesystem.search_in_files_impl(str(tree), "ERROR")
    assert all("__pycache__" not in hit["file"] for hit in result["hits"])


def test_search_bad_regex_and_bad_directory(tree: Path) -> None:
    bad = filesystem.search_in_files_impl(str(tree), "[")
    assert bad["ok"] is False
    assert "bad regex" in bad["error"]
    missing = filesystem.search_in_files_impl(str(tree / "nope"), "x")
    assert missing["ok"] is False


def test_search_max_hits(tree: Path) -> None:
    extra = tree / "many"
    extra.mkdir()
    (extra / "one.txt").write_text("ERROR\nERROR\n", encoding="utf-8")
    result = filesystem.search_in_files_impl(str(extra), "ERROR", max_hits=1)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["truncated"] is True


def test_search_respects_read_root(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_FS_READ_ROOT", str(tree / "sub"))
    result = filesystem.search_in_files_impl(str(tree), "hello")
    assert result["ok"] is False


def test_file_info(tree: Path) -> None:
    result = filesystem.file_info_impl(str(tree / "a.txt"))
    assert result["ok"] is True
    assert result["size_bytes"] == 12
    assert result["lines"] == 2
    assert result["encoding"] == "utf-8"
    assert len(result["sha256_short"]) == 12
    assert result["lines_truncated"] is False


def test_file_info_line_counts(tree: Path) -> None:
    (tree / "nonewline.txt").write_text("one", encoding="utf-8")
    (tree / "empty.txt").write_text("", encoding="utf-8")
    assert filesystem.file_info_impl(str(tree / "nonewline.txt"))["lines"] == 1
    assert filesystem.file_info_impl(str(tree / "empty.txt"))["lines"] == 0


def test_file_info_missing_and_directory(tree: Path) -> None:
    assert filesystem.file_info_impl(str(tree / "missing"))["ok"] is False
    assert filesystem.file_info_impl(str(tree))["ok"] is False


def test_mcp_wrappers(tree: Path) -> None:
    target = tree / "wrapper.txt"
    assert filesystem.write_text_file(str(target), "wrapper")["ok"] is True
    assert filesystem.read_text_file(str(target))["content"] == "wrapper"
    assert filesystem.list_directory(str(tree))["ok"] is True
    assert filesystem.search_in_files(str(tree), "wrapper")["count"] == 1
    assert filesystem.file_info(str(target))["ok"] is True


def test_main_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(filesystem, "mcp", types.SimpleNamespace(run=lambda: calls.append(True)))
    filesystem.main()
    assert calls == [True]
