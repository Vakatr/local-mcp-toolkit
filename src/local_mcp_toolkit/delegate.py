"""Delegate MCP server for OpenAI-compatible chat completion endpoints.

Configuration is read from the environment on every call:

``DELEGATE_BASE_URL``       default ``http://localhost:1234/v1``
``DELEGATE_API_KEY``        default empty (no Authorization header)
``DELEGATE_DEFAULT_MODEL``  default ``sub-agent``
``DELEGATE_TIMEOUT``        default 120 seconds
``DELEGATE_MAX_PROMPT``     default 20 000 characters
``DELEGATE_MAX_RESPONSE``   default 50 000 characters

The API key is only placed in the Authorization header.  It is never included
in results, error messages or logs.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP

from . import common

mcp = FastMCP("delegate")

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "sub-agent"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_PROMPT = 20_000
DEFAULT_MAX_RESPONSE = 50_000

MAX_TOKENS_LIMIT = 8192
MAX_TEMPERATURE = 2.0
MAX_HTTP_TIMEOUT = 600
MAX_MODEL_CHARS = 200
MAX_CONFIG_CHARS = 1_000_000
ERROR_BODY_LIMIT = 500

DEFAULT_SYSTEM = (
    "You are a sub-agent specialized in narrow programming tasks. "
    "Execute the assigned task and return only the final result. "
    "Do not narrate or explain your reasoning."
)

# method, url, payload, headers, timeout -> (parsed JSON or None, error or None)
Transport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str], int],
    tuple[Any | None, str | None],
]


@dataclass(frozen=True)
class DelegateConfig:
    """Runtime configuration for the delegate server."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = field(default="", repr=False)
    default_model: str = DEFAULT_MODEL
    timeout: int = DEFAULT_TIMEOUT
    max_prompt_chars: int = DEFAULT_MAX_PROMPT
    max_response_chars: int = DEFAULT_MAX_RESPONSE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DelegateConfig:
        return cls(
            base_url=common.env_str("DELEGATE_BASE_URL", DEFAULT_BASE_URL, env=env).rstrip("/"),
            api_key=common.env_str("DELEGATE_API_KEY", "", env=env),
            default_model=common.env_str("DELEGATE_DEFAULT_MODEL", DEFAULT_MODEL, env=env),
            timeout=common.env_int(
                "DELEGATE_TIMEOUT",
                DEFAULT_TIMEOUT,
                minimum=1,
                maximum=MAX_HTTP_TIMEOUT,
                env=env,
            ),
            max_prompt_chars=common.env_int(
                "DELEGATE_MAX_PROMPT",
                DEFAULT_MAX_PROMPT,
                minimum=1,
                maximum=MAX_CONFIG_CHARS,
                env=env,
            ),
            max_response_chars=common.env_int(
                "DELEGATE_MAX_RESPONSE",
                DEFAULT_MAX_RESPONSE,
                minimum=1,
                maximum=MAX_CONFIG_CHARS,
                env=env,
            ),
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the API key is never forwarded to another host."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirectHandler,
)


def _open_url(request: urllib.request.Request, timeout: int) -> Any:
    return _OPENER.open(request, timeout=timeout)


