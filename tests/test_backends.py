"""Exercise the adapter contract and request configuration without other model hosts."""

import json

import httpx
import pytest
from pydantic import ValidationError

from iris_chat_engines.backends import ChatCompletionsBackend
from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_chat_engines.messages import FunctionCall, Message, ToolCall
from iris_settings import Settings


def test_an_injected_adapter_can_drive_the_entire_skill_loop_without_model_http():
    class ExampleBackend:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            assert tools[0]["function"]["name"] == "get_local_time"
            if self.calls == 1:
                return Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="native-call-1",
                            type="function",
                            function=FunctionCall(name="get_local_time", arguments="{}"),
                        )
                    ],
                )
            assert messages[-1].role == "tool" and messages[-1].tool_call_id == "native-call-1"
            assert json.loads(messages[-1].content)["timezone"] == "Europe/London"
            return Message(role="assistant", content="Here's the local time.")

    adapter = ExampleBackend()
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("Unexpected HTTP"))) as c:
        chat = ChatEngine(Settings(), c, backend=adapter)
        assert chat.get_response("What time is it?") == "Here's the local time."
    assert adapter.calls == 2
    assert [m.role for m in chat.history] == ["user", "assistant", "tool", "assistant"]


def test_adapter_errors_leave_history_unchanged():
    class FailedBackend:
        def complete(self, messages, tools):
            raise ChatError("Another provider couldn't answer")

    with httpx.Client() as c:
        chat = ChatEngine(Settings(), c, backend=FailedBackend())
        with pytest.raises(ChatError):
            chat.get_response("Hello")
        assert chat.history == []


def test_host_specific_options_and_template_kwargs_are_opt_in_and_reach_each_request():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == "https://model.example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer model-secret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        plain = Settings(
            base_url="https://model.example.test/v1", allow_remote=True, api_key="model-secret"
        )
        configured = Settings(
            base_url=plain.base_url,
            allow_remote=True,
            api_key="model-secret",
            chat_template_kwargs={"enable_thinking": False},
            chat_request_options={"top_k": 20, "provider_option": {"mode": "example"}},
        )
        for settings in (plain, configured):
            chat = ChatEngine(settings, c)
            assert isinstance(chat.backend, ChatCompletionsBackend)
            chat.get_response("Hello")
            chat.get_response("Hello again")
    for payload in requests[:2]:
        assert "chat_template_kwargs" not in payload and "top_k" not in payload
    for payload in requests[2:]:
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["top_k"] == 20 and payload["provider_option"] == {"mode": "example"}
        assert payload["tool_choice"] == "auto" and payload["stream"] is False


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "max_tokens",
        "temperature",
        "chat_template_kwargs",
    ],
)
def test_request_options_cannot_replace_the_tool_loop_or_named_settings(key):
    with pytest.raises(ValidationError):
        Settings(chat_request_options={key: None})


def test_template_and_request_options_support_json_environment_overrides(monkeypatch):
    monkeypatch.setenv("IRIS_CHAT_TEMPLATE_KWARGS", '{"enable_thinking":false}')
    monkeypatch.setenv("IRIS_CHAT_REQUEST_OPTIONS", '{"top_k":20}')
    settings = Settings()
    assert settings.chat_template_kwargs == {"enable_thinking": False}
    assert settings.chat_request_options == {"top_k": 20}
