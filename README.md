# local-mcp-toolkit

[![CI](https://github.com/Vakatr/local-mcp-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/Vakatr/local-mcp-toolkit/actions/workflows/ci.yml)

Three local-first [Model Context Protocol](https://modelcontextprotocol.io/) servers in
one Python package:

- **filesystem** — encoding-aware text reads, directory listing, content search,
  metadata and writes.
- **python-runner** — runs local Python scripts and analyzes their output.
- **delegate** — forwards self-contained tasks to an OpenAI-compatible
  `/chat/completions` endpoint.

All servers use the MCP stdio transport: they start as child processes of the MCP
client, do not bind network ports, and need no daemon.

```mermaid
flowchart LR
  Client[MCP client] -->|stdio| FS[filesystem]
  Client -->|stdio| PY[python-runner]
  Client -->|stdio| DEL[delegate]
  DEL -->|HTTP(S)| LLM[OpenAI-compatible endpoint]
```

## Requirements

- Python 3.10 or newer
- An MCP client (Claude Desktop, LM Studio, or any client that supports stdio servers)

## Install

```bash
git clone https://github.com/Vakatr/local-mcp-toolkit.git
cd local-mcp-toolkit
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

This installs three console scripts: `mcp-filesystem`, `mcp-python-runner`,
`mcp-delegate`. Each can also be started as a module, for example
`python -m local_mcp_toolkit.filesystem`.

## Tools

| Server | Tool | Purpose |
|---|---|---|
| filesystem | `read_text_file` | Read text, auto-detect utf-8 / cp1251 / utf-16 / latin-1 |
| filesystem | `list_directory` | List entries, optionally recursive with a glob |
| filesystem | `search_in_files` | Regex search across file contents |
| filesystem | `file_info` | Size, line count, encoding, SHA-256 prefix |
| filesystem | `write_text_file` | Write text, optionally creating parent directories |
| python-runner | `run_python_script` | Run a `.py` file, capture stdout/stderr |
| python-runner | `analyze_output` | Count lines, timestamps and regex hits |
| python-runner | `run_and_analyze` | Run a script and analyze both output streams |
| python-runner | `list_python_files` | Find `.py` files, skipping caches and virtualenvs |
| delegate | `delegate` | Send a task to an OpenAI-compatible chat endpoint |
| delegate | `list_models` | Query `GET /models` on the endpoint |
| delegate | `health_check` | Check endpoint reachability and latency |

### filesystem

Every path is expanded and resolved before use. When a root is configured, the
resolved path must be relative to it; `..` traversal and symlinks that leave the
root are rejected. Read and write roots are independent.

| Variable | Effect |
|---|---|
| `MCP_FS_READ_ROOT` | Restrict reads, listing, search and `file_info` to this tree |
| `MCP_FS_ROOT` | Restrict `write_text_file` to this tree |

If a variable is unset, the corresponding operation is unrestricted.

Hard limits:

| Operation | Limit |
|---|---|
| `read_text_file` content | 10 MiB (`max_bytes` may request less) |
| `write_text_file` encoded content | 2 MiB |
| `list_directory` entries | 5000 |
| `search_in_files` hits | 1000 |
| `search_in_files` per-file scan | 10 MiB |

`read_text_file` checks for a BOM first, then strict UTF-8, then a UTF-16
byte-lane heuristic, then cp1251, and finally latin-1. Encoding detection is a
heuristic: ambiguous single-byte files can be decoded as cp1251 rather than
latin-1. Pass `encoding` to force a specific codec.

`list_directory` and `search_in_files` skip common cache and vendor directories
(`.git`, `.venv`, `venv`, `__pycache__`, `node_modules`, `build`, `dist`,
`.mypy_cache`, `.pytest_cache`, `.ruff_cache`).

`file_info` hashes the complete file but counts lines only in the first 10 MiB;
`lines_truncated` reports when the count is partial. Files larger than 10 MiB
are skipped by `search_in_files`.

### python-runner

| Variable | Default | Effect |
|---|---|---|
| `MCP_RUNNER_ROOT` | unset | Restrict the script, working directory and listings to this tree |
| `MCP_RUNNER_MAX_TIMEOUT` | `300` | Upper bound in seconds for a per-call `timeout` |
| `MCP_RUNNER_INHERIT_SECRETS` | `0` | Set to `1` to pass secret-looking env vars to the child (not recommended) |

`run_python_script` uses `subprocess.run` with an argument list and
`shell=False`. The child inherits the parent environment, but variables whose
names look like secrets (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`,
`*_CREDENTIALS`) are removed by default. `PYTHONIOENCODING=utf-8` is forced and
`PYTHONUNBUFFERED=1` is set when absent. Only the first 50 000 characters of
stdout and 20 000 of stderr are returned; truncated output ends with
`... [truncated]`.

`analyze_output` recognizes `DD.MM.YYYY HH:MM:SS` and ISO-like
`YYYY-MM-DD HH:MM:SS` timestamps. The default patterns are `ERROR`, `WARN`,
`Exception`, `Traceback`, `FAIL` and `[OK]`, matched case-insensitively. Invalid
regexes are reported with a count of `-1` and listed in `invalid_patterns`.

### delegate

The endpoint must implement the OpenAI chat-completions shape. All settings are
optional.

| Variable | Default | Description |
|---|---|---|
| `DELEGATE_BASE_URL` | `http://localhost:1234/v1` | Base URL, without a trailing `/chat/completions` |
| `DELEGATE_API_KEY` | empty | Sent as `Authorization: Bearer ...` when set |
| `DELEGATE_DEFAULT_MODEL` | `sub-agent` | Model used when the call omits one |
| `DELEGATE_TIMEOUT` | `120` | Default HTTP timeout in seconds |
| `DELEGATE_MAX_PROMPT` | `20000` | Maximum `task` length in characters |
| `DELEGATE_MAX_RESPONSE` | `50000` | Maximum returned content length in characters |

Input validation:

- `task` must be a non-empty string no longer than `DELEGATE_MAX_PROMPT`.
- `model` must be a non-empty string of at most 200 characters.
- `max_tokens` is clamped to `1..8192`; `temperature` to `0.0..2.0`.
- `timeout` is clamped to `1..600` seconds and defaults to `DELEGATE_TIMEOUT`.

Responses longer than `DELEGATE_MAX_RESPONSE` are truncated and flagged with
`"truncated": true`. HTTP, URL, timeout and JSON errors are returned as
`{"ok": false, "error": ...}` dictionaries. HTTP redirects are refused so the
`Authorization` header is never forwarded to another host, and proxy
environment variables are ignored. The API key is placed only in the request
header, is redacted if an endpoint reflects it in an error body, and never
appears in results.

## Client configuration

Ready-to-copy files:

- [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)
- [`examples/lm_studio_config.json`](examples/lm_studio_config.json)

Claude Desktop stores its configuration at:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

LM Studio reads MCP servers from its `mcp.json` configuration. The format is the
same `mcpServers` object shown in the example.

Minimal entry:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "mcp-filesystem",
      "env": {
        "MCP_FS_READ_ROOT": "C:/Users/you/Documents",
        "MCP_FS_ROOT": "C:/Users/you/Documents/agent-workspace"
      }
    }
  }
}
```

If the client does not inherit your shell `PATH`, use the absolute path to the
console script (for example
`C:/Users/you/local-mcp-toolkit/.venv/Scripts/mcp-filesystem.exe` on Windows) or
`command: "python"` with `args: ["-m", "local_mcp_toolkit.filesystem"]`.

## Security model

- **python-runner executes arbitrary local code without a sandbox.** It has the
  same file and network access as the user running the MCP server.
  `MCP_RUNNER_ROOT` is a path guard for cooperative use, not a containment
  boundary against malicious scripts. Secret-looking environment variables are
  stripped from the child by default, but a script can still read files or open
  a network connection.
- **filesystem roots are path checks, not an OS sandbox.** They prevent
  accidental reads/writes outside a tree. Any process that can run code as your
  user can bypass them.
- **delegate sends tasks to the configured endpoint.** Use a local endpoint or
  HTTPS. A plain `http://` URL exposes the task and the API key to the network.
  The key is never logged by this package, but the endpoint necessarily
  receives it.
- Only connect these servers to agents you trust. See [SECURITY.md](SECURITY.md)
  for the full threat model.

## Tests

```bash
ruff check .
pytest --cov=local_mcp_toolkit --cov-branch --cov-fail-under=85
```

The suite uses temporary directories and fake HTTP transports; it does not
need network access or a running MCP server. CI runs the same commands on
Ubuntu and Windows with Python 3.11 and 3.13.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
