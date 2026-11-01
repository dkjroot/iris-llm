"""Agentic skills with fixed clocks and fake HTTP; no models or audio devices."""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest

from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_settings import Settings
from iris_tools import ToolRegistry


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


@pytest.mark.parametrize("enabled", [False, True])
def test_ordinary_conversation_finishes_without_tools_and_disabling_restores_plain_api(enabled):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response("Tell me more."))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(Settings(tools_enabled=enabled), client)
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
