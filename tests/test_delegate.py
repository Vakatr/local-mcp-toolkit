"""Tests for the delegate server. No real network access is used."""

from __future__ import annotations

import io
import json
import types
import urllib.error
from typing import Any

import pytest

from local_mcp_toolkit import delegate


class Recorder:
    """A transport double that records calls and returns a canned response."""

    def __init__(
        self,
        response: Any = None,
        error: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: int,
    ) -> tuple[Any, str | None]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if self.raises:
            raise self.raises
        return self.response, self.error


def _config(**overrides: Any) -> delegate.DelegateConfig:
    values: dict[str, Any] = {
        "base_url": "http://delegate.test/v1",
        "api_key": "top-secret",
        "default_model": "test-model",
        "timeout": 30,
        "max_prompt_chars": 20_000,
        "max_response_chars": 50_000,
    }
    values.update(overrides)
    return delegate.DelegateConfig(**values)


def _chat_response(content: str = "answer", **extra: Any) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 3},
        **extra,
    }


def test_config_defaults() -> None:
    config = delegate.DelegateConfig.from_env(env={})
    assert config.base_url == "http://localhost:1234/v1"
    assert config.api_key == ""
    assert config.default_model == "sub-agent"
    assert config.timeout == 120
    assert config.max_prompt_chars == 20_000
    assert config.max_response_chars == 50_000


def test_config_from_env_mapping() -> None:
    env = {
        "DELEGATE_BASE_URL": " http://example.test/v1/ ",
        "DELEGATE_API_KEY": "secret",
        "DELEGATE_DEFAULT_MODEL": "custom",
        "DELEGATE_TIMEOUT": "9",
        "DELEGATE_MAX_PROMPT": "1000",
        "DELEGATE_MAX_RESPONSE": "2000",
    }
    config = delegate.DelegateConfig.from_env(env=env)
    assert config.base_url == "http://example.test/v1"
    assert config.api_key == "secret"
    assert config.default_model == "custom"
    assert config.timeout == 9
    assert config.max_prompt_chars == 1000
    assert config.max_response_chars == 2000


def test_config_invalid_values_fall_back_and_clamp() -> None:
    env = {
        "DELEGATE_TIMEOUT": "not-a-number",
        "DELEGATE_MAX_PROMPT": "0",
        "DELEGATE_MAX_RESPONSE": "999999999",
    }
    config = delegate.DelegateConfig.from_env(env=env)
    assert config.timeout == 120
    assert config.max_prompt_chars == 1
    assert config.max_response_chars == delegate.MAX_CONFIG_CHARS


def test_config_reads_os_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DELEGATE_BASE_URL", "http://from-os/v1")
    monkeypatch.setenv("DELEGATE_DEFAULT_MODEL", "os-model")
    config = delegate.DelegateConfig.from_env()
    assert config.base_url == "http://from-os/v1"
    assert config.default_model == "os-model"


def test_delegate_payload_and_result() -> None:
    recorder = Recorder(_chat_response())
    result = delegate.delegate_impl(
        "do the task",
        max_tokens=99999,
        temperature=5.0,
        timeout=9999,
        config=_config(),
        transport=recorder,
    )
    assert result["ok"] is True
    assert result["content"] == "answer"
    assert result["model"] == "test-model"
    assert result["usage"] == {"total_tokens": 3}
    assert result["finish_reason"] == "stop"
    assert result["truncated"] is False
    assert isinstance(result["duration_ms"], int)

    call = recorder.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://delegate.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer top-secret"
    assert call["timeout"] == delegate.MAX_HTTP_TIMEOUT
    payload = call["payload"]
    assert payload["model"] == "test-model"
    assert payload["max_tokens"] == delegate.MAX_TOKENS_LIMIT
    assert payload["temperature"] == delegate.MAX_TEMPERATURE
    assert payload["stream"] is False
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert payload["messages"][1]["content"] == "do the task"
    assert "top-secret" not in json.dumps(result)


def test_delegate_custom_model_and_system() -> None:
    recorder = Recorder(_chat_response())
    result = delegate.delegate_impl(
        "task",
        model="other-model",
        system="be terse",
        config=_config(),
        transport=recorder,
    )
    assert result["ok"] is True
    payload = recorder.calls[0]["payload"]
    assert payload["model"] == "other-model"
    assert payload["messages"][0]["content"] == "be terse"


def test_delegate_without_api_key_sends_no_authorization() -> None:
    recorder = Recorder(_chat_response())
    delegate.delegate_impl("task", config=_config(api_key=""), transport=recorder)
    assert "Authorization" not in recorder.calls[0]["headers"]


@pytest.mark.parametrize("task", ["", "   ", None])
def test_delegate_rejects_empty_task(task: Any) -> None:
    recorder = Recorder(_chat_response())
    result = delegate.delegate_impl(task, config=_config(), transport=recorder)
    assert result["ok"] is False
    assert "empty task" in result["error"]
    assert recorder.calls == []


