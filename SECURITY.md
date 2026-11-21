# Security Policy

## Reporting a vulnerability

Do not open a public issue containing exploit details. Use the repository's
**Security → Report a vulnerability** flow (GitHub private security advisory) or
contact the maintainer privately. Include the affected tool, a minimal
reproduction, and the version or commit. You should receive an initial response
within a few days.

## Threat model

`local-mcp-toolkit` is designed for a local desktop setup where the MCP client,
the servers and any scripts they run belong to the same trusted user. It is not
a multi-tenant service and it has no authentication between the client and the
server; the transport is stdio and the client starts the server process.

### Arbitrary code execution (python-runner)

`run_python_script` and `run_and_analyze` execute a local Python file with
`sys.executable`. There is **no sandbox, container, seccomp policy, filesystem
jail or network isolation**. The child process:

- runs with the privileges of the user that started the MCP server;
- can read and write any file that user can access;
- can open network connections;
- inherits the server's environment, except variables whose names look like
  secrets (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_CREDENTIALS`).
  `MCP_RUNNER_INHERIT_SECRETS=1` disables that filter and is not recommended.

`MCP_RUNNER_ROOT`, when set, only checks that the script path, the requested
`cwd` and directory listings resolve inside the configured tree. A malicious
script can ignore those paths entirely once it is running. Treat this server as
equivalent to giving the agent a shell. Use it only with trusted agents and
trusted scripts, preferably in a dedicated user account, VM or container if
strong isolation is required.

### Filesystem path roots (filesystem)

`MCP_FS_READ_ROOT` and `MCP_FS_ROOT` restrict reads and writes respectively.
Paths are expanded and resolved with `Path.resolve()`, and containment is
checked with `Path.relative_to()`, so `..` traversal and symlinks that point
outside the root are rejected. The check happens before the operation.

Limitations:

- An unset root means unrestricted access. This is the default.
- The roots are application-level checks, not an OS sandbox. A malicious local
  process, a race with a symlink, or a script run through python-runner can
  bypass them.
- Reads and writes have independent roots. Setting only `MCP_FS_READ_ROOT` does
  not restrict writes.
- File metadata (size, line count, encoding, SHA-256 prefix) is exposed for
  allowed paths.

### API keys and the delegate endpoint

`DELEGATE_API_KEY` is read from the environment and sent only as
`Authorization: Bearer <key>` to the configured `DELEGATE_BASE_URL`. The package
does not log requests, headers, prompts or keys. Error results contain only the
HTTP status/body excerpt, URL errors or shape errors. If an endpoint reflects
the key in an error body, it is replaced with `***` before the result is
returned.

Operational risks:

- A plain `http://` base URL sends the task and the API key in cleartext. Use
  `https://` or a loopback endpoint.
- The endpoint operator can see every task and response. Do not delegate
  secrets, credentials or personal data unless you trust that endpoint.
- `DELEGATE_BASE_URL` is validated to start with `http://` or `https://`.
  Redirects are refused, so the API key cannot be forwarded to another host;
  a 3xx response is returned as an HTTP error.
- Standard proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`) are
  ignored: the delegate client connects directly to the configured endpoint.
  There is no proxy hop that could observe the API key.
- The API key is in the server process environment. Python scripts run by
  python-runner do not inherit it by default: secret-looking variables are
  stripped from the child environment unless `MCP_RUNNER_INHERIT_SECRETS=1` is
  set explicitly.

### Network exposure

The filesystem and python-runner servers make no network calls of their own,
but scripts they run can. The delegate server contacts only its configured base
URL; proxy environment variables are ignored. No server binds a TCP port for
MCP; the transport is stdio.

### Supply chain and limits

Dependencies are ordinary PyPI packages (`mcp` and its transitive
dependencies). Pin or audit them in sensitive environments. Size limits
(10 MiB reads, 2 MiB writes, 5000 entries, 1000 hits, 50k/20k output, prompt and
response caps) protect against accidental context blow-ups, not against a
determined local attacker.

Known resource caveats:

- On timeout, only the direct child process is killed. Grandchildren spawned by
  the script may keep running.
- stdout and stderr are buffered in memory by `subprocess.run` and truncated
  afterward, so a script that prints without bound can exhaust the server's
  memory before the 50k/20k limits apply.
- `file_info` hashes the whole file even when it exceeds the 10 MiB line-count
  window.
- `search_in_files` and `analyze_output` accept user-supplied regular
  expressions; pathological patterns can be slow.
- The delegate client reads the complete HTTP response before applying the
  response-length cap.
