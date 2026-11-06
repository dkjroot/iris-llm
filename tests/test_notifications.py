"""Notification delivery contracts with fake HTTP; no real pushes or credentials."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from iris_chat_engines.compatible import ChatEngine
from iris_settings import Settings
from iris_tools import ToolRegistry
from iris_tools.notifications import NotificationArguments, NotificationSender
from iris_tools.notify import main
from tests.test_tools import calling, response, tool_call


def configured(**kwargs):
    return Settings(
        notifications_enabled=True,
        pushover_api_token="private-app-token",
        pushover_user_key="private-user-key",
        **kwargs,
    )


def test_push_uses_fixed_recipient_and_form_body_and_only_reports_acceptance():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": 1, "request": "provider-request"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        registry = ToolRegistry(
            configured(pushover_device="iphone", api_key="model-secret"), client
        )
        result = json.loads(registry.execute("send_notification", '{"message":"Hello 👋 & tea"}'))
    assert result["status"] == "accepted"
    assert "does not confirm" in result["detail"]
    request = requests[0]
    assert str(request.url) == "https://api.pushover.net/1/messages.json"
    assert request.method == "POST" and "authorization" not in request.headers
    assert parse_qs(request.content.decode()) == {
        "token": ["private-app-token"],
        "user": ["private-user-key"],
        "message": ["Hello 👋 & tea"],
        "title": ["Iris"],
        "priority": ["0"],
        "device": ["iphone"],
    }
    assert "private-" not in json.dumps(result)
    assert "private-" not in json.dumps(registry.definitions())


@pytest.mark.parametrize("settings", [Settings(), Settings(notifications_enabled=True)])
def test_disabled_or_unconfigured_sender_never_contacts_network(settings):
    def unexpected(request):
        pytest.fail("Must not send")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        assert "error" in NotificationSender(settings, client).send(
            NotificationArguments(message="Hi")
        )


@pytest.mark.parametrize("tools,notifications", [(False, True), (True, False), (False, False)])
def test_skill_requires_both_switches(tools, notifications):
    with httpx.Client() as client:
        registry = ToolRegistry(
            Settings(tools_enabled=tools, notifications_enabled=notifications), client
        )
        assert "send_notification" not in registry.skills


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"message": " "},
        {"message": "x" * 1025},
        {"message": 4},
        {"message": "Hi", "title": "x" * 251},
        {"message": "Hi", "title": " "},
        {"message": "Hi", "user": "someone-else"},
        {"message": "Hi", "priority": 2},
    ],
)
def test_invalid_arguments_do_not_send(arguments):
    def unexpected(request):
        pytest.fail("Must not send")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        result = ToolRegistry(configured(), client).execute(
            "send_notification", json.dumps(arguments)
        )
    assert "error" in json.loads(result)


@pytest.mark.parametrize(
    "failure",
    ["reject", "quota", "server", "status", "json", "list", "bool", "redirect", "timeout"],
)
def test_failure_is_sanitised_and_not_retried(failure):
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-app-token private-user-key", request=request)
        if failure == "json":
            return httpx.Response(200, text="private-app-token")
        status = {"reject": 400, "quota": 429, "server": 503, "redirect": 302}.get(failure, 200)
        body = (
            []
            if failure == "list"
            else {
                "status": True if failure == "bool" else 0,
                "errors": ["private-app-token private-user-key"],
            }
        )
        return httpx.Response(status, json=body, headers={"location": "https://example.com"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        registry = ToolRegistry(configured(), client)
        first = registry.execute("send_notification", '{"message":"Hi"}')
        second = registry.execute("send_notification", '{"message":"Hi"}')
    assert "error" in json.loads(first) and "retry" in first
    assert "private-" not in first
    assert "cooldown" in second
    assert len(requests) == 1


def test_sender_cooldown_expires_and_default_device_is_omitted(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("iris_tools.notifications.time.monotonic", lambda: now[0])
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": 1})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        sender = NotificationSender(configured(), client)
        args = NotificationArguments(message="Hi")
        assert sender.send(args)["status"] == "accepted"
        assert "error" in sender.send(args)
        now[0] += 5
        assert sender.send(args)["status"] == "accepted"
    assert len(requests) == 2
    assert "device" not in parse_qs(requests[0].content.decode())


def test_model_tool_round_trip_keeps_credentials_out_of_chat():
    model_requests = []
    pushes = []
    replies = iter(
        [
            calling(tool_call("send_notification", '{"message":"Hello from Iris"}')),
            response("Pushover accepted your notification."),
        ]
    )

    def handle(request):
        if request.url.host == "api.pushover.net":
            pushes.append(request)
            return httpx.Response(200, json={"status": 1})
        model_requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        chat = ChatEngine(configured(), client)
        assert "accepted" in chat.get_response("Send a test notification to my phone")
    assert len(pushes) == 1 and len(model_requests) == 2
    assert "private-" not in json.dumps(model_requests)


def test_macbook_command_prompts_privately_and_sends_once(monkeypatch, capsys):
    prompts = []
    keys = iter(["private-app-token", "private-user-key"])

    def prompt(label):
        prompts.append(label)
        return next(keys)

    real_client = httpx.Client
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": 1})

    monkeypatch.setattr("sys.argv", ["notify", "--device", "iphone"])
    monkeypatch.setattr("iris_tools.notify.getpass.getpass", prompt)
    monkeypatch.setattr(
        "iris_tools.notify.httpx.Client", lambda: real_client(transport=httpx.MockTransport(handle))
    )
    assert main() == 0
    assert len(prompts) == 2 and len(requests) == 1
    assert "accepted" in capsys.readouterr().out
