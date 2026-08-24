"""The OpenAI-compatible adapter — the LLM seam's one concrete plug.

What matters here is the configuration surface, not the wire: the key comes
from an environment variable and only from there, a missing model or key is
a ConfigError before any request is made, and the request itself is one
user message at temperature 0. The client is injected in every test — the
real ``openai`` library is an optional extra and no test may depend on it.
"""

from __future__ import annotations

import sys
import types

import pytest

from crawler import ConfigError, LLMAnnotator, annotator_from_env, openai_compatible
from crawler.llm import API_KEY_ENV, BASE_URL_ENV, DEFAULT_TIMEOUT, MODEL_ENV, TIMEOUT_ENV


class FakeClient:
    """Records construction and every create() call; answers with a script."""

    def __init__(self, *, base_url=None, api_key=None, timeout=None):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                message = types.SimpleNamespace(content=outer.reply)
                choice = types.SimpleNamespace(message=message)
                return types.SimpleNamespace(choices=[choice])

        self.chat = types.SimpleNamespace(completions=_Completions())
        self.reply = '{"ok": true}'


def factory_of(holder: dict):
    def factory(**kwargs):
        holder["client"] = FakeClient(**kwargs)
        return holder["client"]

    return factory


class TestOpenAICompatible:
    def test_one_user_message_at_temperature_zero(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        holder: dict = {}
        complete = openai_compatible(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            client_factory=factory_of(holder),
        )
        assert complete("describe this table") == '{"ok": true}'

        client = holder["client"]
        assert client.base_url == "http://localhost:11434/v1"
        assert client.api_key == "dummy"
        (call,) = client.calls
        assert call["model"] == "qwen3.5:9b"
        assert call["temperature"] == 0
        assert call["messages"] == [
            {"role": "user", "content": "describe this table"}
        ]

    def test_the_key_comes_from_the_named_variable(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        monkeypatch.setenv("VAULT_LLM_KEY", "s3cret")
        holder: dict = {}
        openai_compatible(
            model="m", api_key_env="VAULT_LLM_KEY", client_factory=factory_of(holder)
        )
        assert holder["client"].api_key == "s3cret"

    def test_a_missing_key_is_a_config_error_not_a_shrug(self, monkeypatch):
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        with pytest.raises(ConfigError, match=API_KEY_ENV):
            openai_compatible(model="m", client_factory=FakeClient)

    def test_a_null_content_answer_becomes_the_empty_string(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        holder: dict = {}
        complete = openai_compatible(model="m", client_factory=factory_of(holder))
        holder["client"].reply = None
        # The empty string then fails JSON parsing in LLMAnnotator, which the
        # pass converts to the explicit unknown — never a crash here.
        assert complete("anything") == ""

    def test_the_missing_extra_is_named_in_the_error(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(ConfigError, match=r"okf\[llm\]"):
            openai_compatible(model="m")


class TestAnnotatorFromEnv:
    def test_everything_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "qwen3.5:9b")
        monkeypatch.setenv(BASE_URL_ENV, "http://localhost:11434/v1")
        monkeypatch.setenv(API_KEY_ENV, "ollama")
        holder: dict = {}
        annotator = annotator_from_env(client_factory=factory_of(holder))
        assert isinstance(annotator, LLMAnnotator)
        assert holder["client"].base_url == "http://localhost:11434/v1"

    def test_a_missing_model_is_a_config_error(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV, raising=False)
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        with pytest.raises(ConfigError, match=MODEL_ENV):
            annotator_from_env(client_factory=FakeClient)

    def test_an_unset_base_url_means_the_vendor_default(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "gpt-x")
        monkeypatch.delenv(BASE_URL_ENV, raising=False)
        monkeypatch.delenv(TIMEOUT_ENV, raising=False)
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        holder: dict = {}
        annotator_from_env(client_factory=factory_of(holder))
        assert holder["client"].base_url is None
        assert holder["client"].timeout == DEFAULT_TIMEOUT

    def test_the_timeout_can_be_stretched_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "gpt-x")
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        monkeypatch.setenv(TIMEOUT_ENV, "900")
        holder: dict = {}
        annotator_from_env(client_factory=factory_of(holder))
        assert holder["client"].timeout == 900.0

    def test_a_non_numeric_timeout_is_a_config_error(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV, "gpt-x")
        monkeypatch.setenv(API_KEY_ENV, "dummy")
        monkeypatch.setenv(TIMEOUT_ENV, "eventually")
        with pytest.raises(ConfigError, match=TIMEOUT_ENV):
            annotator_from_env(client_factory=FakeClient)
