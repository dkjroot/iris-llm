import json

import httpx
import pytest
from pydantic import SecretStr

from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_settings import Settings


def reply(text: str) -> dict[str, object]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.mark.parametrize("text", ["I'm fine", "I’m fine", "Use &#x27; for an apostrophe", "A & B"])
def test_plain_text_is_not_html(text: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=reply(text)))
    ) as client:
        chat = ChatEngine(Settings(), client)
        assert chat.get_response("hello") == text
        assert chat.history[-1].content == text


@pytest.mark.parametrize(
    ("encoding", "text", "expected"),
    [
        ("html", "I&#x27;m fine &amp; well", "I'm fine & well"),
        ("double-html", "I&amp;#x27;m fine", "I'm fine"),
        ("html", "I&amp;#x27;m fine", "I&#x27;m fine"),
        ("double-html", "I’m fine", "I’m fine"),
    ],
)
def test_explicit_legacy_decoding_is_bounded(encoding: str, text: str, expected: str) -> None:
    settings = Settings.model_validate({"reply_encoding": encoding})
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=reply(text)))
    ) as client:
        assert ChatEngine(settings, client).get_response("hello") == expected


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"role": "assistant", "content": None}}]},
        reply(" "),
        {"choices": [{"message": {"role": "user", "content": "wrong role"}}]},
    ],
)
def test_invalid_replies_do_not_commit_a_turn(body: dict[str, object]) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        chat = ChatEngine(Settings(), client)
        with pytest.raises(ChatError):
            chat.get_response("hello")
        assert chat.history == []


def test_request_history_is_bounded_per_instance_and_preserves_newlines() -> None:
    requests: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:5001/v1/chat/completions"
        assert request.extensions["timeout"]["connect"] == 10
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=reply("One.\nTwo."))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(history_turns=1), client)
        assert chat.get_response("first") == "One.\nTwo."
        chat.get_response("second")
        chat.get_response("third")
        assert len(chat.history) == 2
        assert chat.history[0].content == "third"
        assert len(requests[2]["messages"]) == 4
        assert ChatEngine(Settings(), client).history == []


@pytest.mark.parametrize("kind", ["timeout", "connection", "http", "json"])
def test_failed_turn_keeps_successful_history(kind: str) -> None:
    failed = False

    def handle(request: httpx.Request) -> httpx.Response:
        if not failed:
            return httpx.Response(200, json=reply("hello"))
        if kind == "timeout":
            raise httpx.ReadTimeout("private diagnostic", request=request)
        if kind == "connection":
            raise httpx.ConnectError("private diagnostic", request=request)
        if kind == "http":
            return httpx.Response(500, text="sensitive server body")
        return httpx.Response(200, text="not json")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(), client)
        chat.get_response("first")
        before = chat.history.copy()
        failed = True
        with pytest.raises(ChatError) as error:
            chat.get_response("second")
        assert "sensitive" not in str(error.value)
        assert "private diagnostic" not in str(error.value)
        assert chat.history == before


def test_zero_history_and_trace_never_prints_authentication(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-test-key"
        return httpx.Response(200, json=reply("hi"))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        settings = Settings(history_turns=0, api_key=SecretStr("secret-test-key"), trace=True)
        chat = ChatEngine(settings, client)
        chat.get_response("hello")
        assert chat.history == []
        output = capsys.readouterr().out
        assert "hello" in output and "hi" in output
        assert "secret-test-key" not in output


def test_empty_input_never_calls_server() -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No request expected")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(ChatError):
            ChatEngine(Settings(), client).get_response(" ")
