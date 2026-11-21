"""Agentic skills with fixed clocks and fake HTTP; no models or audio devices."""

import json
from datetime import UTC, datetime
from typing import Literal
from unittest.mock import Mock, call, patch

import httpx
import pytest
from pydantic import ConfigDict, Field

from iris_chat_engines.compatible import ChatEngine, ChatError, Message
from iris_domain.turn import effect_scope
from iris_droid.protocol import write_wav
from iris_settings import Settings
from iris_tools import NoArguments, Skill, ToolRegistry


def response(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_call(name="get_local_time", arguments="{}", call_id="clock"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def calling(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "I need fresh information.",
                    "tool_calls": list(calls),
                }
            }
        ]
    }


def test_model_tool_schema_omits_only_generated_titles():
    class Options(NoArguments):
        model_config = ConfigDict(title="Options")
        mode: Literal["quick", "full"] = "quick"
        count: int = Field(ge=1, le=5, description="Number of items.")

    class Arguments(NoArguments):
        title: str = Field(title="Title", min_length=3)
        options: Options

    skill = Skill("example", "An example skill.", Arguments, lambda _: {})
    with httpx.Client() as client:
        registry = ToolRegistry(Settings(tools_enabled=False), client)
        registry.register(skill)
        parameters = registry.definitions()[0]["function"]["parameters"]

    expected = Arguments.model_json_schema()
    del expected["title"]
    options = expected["$defs"]["Options"]["properties"]
    del options["mode"]["title"]
    del options["count"]["title"]
    assert parameters == expected


def test_compact_tool_results_keep_unicode_and_duplicate_effect_guard():
    with httpx.Client() as client, effect_scope() as effects:
        registry = ToolRegistry(Settings(), client)
        run = Mock(return_value={"status": "café", "details": [1, 2]})
        registry.register(Skill("example", "An example effect.", NoArguments, run, effect=True))

        assert registry.execute("example", "{}") == '{"status":"café","details":[1,2]}'
        assert registry.execute("example", "{}") == (
            '{"error":"This action was already attempted in this turn. '
            'Check its result before retrying."}'
        )
        run.assert_called_once()
        assert [receipt.outcome for receipt in effects.receipts] == ["returned"]


@pytest.mark.parametrize(
    "summer,offset,abbreviation", [(False, "+00:00", "GMT"), (True, "+01:00", "BST")]
)
def test_clock_uses_configured_timezone_and_daylight_saving(summer, offset, abbreviation):
    instant = datetime(2026, 7 if summer else 11, 1, 12, 34, 56, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    with httpx.Client() as client, patch("iris_tools.datetime", Clock):
        result = json.loads(ToolRegistry(Settings(), client).execute("get_local_time", "{}"))
    assert result["datetime"].endswith(offset)
    assert result["abbreviation"] == abbreviation
    assert result["time"] == ("13:34:56" if summer else "12:34:56")
    assert result["date"] == ("2026-07-01" if summer else "2026-11-01")


def test_clock_can_use_another_timezone():
    with httpx.Client() as client:
        result = ToolRegistry(Settings(local_timezone="Asia/Tokyo"), client).local_time()
    assert result["timezone"] == "Asia/Tokyo"
    assert result["datetime"].endswith("+09:00")


def test_model_chooses_tool_then_receives_result_and_answers_with_complete_history():
    requests = []
    responses = iter([calling(tool_call()), response("It is Sunday."), response("You're welcome.")])

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(history_turns=1), client)
        assert chat.get_response("What day is it?") == "It is Sunday."
        assert requests[0]["tool_choice"] == "auto"
        assert [tool["function"]["name"] for tool in requests[0]["tools"]] == ["get_local_time"]
        messages = requests[1]["messages"]
        assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool"]
        assert messages[-2]["reasoning_content"] == "I need fresh information."
        assert messages[-1]["tool_call_id"] == "clock"
        assert json.loads(messages[-1]["content"])["timezone"] == "Europe/London"
        assert [m.role for m in chat.history] == ["user", "assistant", "tool", "assistant"]
        chat.get_response("Thanks")
        assert [m.role for m in chat.history] == ["user", "assistant"]
        assert chat.history[0].content == "Thanks"
        assert len(requests[2]["messages"]) == 6  # Whole previous exchange, not orphaned results.


