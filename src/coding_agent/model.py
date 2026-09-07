"""Synchronous OpenAI-compatible model calls with explicit, trustworthy usage."""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx

from coding_agent.types import Message, ModelReply, Schema, ToolCall, Usage

if TYPE_CHECKING:
    from coding_agent.config import ModelConfig


class ModelError(Exception):
    """A sanitized model transport or response validation failure."""


class ModelDeadlineExceeded(ModelError):
    """The agent's global wall-clock deadline expired during a model call."""


_RETRY_STATUSES = {429}
_TRANSIENT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
_MAX_RETRIES = 2
_MAX_RETRY_DELAY = 5.0
_MAX_RESULT_REDIRECTS = 20


def _result_url(current: str, location: str | None, original: str) -> str:
    if not location or not location.strip() or "#" in location:
        raise ModelError("Invalid model result redirect")
    try:
        target = httpx.URL(current).join(location)
        origin = httpx.URL(original)
        same_origin = (target.scheme, target.host, target.port) == (
            origin.scheme,
            origin.host,
            origin.port,
        )
        if not same_origin or target.userinfo or target.fragment:
            raise ValueError("Unsafe redirect")
    except (httpx.InvalidURL, ValueError):
        raise ModelError("Invalid model result redirect") from None
    return str(target)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=_invalid_constant,
            parse_float=_finite_float,
        )
    except (ValueError, RecursionError):
        raise ModelError(f"{label} must be a strict JSON object") from None
    if not isinstance(value, dict):
        raise ModelError(f"{label} must be a strict JSON object")
    return value


def _token_count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ModelError(f"Invalid or missing usage.{label}")
    return value


def _usage(value: Any) -> Usage:
    if not isinstance(value, dict):
        raise ModelError("Missing model usage; cannot account for token budget")
    input_tokens = _token_count(value.get("prompt_tokens"), "prompt_tokens")
    output_tokens = _token_count(value.get("completion_tokens"), "completion_tokens")
    details = value.get("prompt_tokens_details")
    if details is not None and not isinstance(details, dict):
        raise ModelError("Invalid usage.prompt_tokens_details")
    cached = _token_count((details or {}).get("cached_tokens", 0), "cached_tokens")
    if cached > input_tokens:
        raise ModelError("Cached token usage exceeds input token usage")
    if "total_tokens" in value:
        total = _token_count(value["total_tokens"], "total_tokens")
        if total != input_tokens + output_tokens:
            raise ModelError("Inconsistent model token usage")
    return Usage(input_tokens, output_tokens, cached)


def _tool_call(value: Any) -> ToolCall:
    if not isinstance(value, dict) or value.get("type") != "function":
        raise ModelError("Invalid tool call: expected function type")
    call_id = value.get("id")
    function = value.get("function")
    if not isinstance(call_id, str) or not call_id or not isinstance(function, dict):
        raise ModelError("Invalid tool call identity")
    name, arguments = function.get("name"), function.get("arguments")
    if not isinstance(name, str) or not name or not isinstance(arguments, str):
        raise ModelError("Invalid tool call function or arguments")
    return ToolCall(call_id, name, _json_object(arguments, "Tool call arguments"))


def _calls(message: Message) -> tuple[ToolCall, ...]:
    values = message.get("tool_calls", [])
    if not isinstance(values, list):
        raise ModelError("Assistant tool_calls must be a list")
    calls = tuple(_tool_call(value) for value in values)
    if len({call.id for call in calls}) != len(calls):
        raise ModelError("Duplicate tool call IDs")
    if message.get("function_call") is not None:
        raise ModelError("Legacy function_call responses are unsupported")
    return calls


