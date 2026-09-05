import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from coding_agent.config import ModelConfig
from coding_agent.model import ModelError, OpenAIModel, _reply, _result_url


def body(message=None):
    return {
        "choices": [
            {
                "message": message or {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.parametrize("arguments", ['{"x":1,"x":2}', "[]", '{"x":NaN}', "bad"])
def test_malformed_tool_arguments_fail_explicitly(arguments):
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "1",
                "type": "function",
                "function": {"name": "edit_file", "arguments": arguments},
            }
        ],
    }
    with pytest.raises(ModelError, match="strict JSON object"):
        _reply(httpx.Response(200, json=body(message)))


def test_reasoning_message_preserved_and_missing_usage_rejected():
    message = {"role": "assistant", "content": "answer", "reasoning_content": "reasoning"}
    assert _reply(httpx.Response(200, json=body(message))).message == message
    value = body()
    del value["usage"]
    with pytest.raises(ModelError, match="Missing model usage"):
        _reply(httpx.Response(200, json=value))


def test_real_http_client_auth_usage_and_deadline(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    self.path,
                    self.headers["Authorization"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            payload = json.dumps(body()).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AGENT_API_KEY", "dummy-private-key")
    model = OpenAIModel(
        ModelConfig(name="org/model", base_url=f"http://127.0.0.1:{server.server_port}/v1")
    )
    try:
        response = model.complete([{"role": "user", "content": "hello"}], [], 100)
        assert response.usage.total == 15
        assert requests[0][0] == "/v1/chat/completions"
        assert requests[0][1] == "Bearer dummy-private-key"
        assert requests[0][2]["model"] == "org/model"
        model.set_deadline(0)
        with pytest.raises(ModelError, match="deadline"):
            model.complete([], [], 100)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def redirect_server(monkeypatch):
    requests = []
    responses = [(303, {"Location": "?result=private-result-token"}, None), (200, {}, body())]

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            content = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append((self.command, self.path, self.headers["Authorization"], content))
            status, headers, value = responses.pop(0)
            payload = json.dumps(value).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_POST = respond
        do_GET = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AGENT_API_KEY", "dummy-private-key")
    model = OpenAIModel(
        ModelConfig(name="org/model", base_url=f"http://127.0.0.1:{server.server_port}/v1")
    )
    try:
        yield model, requests, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_303_polls_existing_result_with_usage(redirect_server):
    model, requests, _ = redirect_server
    events = []
    model.set_observer(events.append)
    reply = model.complete([{"role": "user", "content": "hello"}], [], 100)
    assert reply.usage.total == 15
    assert reply.message["content"] == "hello"
    assert [request[0] for request in requests] == ["POST", "GET"]
    assert requests[1][1] == "/v1/chat/completions?result=private-result-token"
    assert requests[1][2] == "Bearer dummy-private-key"
    assert requests[1][3] == b""
    assert "private-result-token" not in repr(events)
    assert "dummy-private-key" not in repr(events)


@pytest.mark.parametrize(
    "location",
    [
        None,
        "",
        " ",
        "https://other.example/?secret=token",
        "//other.example/result",
        "http://127.0.0.1:1/result",
        "https://127.0.0.1/result",
        "http://user:password@127.0.0.1/result",
        "?result=token#fragment",
        "?result=token#",
        "http://[broken",
        "ftp://127.0.0.1/result",
    ],
)
def test_real_http_rejects_unsafe_redirect_without_second_request(redirect_server, location):
    model, requests, responses = redirect_server
    responses[0] = (303, {} if location is None else {"Location": location}, None)
    events = []
    model.set_observer(events.append)
    with pytest.raises(ModelError, match="Invalid model result redirect") as error:
        model.complete([], [], 100)
    assert len(requests) == 1
    for secret in ("token", "password", "dummy-private-key"):
        assert secret not in str(error.value) + repr(events)


def test_redirect_origin_normalization_and_relative_resolution():
    original = "https://model.example/v1/chat/completions"
    assert _result_url(original, "https://MODEL.example:443/result?t=1", original) == (
        "https://model.example/result?t=1"
    )
    assert _result_url("https://model.example/result?t=1", "?t=2", original) == (
        "https://model.example/result?t=2"
    )
    with pytest.raises(ModelError, match="Invalid model result redirect"):
        _result_url(original, "https://user:password@model.example/result", original)


def test_real_http_redirect_loop_is_bounded(redirect_server):
    model, requests, responses = redirect_server
    responses[:] = [(303, {"Location": "?result=private-result-token"}, None)] * 21
    with pytest.raises(ModelError, match="redirect limit"):
        model.complete([], [], 100)
    assert [request[0] for request in requests] == ["POST"] + ["GET"] * 20


@pytest.mark.parametrize("redirect", [False, True])
def test_real_http_429_retries_current_request_only(redirect_server, monkeypatch, redirect):
    model, requests, responses = redirect_server
    monkeypatch.setattr("coding_agent.model.time.sleep", lambda _: None)
    responses[:] = responses[:1] if redirect else []
    responses.extend([(429, {"Retry-After": "0"}, None)] * 2 + [(200, {}, body())])
    assert model.complete([], [], 100).usage.total == 15
    assert [request[0] for request in requests] == (
        ["POST", "GET", "GET", "GET"] if redirect else ["POST"] * 3
    )


def test_real_http_polling_obeys_deadline(redirect_server):
    model, requests, _ = redirect_server

    def expire(event):
        if event["stage"] == "poll":
            model.set_deadline(time.monotonic() - 1)

    model.set_observer(expire)
    with pytest.raises(ModelError, match="deadline exceeded"):
        model.complete([], [], 100)
    assert len(requests) == 1


def test_real_http_polling_requires_usage(redirect_server):
    model, requests, responses = redirect_server
    result = body()
    del result["usage"]
    responses[1] = (200, {}, result)
    with pytest.raises(ModelError, match="Missing model usage"):
        model.complete([], [], 100)
    assert [request[0] for request in requests] == ["POST", "GET"]


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
def test_poll_transport_failure_is_sanitized_and_never_reposts(monkeypatch, failure):
    requests = []
    timeouts = []
    events = []

    def send(request):
        requests.append(request)
        timeouts.append(request.extensions["timeout"]["read"])
        if request.method == "POST":
            return httpx.Response(303, headers={"Location": "?result=private-result-token"})
        raise failure("private-result-token dummy-private-key", request=request)

    client_class = httpx.Client
    monkeypatch.setattr(
        "coding_agent.model.httpx.Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(send), **kwargs),
    )
    monkeypatch.setenv("AGENT_API_KEY", "dummy-private-key")
    model = OpenAIModel(ModelConfig(name="org/model", timeout=10))
    model.set_deadline(time.monotonic() + 5)
    model.set_observer(events.append)
    with pytest.raises(ModelError, match="No automatic retry") as error:
        model.complete([], [], 100)
    assert [request.method for request in requests] == ["POST", "GET"]
    assert 0 < timeouts[1] <= timeouts[0] <= 5
    assert "private-result-token" not in str(error.value) + repr(events)
    assert "dummy-private-key" not in str(error.value) + repr(events)