@pytest.mark.parametrize("face", [False, True])
def test_bare_tool_marker_is_reprompted_before_dispatch_and_stays_out_of_history(face):
    cue = "[[expression:curious]]\n" if face else ""
    requests = []
    responses = iter(
        [
            response(
                cue + "I'll check that right away.\n\n[[get_local_time]]\n[[get_local_weather]]"
            ),
            calling(tool_call(), tool_call("get_local_weather", call_id="weather")),
            response(cue + "It's Sunday, and it's raining."),
            response("You're welcome."),
        ]
    )

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(weather_enabled=True), client)
        with patch.object(chat.tools, "execute", return_value="{}") as execute:
            reply = chat.generate_reply("What day is it, and how's the weather?", face=face)
            assert execute.call_args_list == [
                call("get_local_time", "{}"),
                call("get_local_weather", "{}"),
            ]
        assert reply.text == "It's Sunday, and it's raining."
        assert reply.expression == ("curious" if face else None)
        assert chat.last_tool_names == ("get_local_time", "get_local_weather")
        assert [message.role for message in chat.history] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "assistant",
        ]
        chat.get_response("Thanks")
    assert len(requests) == 4
    original, correction, result, followup = [request["messages"] for request in requests]
    assert correction[0] == original[0]
    assert correction[-1]["content"].startswith(original[-1]["content"] + "\n\n")
    assert "call the appropriate function using the tools provided" in correction[-1]["content"]
    assert [message["tool_call_id"] for message in result[-2:]] == ["clock", "weather"]
    assert result[1] == original[1]
    followup_json = json.dumps(followup)
    assert "The previous reply used tool names as plain text" not in followup_json
    assert "I'll check that right away." not in followup_json
    assert "[[get_local_time]]" not in followup_json
    assert "[[get_local_weather]]" not in followup_json


def test_history_marker_examples_are_replaced_only_in_outgoing_requests():
    requests = []
    responses = iter([calling(tool_call()), response("It's Sunday."), response("Of course.")])

    def handle(request):
        requests.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json=next(responses))

    genuine_call = Message.model_validate(
        calling(tool_call(call_id="old"))["choices"][0]["message"]
    )
    genuine_call.content = "[[get_local_time]]"
    old_result = Message(role="tool", tool_call_id="old", content="An old date.")
    old_reply = "I'll check right away.\n[[get_local_time]]\n[[search_memory]]\n[[get_local_time]]"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(), client)
        chat.tools.skills["search_memory"] = Skill(
            "search_memory", "Search memory", NoArguments, lambda args: {}
        )
        chat.history = [
            Message(role="user", content="Yesterday's question"),
            genuine_call,
            old_result,
            Message(role="assistant", content="Yesterday's answer"),
        ]
        chat.remember_reply("What day is it now?", old_reply)
        prior_history = chat.history.copy()
        assert chat.get_response("Try again.") == "It's Sunday."
        assert chat.get_response("Thanks.") == "Of course."
        assert chat.history[: len(prior_history)] == prior_history
        assert chat.history[len(prior_history)].content == "Try again."
    assert len(requests) == 3
    for messages in requests:
        assert messages[2] == genuine_call.model_dump(exclude_none=True)
        assert messages[3] == old_result.model_dump(exclude_none=True)
        assert messages[6]["content"] == (
            "I mentioned get_local_time, search_memory but did not call those tools."
        )
    assert requests[0][-1]["content"].startswith(
        "Try again.\n\nThe previous reply used tool names as plain text."
    )
    assert requests[1][7]["content"] == "Try again."
    assert requests[2][-1]["content"] == "Thanks."


@pytest.mark.parametrize("enabled", [False, True])
def test_repeated_bare_tool_marker_fails_without_dispatch_or_history(enabled):
    requests = []
    responses = iter(
        [response("Hello."), response("[[set_home_power]]"), response("[[set_home_power]]")]
    )

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(tools_enabled=enabled), client)
        chat.get_response("Hello")
        before = chat.history.copy()
        with patch.object(chat.tools, "execute") as execute:
            with pytest.raises(ChatError, match="tool marker instead of an answer"):
                chat.get_response("Can you control the lights?")
            execute.assert_not_called()
        assert chat.history == before
    assert len(requests) == 3
    assert requests[1].get("tools") == requests[2].get("tools")