def test_delegate_rejects_too_long_task() -> None:
    recorder = Recorder(_chat_response())
    result = delegate.delegate_impl(
        "123456", config=_config(max_prompt_chars=5), transport=recorder
    )
    assert result["ok"] is False
    assert "task too long" in result["error"]


@pytest.mark.parametrize("model", ["", "   ", "x" * (delegate.MAX_MODEL_CHARS + 1)])
def test_delegate_rejects_invalid_model(model: str) -> None:
    recorder = Recorder(_chat_response())
    result = delegate.delegate_impl("task", model=model, config=_config(), transport=recorder)
    assert result["ok"] is False
    assert "model" in result["error"]
    assert recorder.calls == []


def test_delegate_rejects_non_string_system() -> None:
    result = delegate.delegate_impl(
        "task",
        system=123,
        config=_config(),
        transport=Recorder(),  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert "system must be a string" in result["error"]


def test_delegate_clamps_invalid_numeric_arguments() -> None:
    recorder = Recorder(_chat_response())
    result = delegate.delegate_impl(
        "task",
        max_tokens="many",  # type: ignore[arg-type]
        temperature="hot",  # type: ignore[arg-type]
        timeout="never",  # type: ignore[arg-type]
        config=_config(timeout=7),
        transport=recorder,
    )
    assert result["ok"] is True
    payload = recorder.calls[0]["payload"]
    assert payload["max_tokens"] == 2048
    assert payload["temperature"] == 0.7
    assert recorder.calls[0]["timeout"] == 7


def test_delegate_default_timeout_comes_from_config() -> None:
    recorder = Recorder(_chat_response())
    delegate.delegate_impl("task", config=_config(timeout=7), transport=recorder)
    assert recorder.calls[0]["timeout"] == 7


def test_delegate_truncates_response() -> None:
    recorder = Recorder(_chat_response("abcdefgh"))
    result = delegate.delegate_impl(
        "task", config=_config(max_response_chars=5), transport=recorder
    )
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["content"].startswith("abcde")
    assert result["content"].endswith("... [truncated]")


def test_config_api_key_is_not_in_repr() -> None:
    assert "top-secret" not in repr(_config())


def test_delegate_redacts_reflected_key() -> None:
    recorder = Recorder(None, "HTTP 400: rejected key top-secret")
    result = delegate.delegate_impl("task", config=_config(), transport=recorder)
    assert result["ok"] is False
    assert "top-secret" not in json.dumps(result)
    assert "***" in result["error"]


def test_delegate_http_error() -> None:
    recorder = Recorder(None, "HTTP 500: upstream failed")
    result = delegate.delegate_impl("task", config=_config(), transport=recorder)
    assert result["ok"] is False
    assert result["model"] == "test-model"
    assert result["error"] == "HTTP 500: upstream failed"
    assert "top-secret" not in json.dumps(result)


def test_delegate_transport_exception() -> None:
    recorder = Recorder(raises=RuntimeError("connection dropped"))
    result = delegate.delegate_impl("task", config=_config(), transport=recorder)
    assert result["ok"] is False
    assert "transport error: RuntimeError" in result["error"]
    assert "top-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "response",
    [
        "not-an-object",
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": 42}}]},
        {"choices": ["bad"]},
    ],
)
def test_delegate_bad_response_shapes(response: Any) -> None:
    result = delegate.delegate_impl("task", config=_config(), transport=Recorder(response))
    assert result["ok"] is False
    assert "bad response shape" in result["error"]


def test_delegate_non_dict_usage_becomes_empty() -> None:
    response = _chat_response()
    response["usage"] = "n/a"
    result = delegate.delegate_impl("task", config=_config(), transport=Recorder(response))
    assert result["ok"] is True
    assert result["usage"] == {}


def test_list_models() -> None:
    recorder = Recorder(
        {"data": [{"id": "alpha"}, {"id": ""}, {"nope": 1}, "junk", {"id": "beta"}]}
    )
    result = delegate.list_models_impl(config=_config(), transport=recorder)
    assert result["ok"] is True
    assert result["models"] == ["alpha", "beta"]
    assert result["count"] == 2
    assert recorder.calls[0]["method"] == "GET"
    assert recorder.calls[0]["url"] == "http://delegate.test/v1/models"
    assert recorder.calls[0]["timeout"] == 10


def test_list_models_errors() -> None:
    failed = delegate.list_models_impl(
        config=_config(), transport=Recorder(None, "URLError: refused")
    )
    assert failed["ok"] is False
    assert "URLError" in failed["error"]

    bad_shape = delegate.list_models_impl(config=_config(), transport=Recorder({"data": "nope"}))
    assert bad_shape["ok"] is False
    assert "bad response shape" in bad_shape["error"]


def test_health_check_success() -> None:
    recorder = Recorder({"data": [{"id": "a"}, {"id": "b"}]})
    result = delegate.health_check_impl(config=_config(timeout=30), transport=recorder)
    assert result["ok"] is True
    assert result["model_count"] == 2
    assert isinstance(result["latency_ms"], int)
    assert recorder.calls[0]["timeout"] == 5