def _public_base_url(base_url: str) -> str:
    """Drop userinfo from a base URL before returning it to the client."""
    parts = urlsplit(base_url)
    if parts.username is None and parts.password is None:
        return base_url
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _urllib_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: int,
) -> tuple[Any | None, str | None]:
    """Perform one HTTP request and normalize all failures to an error string."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _open_url(request, timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except (OSError, AttributeError):
            error_body = ""
        return None, f"HTTP {e.code}: {error_body[:ERROR_BODY_LIMIT]}"
    except urllib.error.URLError as e:
        return None, f"URLError: {e.reason}"
    except (TimeoutError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"

    try:
        return json.loads(body), None
    except json.JSONDecodeError as e:
        return None, f"invalid JSON response: {e}"


def _headers(config: DelegateConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "local-mcp-toolkit/0.1",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _request(
    config: DelegateConfig,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    timeout: int,
    transport: Transport | None = None,
) -> tuple[Any | None, str | None]:
    if not config.base_url.startswith(("http://", "https://")):
        return None, "invalid DELEGATE_BASE_URL (must start with http:// or https://)"

    url = f"{config.base_url}{path}"
    sender = transport or _urllib_request
    try:
        response, error = sender(method, url, payload, _headers(config), timeout)
    except Exception as e:  # noqa: BLE001 - transports may raise anything
        message = f"transport error: {type(e).__name__}: {e}"
        return None, _redact(message, config.api_key)
    return response, _redact(error, config.api_key)


def _redact(text: str | None, secret: str) -> str | None:
    """Remove a reflected API key from an error string."""
    if text and secret:
        return text.replace(secret, "***")
    return text


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return default


def delegate_impl(
    task: str,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout: int | None = None,
    *,
    config: DelegateConfig | None = None,
    transport: Transport | None = None,
) -> dict:
    """Send a self-contained task to the delegate endpoint."""
    config = config or DelegateConfig.from_env()

    if not isinstance(task, str) or not task.strip():
        return common.error("empty task")
    if len(task) > config.max_prompt_chars:
        return common.error(f"task too long: {len(task)} chars > {config.max_prompt_chars}")

    target_model = config.default_model if model is None else model
    if not isinstance(target_model, str) or not target_model.strip():
        return common.error("model must be a non-empty string")
    target_model = target_model.strip()
    if len(target_model) > MAX_MODEL_CHARS:
        return common.error(f"model too long: {len(target_model)} chars > {MAX_MODEL_CHARS}")

    if system is not None and not isinstance(system, str):
        return common.error("system must be a string")

    effective_timeout = _bounded_int(
        config.timeout if timeout is None else timeout,
        config.timeout,
        1,
        MAX_HTTP_TIMEOUT,
    )
    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system if system else DEFAULT_SYSTEM},
            {"role": "user", "content": task},
        ],
        "max_tokens": _bounded_int(max_tokens, 2048, 1, MAX_TOKENS_LIMIT),
        "temperature": _bounded_float(temperature, 0.7, 0.0, MAX_TEMPERATURE),
        "stream": False,
    }

    start = time.monotonic()
    response, err = _request(
        config, "POST", "/chat/completions", payload, effective_timeout, transport
    )
    duration_ms = int((time.monotonic() - start) * 1000)

    if err:
        return {
            "ok": False,
            "model": target_model,
            "error": err,
            "duration_ms": duration_ms,
        }
    if not isinstance(response, dict):
        return {
            "ok": False,
            "model": target_model,
            "error": "bad response shape: expected a JSON object",
            "duration_ms": duration_ms,
        }

    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return {
            "ok": False,
            "model": target_model,
            "error": f"bad response shape: {e}",
            "duration_ms": duration_ms,
        }
    if not isinstance(content, str):
        return {
            "ok": False,
            "model": target_model,
            "error": "bad response shape: message content is not a string",
            "duration_ms": duration_ms,
        }

    content, truncated = common.truncate_text(content, config.max_response_chars)
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    return {
        "ok": True,
        "model": target_model,
        "content": content,
        "truncated": truncated,
        "usage": usage,
        "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
        "duration_ms": duration_ms,
    }


def list_models_impl(
    *,
    config: DelegateConfig | None = None,
    transport: Transport | None = None,
) -> dict:
    """List models exposed by the configured endpoint."""
    config = config or DelegateConfig.from_env()
    response, err = _request(
        config, "GET", "/models", None, max(1, min(config.timeout, 10)), transport
    )
    if err:
        return {
            "ok": False,
            "base_url": _public_base_url(config.base_url),
            "error": err,
        }
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        return {
            "ok": False,
            "base_url": _public_base_url(config.base_url),
            "error": "bad response shape: expected a data list",
        }

    models = [
        item["id"]
        for item in response["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    ]
    return {
        "ok": True,
        "base_url": _public_base_url(config.base_url),
        "count": len(models),
        "models": models,
    }


def health_check_impl(
    *,
    config: DelegateConfig | None = None,
    transport: Transport | None = None,
) -> dict:
    """Check that the endpoint answers ``GET /models``."""
    config = config or DelegateConfig.from_env()
    start = time.monotonic()
    response, err = _request(
        config, "GET", "/models", None, max(1, min(config.timeout, 5)), transport
    )
    latency_ms = int((time.monotonic() - start) * 1000)
    if err:
        return {
            "ok": False,
            "base_url": _public_base_url(config.base_url),
            "error": err,
            "latency_ms": latency_ms,
        }

    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        return {
            "ok": False,
            "base_url": _public_base_url(config.base_url),
            "error": "bad response shape: expected a data list",
            "latency_ms": latency_ms,
        }
    return {
        "ok": True,
        "base_url": _public_base_url(config.base_url),
        "model_count": len(data),
        "latency_ms": latency_ms,
    }


@mcp.tool()
def delegate(
    task: str,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout: int | None = None,
) -> dict:
    """Send a self-contained task to another LLM and return its answer.

    Args:
        task: Full task description; it must not rely on the caller's context.
        model: Model identifier; defaults to ``DELEGATE_DEFAULT_MODEL``.
        system: Optional system prompt.
        max_tokens: Generation cap, clamped to 1..8192.
        temperature: Sampling temperature, clamped to 0.0..2.0.
        timeout: HTTP timeout in seconds; defaults to ``DELEGATE_TIMEOUT``.
    """
    return delegate_impl(
        task,
        model=model,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


@mcp.tool()
def list_models() -> dict:
    """List models available on the configured delegate endpoint."""
    return list_models_impl()


@mcp.tool()
def health_check() -> dict:
    """Check reachability of the configured delegate endpoint."""
    return health_check_impl()


def main() -> None:
    """Run the delegate MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