def test_repeated_marker_never_reaches_voice_or_durable_memory(brain_factory):
    invalid = Message(
        role="assistant",
        content="I'll check right away.\n[[get_local_time]]\n[[get_local_weather]]",
    )
    harness = brain_factory(memory_enabled=True, replies=[invalid, invalid])
    wav = write_wav(b"\1\0" * 1600, 16000)
    with pytest.raises(ChatError, match="tool marker instead of an answer"):
        harness.brain.turn("session", wav, face=True)
    assert len(harness.model.calls) == 2
    assert harness.tts.calls == []
    assert harness.brain._session("session").history == []
    assert not harness.brain.memory.export("default")["exchanges"]


@pytest.mark.parametrize(
    "text",
    [
        "The model printed [[get_local_time]] in the reply.",
        "`[[get_local_time]]`",
        "It printed this example:\n```text\n[[get_local_time]]\n```",
        "It printed this example:\n\n    [[get_local_time]]",
        "[[unrelated_note]]",
    ],
)
def test_quoted_or_unrelated_bracket_text_remains_literal(text):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response("[[expression:focused]]\n" + text))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(), client)
        reply = chat.generate_reply("Show me the example.", face=True)
        assert reply.text == text
        assert reply.expression == "focused"
        assert chat.history[-1].content == text
    assert len(requests) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_ordinary_conversation_finishes_without_tools_and_disabling_restores_plain_api(enabled):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response("Tell me more."))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(tools_enabled=enabled, conversation_style_prompt=""), client)
        assert chat.get_response("I've had a lovely day") == "Tell me more."
    assert len(requests) == 1
    assert ("tools" in requests[0]) is enabled
    if not enabled:
        assert "tool_choice" not in requests[0]
        assert requests[0]["messages"][0]["content"] == chat.settings.system_prompt


@pytest.mark.parametrize(
    "call",
    [tool_call("invented"), tool_call(arguments='{"extra": 1}'), tool_call(arguments="not json")],
)
def test_bad_tool_request_returns_error_to_model_so_it_can_recover(call):
    responses = iter([calling(call), response("I couldn't check that.")])
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(), client)
        assert chat.get_response("Check please") == "I couldn't check that."
    assert "error" in json.loads(requests[1]["messages"][-1]["content"])


@pytest.mark.parametrize("failure", ["loop", "duplicate", "http", "malformed", "disabled"])
def test_failed_tool_turn_leaves_previous_history_unchanged(failure):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(200, json=response("Hello."))
        if failure == "http" and count == 3:
            return httpx.Response(500)
        call = tool_call(call_id=str(count))
        if failure == "malformed":
            call["function"]["arguments"] = {"bad": "object"}
        body = calling(call, call) if failure == "duplicate" else calling(call)
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(tools_enabled=failure != "disabled"), client)
        chat.get_response("Hello")
        before = chat.history.copy()
        with pytest.raises(ChatError):
            chat.get_response("Check the time")
        assert chat.history == before
    assert count <= 6


def weather_payload(days=2):
    return {
        "current": {
            "time": "2026-11-01T12:00",
            "temperature_2m": 12,
            "apparent_temperature": 10,
            "weather_code": 3,
            "wind_speed_10m": 6,
        },
        "daily": {
            "time": [f"2026-11-0{i + 1}" for i in range(days)],
            "weather_code": [61] * days,
            "temperature_2m_max": [13] * days,
            "temperature_2m_min": [7] * days,
            "precipitation_probability_max": [70] * days,
            "precipitation_sum": [2.5] * days,
            "wind_speed_10m_max": [12] * days,
        },
    }


def weather_handler(requests, *, failure=None):
    def handle(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": []
                    if failure == "place"
                    else [
                        {
                            "name": "Stroud",
                            "latitude": 51.75,
                            "longitude": -2.2,
                            "country_code": "GB",
                            "admin2": "Gloucestershire",
                        }
                    ]
                },
            )
        assert request.url.host == "api.open-meteo.com"
        if failure == "timeout":
            raise httpx.ReadTimeout("private error", request=request)
        if failure == "http":
            return httpx.Response(503)
        payload = weather_payload()
        if failure == "shape":
            payload["daily"]["weather_code"] = []
        if failure == "nan":
            payload["current"]["temperature_2m"] = "NaN"
        return httpx.Response(200, json=payload)

    return handle


