"""Tests for shared helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_mcp_toolkit import common


def test_detect_encoding_utf8() -> None:
    assert common.detect_encoding("привет".encode()) == "utf-8"


def test_detect_encoding_utf8_bom() -> None:
    assert common.detect_encoding(b"\xef\xbb\xbfhello") == "utf-8-sig"


def test_detect_encoding_cp1251() -> None:
    assert common.detect_encoding("Привет, мир!".encode("cp1251")) == "cp1251"


def test_detect_encoding_utf16_bom() -> None:
    assert common.detect_encoding("hello".encode("utf-16")) == "utf-16"


def test_detect_encoding_utf16_le_without_bom() -> None:
    assert common.detect_encoding("hello world".encode("utf-16-le")) == "utf-16-le"


def test_detect_encoding_utf16_be_without_bom() -> None:
    assert common.detect_encoding("hello world".encode("utf-16-be")) == "utf-16-be"


def test_detect_encoding_latin1_fallback() -> None:
    # 0x98 is undefined in cp1251, so only latin-1 can decode this.
    assert common.detect_encoding(b"caf\xe9 \x98") == "latin-1"


def test_detect_encoding_empty_is_utf8() -> None:
    assert common.detect_encoding(b"") == "utf-8"


def test_decode_text_explicit_and_unknown() -> None:
    text, used = common.decode_text("Привет".encode("cp1251"), "cp1251")
    assert text == "Привет"
    assert used == "cp1251"
    with pytest.raises(LookupError):
        common.decode_text(b"abc", "definitely-not-a-codec")


def test_env_str_trims_and_defaults() -> None:
    env = {"A": "  value  ", "B": "   ", "C": ""}
    assert common.env_str("A", env=env) == "value"
    assert common.env_str("B", "fallback", env=env) == "fallback"
    assert common.env_str("C", "fallback", env=env) == "fallback"
    assert common.env_str("MISSING", "fallback", env=env) == "fallback"


def test_env_int_valid_invalid_and_clamped() -> None:
    env = {"OK": "42", "BAD": "many", "LOW": "-5", "HIGH": "999"}
    assert common.env_int("OK", 1, env=env) == 42
    assert common.env_int("BAD", 7, env=env) == 7
    assert common.env_int("MISSING", 7, env=env) == 7
    assert common.env_int("LOW", 7, minimum=1, env=env) == 1
    assert common.env_int("HIGH", 7, maximum=100, env=env) == 100


def test_env_helpers_read_os_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TEST_VALUE", " 123 ")
    assert common.env_int("MCP_TEST_VALUE", 0) == 123
    assert common.env_str("MCP_TEST_VALUE") == "123"


def test_root_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCP_FS_ROOT", str(tmp_path))
    assert common.root_from_env("MCP_FS_ROOT") == tmp_path.resolve()
    monkeypatch.delenv("MCP_FS_ROOT")
    assert common.root_from_env("MCP_FS_ROOT") is None


def test_resolve_path_expands_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target").mkdir()
    assert common.resolve_path("target/../target") == (tmp_path / "target").resolve()


def test_containment_error() -> None:
    root = Path("/safe/root")
    assert common.containment_error(root / "child" / "file.txt", root) is None
    assert common.containment_error(root, None) is None
    error = common.containment_error(Path("/safe/root-evil/file.txt"), root)
    assert error is not None
    assert "outside allowed root" in error
    assert "write" in common.containment_error(Path("/elsewhere"), root, action="write")


def test_truncate_text() -> None:
    assert common.truncate_text("abc", 10) == ("abc", False)
    assert common.truncate_text("abc", 3) == ("abc", False)
    text, truncated = common.truncate_text("abcdef", 3)
    assert text.startswith("abc")
    assert text.endswith(common.TRUNCATION_MARKER)
    assert truncated is True
    assert common.truncate_text("abc", 0) == ("", True)
    assert common.truncate_text("", 0) == ("", False)


def test_normalize_extensions() -> None:
    assert common.normalize_extensions(["py", ".PY", " .Log ", ""]) == {".py", ".log"}
    assert common.normalize_extensions(None) == set()


def test_is_skipped() -> None:
    assert common.is_skipped(("src", "__pycache__", "x.py"))
    assert common.is_skipped((".git",))
    assert not common.is_skipped(("src", "main.py"))


def test_error_shape() -> None:
    assert common.error("boom") == {"ok": False, "error": "boom"}