def test_health_check_errors() -> None:
    failed = delegate.health_check_impl(
        config=_config(), transport=Recorder(None, "HTTP 503: down")
    )
    assert failed["ok"] is False
    assert "HTTP 503" in failed["error"]

    bad_shape = delegate.health_check_impl(config=_config(), transport=Recorder({"data": {}}))
    assert bad_shape["ok"] is False
    assert "bad response shape" in bad_shape["error"]


@pytest.mark.parametrize("function", ["delegate", "list_models", "health_check"])
def test_invalid_base_url_is_rejected(function: str) -> None:
    config = _config(base_url="localhost:1234/v1")
    recorder = Recorder()
    if function == "delegate":
        result = delegate.delegate_impl("task", config=config, transport=recorder)
    elif function == "list_models":
        result = delegate.list_models_impl(config=config, transport=recorder)
    else:
        result = delegate.health_check_impl(config=config, transport=recorder)
    assert result["ok"] is False
    assert "invalid DELEGATE_BASE_URL" in result["error"]
    assert recorder.calls == []


def test_redirects_are_refused() -> None:
    handler = delegate._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", {}, "http://evil.test") is None
    assert any(isinstance(item, delegate._NoRedirectHandler) for item in delegate._OPENER.handlers)
    assert not any(
        type(item) is delegate.urllib.request.HTTPRedirectHandler
        for item in delegate._OPENER.handlers
    )
    # An empty proxy map prevents HTTP(S)_PROXY from redirecting the key elsewhere.
    for item in delegate._OPENER.handlers:
        if isinstance(item, delegate.urllib.request.ProxyHandler):
            assert item.proxies == {}


def test_base_url_credentials_are_redacted() -> None:
    config = _config(base_url="http://user:password@delegate.test/v1")
    recorder = Recorder({"data": [{"id": "a"}]})
    result = delegate.list_models_impl(config=config, transport=recorder)
    assert result["ok"] is True
    assert result["base_url"] == "http://delegate.test/v1"
    assert "password" not in json.dumps(result)
    assert (
        delegate._public_base_url("http://user:password@delegate.test:8443/v1")
        == "http://delegate.test:8443/v1"
    )
    assert (
        delegate._public_base_url("http://user:password@delegate.test:bad/v1")
        == "http://delegate.test/v1"
    )


def test_urllib_request_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(delegate._OPENER, "open", fake_urlopen)
    response, error = delegate._urllib_request(
        "POST",
        "http://example.test/v1/chat/completions",
        {"model": "m"},
        {"Authorization": "Bearer secret"},
        3,
    )
    assert error is None
    assert response == {"ok": True}
    assert captured["timeout"] == 3
    request = captured["request"]
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {"model": "m"}
    assert request.get_header("Authorization") == "Bearer secret"


def test_urllib_request_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: int) -> None:
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", None, io.BytesIO(b'{"error":"nope"}')
        )

    monkeypatch.setattr(delegate._OPENER, "open", fake_urlopen)
    response, error = delegate._urllib_request("GET", "http://example.test/v1/models", None, {}, 3)
    assert response is None
    assert error is not None
    assert "HTTP 401" in error
    assert "nope" in error


def test_urllib_request_http_error_without_readable_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenBody:
        def read(self, *args: object) -> bytes:
            raise OSError("body unavailable")

        def close(self) -> None:
            pass

    def fake_urlopen(request: Any, timeout: int) -> None:
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", None, BrokenBody())

    monkeypatch.setattr(delegate._OPENER, "open", fake_urlopen)
    response, error = delegate._urllib_request("GET", "http://example.test/v1/models", None, {}, 3)
    assert response is None
    assert error == "HTTP 500: "


def test_urllib_request_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: int) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(delegate._OPENER, "open", fake_urlopen)
    response, error = delegate._urllib_request("GET", "http://example.test/v1/models", None, {}, 3)
    assert response is None
    assert error == "URLError: connection refused"


def test_urllib_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: int) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(delegate._OPENER, "open", fake_urlopen)
    response, error = delegate._urllib_request("GET", "http://example.test/v1/models", None, {}, 3)
    assert response is None
    assert error == "TimeoutError: timed out"


def test_urllib_request_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr(delegate._OPENER, "open", lambda request, timeout: FakeResponse())
    response, error = delegate._urllib_request("GET", "http://example.test/v1/models", None, {}, 3)
    assert response is None
    assert error is not None
    assert "invalid JSON" in error


def test_mcp_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    # An invalid URL makes the wrappers fail fast without any network access.
    monkeypatch.setenv("DELEGATE_BASE_URL", "localhost:1234/v1")
    assert delegate.delegate("task")["ok"] is False
    assert delegate.list_models()["ok"] is False
    assert delegate.health_check()["ok"] is False


def test_main_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(delegate, "mcp", types.SimpleNamespace(run=lambda: calls.append(True)))
    delegate.main()
    assert calls == [True]