def test_weather_resolves_configured_town_and_fetches_fresh_data_with_units():
    requests = []
    with httpx.Client(transport=httpx.MockTransport(weather_handler(requests))) as client:
        tools = ToolRegistry(Settings(weather_enabled=True, api_key="llm-secret"), client)
        first = json.loads(tools.execute("get_local_weather", '{"days":2}'))
        tools.execute("get_local_weather", '{"days":2}')
    assert len(requests) == 3  # Resolve once; refresh forecast on each call.
    assert requests[0].url.params["name"] == "Stroud"
    assert requests[0].url.params["countryCode"] == "GB"
    assert requests[1].url.params["timezone"] == "Europe/London"
    assert requests[1].url.params["wind_speed_unit"] == "mph"
    assert first["location"]["admin2"] == "Gloucestershire"
    assert first["current"]["conditions"] == "overcast"
    assert first["daily"][1]["conditions"] == "slight rain"
    assert first["daily"][1]["precipitation_probability_max"] == 70
    assert first["source"] == "Open-Meteo"


@pytest.mark.parametrize("failure", ["timeout", "http", "shape", "place", "nan"])
def test_weather_errors_are_data_for_model_without_fake_forecasts_or_private_details(failure):
    with httpx.Client(
        transport=httpx.MockTransport(weather_handler([], failure=failure))
    ) as client:
        result = ToolRegistry(Settings(weather_enabled=True), client).execute(
            "get_local_weather", '{"days":2}'
        )
    assert "error" in json.loads(result)
    assert "private error" not in result


@pytest.mark.parametrize(
    "arguments",
    ['{"days":8}', '{"days":0}', '{"days":true}', '{"days":"2"}', '{"url":"http://elsewhere"}'],
)
def test_weather_argument_validation_prevents_requests(arguments):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("Unexpected request"))
    ) as c:
        result = ToolRegistry(Settings(weather_enabled=True), c).execute(
            "get_local_weather", arguments
        )
    assert "error" in json.loads(result)


def test_weather_is_not_advertised_or_executed_without_online_opt_in():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("Unexpected request"))
    ) as c:
        tools = ToolRegistry(Settings(), c)
        assert "get_local_weather" not in tools.skills
        assert "error" in json.loads(tools.execute("get_local_weather", "{}"))


def test_multiple_requested_skills_are_executed_and_correlated_before_final_answer():
    model_requests, weather_requests = [], []
    responses = iter(
        [
            calling(tool_call(), tool_call("get_local_weather", '{"days":2}', "weather")),
            response("Here is the time and the forecast from Open-Meteo."),
        ]
    )
    weather = weather_handler(weather_requests)

    def handle(request):
        if request.method == "GET":
            return weather(request)
        assert request.headers["authorization"] == "Bearer model-only-key"
        model_requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(weather_enabled=True, api_key="model-only-key"), client)
        assert "forecast" in chat.get_response("What time is it, and will it rain tomorrow?")
    results = model_requests[1]["messages"][-2:]
    assert [message["tool_call_id"] for message in results] == ["clock", "weather"]
    assert json.loads(results[0]["content"])["timezone"] == "Europe/London"
    assert len(json.loads(results[1]["content"])["daily"]) == 2
    assert len(model_requests) == 2 and len(weather_requests) == 2


def test_raw_gemma_tool_syntax_reports_template_problem_instead_of_becoming_speech():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=response("<|tool_call>call:get_local_time{}<tool_call|>")
            )
        )
    ) as client:
        chat = ChatEngine(Settings(), client)
        with pytest.raises(ChatError, match="Use Jinja"):
            chat.get_response("What time is it?")
        assert not chat.history


def test_synthesis_failure_rolls_back_tool_exchange_and_clear_context_removes_it():
    from unittest.mock import Mock

    import numpy as np

    from iris_droid.protocol import write_wav
    from iris_droid.server import Brain

    def handle(request):
        messages = json.loads(request.content)["messages"]
        body = response("It is Sunday.") if messages[-1]["role"] == "tool" else calling(tool_call())
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        tts = Mock()
        brain = Brain(Settings(), client, Mock(recognize=Mock(return_value="What day is it?")), tts)
        recording = write_wav(b"\x01\x00" * 1600, 16000)
        tts.synthesize.side_effect = OSError("speaker service unavailable")
        with pytest.raises(OSError):
            brain.turn("pi", recording)
        assert brain.sessions["pi"].history == []
        tts.synthesize.side_effect = None
        tts.synthesize.return_value = (np.ones(100, dtype=np.float32), 24000)
        brain.turn("pi", recording)
        assert len(brain.sessions["pi"].history) == 4
        brain.turn("pi", b"clear context", text_only=True)
        assert "pi" not in brain.sessions
