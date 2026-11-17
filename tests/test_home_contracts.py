"""Real home HTTP validation and outcomes; no physical devices or production keys."""

import http.client
import json
import ssl
from pathlib import Path
from threading import Thread
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from iris_home.devices import Device
from iris_home.protocol import Request, SwitchRequest
from iris_home.schedule import Scheduler
from iris_home.service import Config, HomeAPI, HomeServer
from iris_settings import Settings
from iris_tools.home import HomeClient
from tests.support import FakeSwitch

TOKEN = "f" * 64


@pytest.fixture
def home(tmp_path):
    switch = FakeSwitch()
    devices = {"test lamp": Device(switch, {})}
    with Scheduler(tmp_path / "home.sqlite3", ZoneInfo("UTC"), devices) as scheduler:
        with HomeServer(("127.0.0.1", 0), HomeAPI(devices, scheduler), TOKEN, None) as server:
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()

            def request(route, body, **headers):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request(
                        "POST",
                        route,
                        body,
                        {"Authorization": f"Bearer {TOKEN}", **headers},
                    )
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                yield switch, request
            finally:
                server.shutdown()
                thread.join(2)
                assert not thread.is_alive()


@pytest.mark.parametrize(
    "route,body,headers,status",
    [
        ("/v1/switch", b'{"device":"test lamp","state":"on"}', {"Authorization": "wrong"}, 401),
        ("/v1/devices", b"{}", {}, 404),
        ("/v1/list", b"{", {}, 400),
        ("/v1/list", b"{}", {"Content-Length": "bad"}, 400),
        ("/v1/list", b"{}", {"Content-Length": "16385"}, 400),
        ("/v1/list", b"{}", {"Transfer-Encoding": "chunked"}, 400),
        ("/v1/list", b'{"extra":1}', {}, 400),
        ("/v1/switch", b'{"device":"test lamp","state":true}', {}, 400),
        ("/v1/status", b'{"device":"unknown"}', {}, 404),
        ("/v1/meter", b'{"device":"test lamp","metric":"power"}', {}, 503),
    ],
)
def test_rejected_home_request_does_not_operate_device(home, route, body, headers, status):
    switch, request = home
    actual, payload = request(route, body, **headers)
    assert actual == status and "error" in payload
    assert switch.commands == []


def test_home_readback_conflict_and_failure_are_distinct(home):
    switch, request = home
    body = b'{"device":"test lamp","state":"on"}'
    assert request("/v1/switch", body) == (
        200,
        {"device": "test lamp", "accepted": True, "requested_state": "on"},
    )
    assert request("/v1/status", b'{"device":"test lamp"}')[1]["state"] == "on"
    schedule = b'{"device":"test lamp","revision":0,"rules":[]}'
    assert request("/v1/save-schedule", schedule)[0] == 200
    assert request("/v1/save-schedule", schedule)[0] == 409
    assert request("/v1/schedule", b'{"device":"test lamp"}')[1]["revision"] == 1
    switch.offline = True
    assert request("/v1/switch", b'{"device":"test lamp","state":"off"}')[0] == 503
    assert switch.commands == ["on"]


@pytest.mark.parametrize(
    "change",
    [
        {"listen": "192.0.2.1"},
        {"tls_cert": "certificate.crt"},
        {"devices": {"lamp": {"node": 91}, " LAMP ": {"node": 92}}},
        {"devices": {"lamp": {"node": 91}, "other": {"node": 91}}},
        {"devices": {"lamp": {"node": "91"}}},
        {"devices": {"lamp": {"node": 91, "switch_endpoint": 65535}}},
    ],
)
def test_home_configuration_rejects_ambiguous_or_unprotected_devices(tmp_path, change):
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "database": tmp_path / "home.db",
                "matter_storage": tmp_path,
                "devices": {"lamp": {"node": 91}},
                **change,
            }
        )


def test_home_tls_checks_trust_hostname_and_key_then_recovers(tmp_path):
    fixtures = Path(__file__).parent / "fixtures" / "tls"
    cert = fixtures / "home-cert.pem"
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, fixtures / "home-key.pem")
    switch = FakeSwitch()
    devices = {"test lamp": Device(switch, {})}
    with Scheduler(tmp_path / "home.sqlite3", ZoneInfo("UTC"), devices) as scheduler:
        with HomeServer(("127.0.0.1", 0), HomeAPI(devices, scheduler), TOKEN, tls) as server:
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:

                def settings(host="127.0.0.1", ca=cert, token=TOKEN):
                    return Settings(
                        home_service_url=f"https://{host}:{server.server_port}",
                        home_service_ca=ca,
                        home_service_token=token,
                    )

                command = SwitchRequest(device="test lamp", state="on")
                for invalid in (
                    settings(ca=None),
                    settings(host="localhost"),
                    settings(token="x" * 64),
                ):
                    assert "error" in HomeClient(invalid).execute("switch", command)
                    assert not switch.commands
                client = HomeClient(settings(), persistent=True)
                try:
                    listed = client.execute("list", Request())
                    assert listed["devices"] == [{"device": "test lamp", "meters": []}]
                    assert client.execute("switch", command)["accepted"] is True
                    assert switch.commands == ["on"]
                finally:
                    client.close()
            finally:
                server.shutdown()
                thread.join(2)
                assert not thread.is_alive()
