# Contributing

## Development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest --cov=local_mcp_toolkit --cov-branch --cov-fail-under=85
```

Python 3.10+ is supported. CI covers 3.11 and 3.13 on Ubuntu and Windows.

## Layout

```
src/local_mcp_toolkit/common.py         shared env/path/encoding helpers
src/local_mcp_toolkit/filesystem.py     filesystem tools
src/local_mcp_toolkit/python_runner.py  script runner tools
src/local_mcp_toolkit/delegate.py       OpenAI-compatible delegation
tests/                                  pytest suite, no network required
```

## Adding or changing a tool

1. Put the behavior in a function named `<tool>_impl` that returns a plain
   `dict`. Keep it independent of the MCP decorator.
2. Read environment variables at call time, not at import time, so tests and
   long-running processes can change configuration.
3. Add a thin `@mcp.tool()` wrapper with the public signature and docstring.
4. Return errors as `{"ok": False, "error": "..."}`. Never include secrets,
   full API keys or request headers in an error.
5. Add tests for success, validation, limits and the error paths. Use
   `tmp_path` and fake transports; do not require network access or a live MCP
   server.
6. Update the tool table and environment table in `README.md` if the public
   surface changed.

## Style

- `ruff check .` must pass with the configuration in `pyproject.toml`
  (line length 100, Python 3.10 target).
- Prefer the standard library. New runtime dependencies need a justification.
- Keep outputs compact: truncate large text and respect the documented limits.
- Do not log requests, prompts, environment dumps or credentials.

## Tests

```bash
pytest                              # quick run
pytest --cov=local_mcp_toolkit --cov-branch --cov-report=term-missing
```

Tests must pass on Windows and Linux. Avoid POSIX-only paths, signals and
permissions. Use `pathlib` and compare resolved paths when a test depends on
the working directory.

## Pull request checklist

- [ ] `ruff check .` passes
- [ ] `pytest --cov=... --cov-fail-under=85` passes
- [ ] New behavior is covered by tests
- [ ] README/SECURITY updated when the security surface changes
- [ ] No secrets, tokens, personal paths or generated environments committed

## Security

Read [SECURITY.md](SECURITY.md) before changing path handling, subprocess
arguments, environment inheritance or delegate HTTP behavior. Changes that
weaken the roots or expose credentials will not be merged.