def _choice(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ModelError("Model response must contain exactly one choice")
    return dict(choices[0])


def _reply(response: httpx.Response) -> ModelReply:
    body = _json_object(response.text, "Model response")
    choice = _choice(body)
    message, reason = choice.get("message"), choice.get("finish_reason")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ModelError("Model response is missing an assistant message")
    if not isinstance(reason, str) or not reason:
        raise ModelError("Model response is missing finish_reason")
    content = message.get("content")
    if content is not None and not isinstance(content, (str, list)):
        raise ModelError("Invalid assistant content")
    calls = _calls(message)
    if reason == "tool_calls" and not calls:
        raise ModelError("tool_calls finish_reason without tool calls")
    return ModelReply(message, calls, _usage(body.get("usage")), reason)


def _retry_delay(header: str | None, attempt: int) -> float:
    fallback = float(0.25 * 2**attempt)
    if header is None:
        return fallback
    try:
        delay = float(header)
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
            delay = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    return min(_MAX_RETRY_DELAY, max(0.0, delay)) if math.isfinite(delay) else fallback


class OpenAIModel:
    """Use only the configured endpoint, never log payloads, and never execute tools."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        try:
            url = httpx.URL(config.base_url)
        except (httpx.InvalidURL, ValueError):
            raise ModelError("Invalid model base URL") from None
        if url.scheme not in {"http", "https"} or not url.host:
            raise ModelError("Model base URL must be an absolute HTTP(S) URL")
        if url.userinfo or url.query or url.fragment:
            raise ModelError("Model base URL must not contain credentials, query, or fragment")
        if not math.isfinite(config.timeout) or config.timeout <= 0:
            raise ModelError("Model timeout must be positive and finite")
        self._url = str(url).rstrip("/") + "/chat/completions"
        self._deadline = float("inf")
        self._observer: Callable[[dict[str, Any]], None] | None = None

    def set_observer(self, observer: Callable[[dict[str, Any]], None]) -> None:
        self._observer = observer

    def _notify(self, **event: Any) -> None:
        if self._observer is not None:
            self._observer(event)

    def set_deadline(self, deadline: float) -> None:
        self._deadline = deadline

    def _timeout(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ModelDeadlineExceeded("Model request deadline exceeded")
        return min(self.config.timeout, remaining)

    def _pause(self, delay: float) -> None:
        time.sleep(min(delay, self._timeout()))

    def complete(self, messages: list[Message], tools: list[Schema], max_tokens: int) -> ModelReply:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ModelError("max_tokens must be a positive integer")
        if max_tokens > self.config.max_output_tokens:
            raise ModelError("max_tokens exceeds configured output limit")
        key = os.environ.get(self.config.api_key_env)
        if not key or not key.strip():
            raise ModelError("Model API key environment variable is missing or empty")
        payload: dict[str, Any] = {
            "model": self.config.name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "seed": self.config.seed,
        }
        if tools:
            payload["tools"] = tools
        with httpx.Client(timeout=self._timeout(), trust_env=False) as client:
            response = self._request(client, payload, key)
        return _reply(response)

    def _request(self, client: httpx.Client, payload: dict[str, Any], key: str) -> httpx.Response:
        attempt = redirects = 0
        url = self._url
        request_payload: dict[str, Any] | None = payload
        while True:
            response = self._send(client, url, request_payload, key, attempt)
            if response.status_code == 303:
                if redirects >= _MAX_RESULT_REDIRECTS:
                    raise ModelError("Model result redirect limit exceeded")
                url = _result_url(url, response.headers.get("Location"), self._url)
                request_payload = None
                redirects += 1
                self._notify(stage="poll", redirect=redirects)
                continue
            if response.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                delay = _retry_delay(response.headers.get("Retry-After"), attempt)
                self._notify(stage="retry", delay_seconds=delay)
                self._pause(delay)
                attempt += 1
                continue
            if response.status_code != 200:
                raise ModelError(f"Model endpoint returned HTTP {response.status_code}")
            return response

    def _send(
        self,
        client: httpx.Client,
        url: str,
        payload: dict[str, Any] | None,
        key: str,
        attempt: int,
    ) -> httpx.Response:
        self._notify(stage="request", attempt=attempt + 1)
        try:
            response = client.request(
                "POST" if payload is not None else "GET",
                url,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
                timeout=self._timeout(),
                follow_redirects=False,
            )
        except _TRANSIENT_ERRORS:
            self._notify(stage="transport_error", attempt=attempt + 1)
            if time.monotonic() >= self._deadline:
                raise ModelDeadlineExceeded("Model request deadline exceeded") from None
            raise ModelError(
                "Model transport failed; request usage is unknown. No automatic retry."
            ) from None
        except (httpx.HTTPError, ValueError, TypeError):
            self._notify(stage="request_error", attempt=attempt + 1)
            raise ModelError("Model request failed") from None
        self._notify(stage="response", attempt=attempt + 1, http_status=response.status_code)
        return response


class ScriptedModel:
    """Deterministic model for tests; exhausting the script is an explicit error."""

    def __init__(self, replies: list[ModelReply]) -> None:
        self._replies = deque(replies)

    def complete(self, messages: list[Message], tools: list[Schema], max_tokens: int) -> ModelReply:
        if not self._replies:
            raise ModelError("Scripted model has no replies remaining")
        return self._replies.popleft()
