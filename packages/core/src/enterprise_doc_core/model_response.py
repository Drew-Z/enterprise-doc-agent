"""Bounded OpenAI response framing; business validation belongs to the gateways."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

MAX_MODEL_STREAM_BYTES = 8 * 1024**2
_SSE_LINE_END = re.compile(rb"\r\n|\r|\n")


def retryable_provider_error(envelope: Any) -> bool:
    """Classify explicit provider codes, never free-form messages or raw payloads."""
    codes = {
        "upstream_error",
        "server_error",
        "internal_error",
        "do_request_failed",
        "rate_limit_exceeded",
        "rate_limit_error",
        "overloaded_error",
        "timeout",
        "gateway_timeout",
        "service_unavailable",
    }
    return isinstance(envelope, dict) and any(
        isinstance(envelope.get(key), str) and envelope[key] in codes for key in ("type", "code")
    )


class ModelResponseError(ValueError):
    def __init__(self, code: str = "invalid_model_stream", *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class OpenAIResponseReader:
    """One reader per request. Accounting survives cancellation without saving content."""

    def __init__(
        self, *, streaming: bool, max_bytes: int, max_stream_bytes: int | None = None
    ) -> None:
        if max_bytes <= 0 or (max_stream_bytes is not None and max_stream_bytes <= 0):
            raise ValueError("positive response limit required")
        self.streaming = streaming
        self.max_bytes = max_bytes
        self.max_stream_bytes = max_stream_bytes if max_stream_bytes is not None else max_bytes
        self.accounting_response: httpx.Response | None = None
        self._metadata: dict[str, Any] = {}
        self._content: list[str] = []
        self._content_bytes = 0
        self._finish: str | None = None
        self._done = False
        self._response: httpx.Response | None = None

    def _response_for(self, payload: Any) -> httpx.Response:
        assert self._response is not None
        headers = {
            key: value
            for key, value in self._response.headers.items()
            if key.lower()
            not in {"content-encoding", "content-length", "content-type", "transfer-encoding"}
        }
        try:
            return httpx.Response(
                self._response.status_code,
                json=payload,
                headers=headers,
                request=self._response.request,
            )
        except UnicodeError:
            raise ModelResponseError() from None

    def _event(self, data: str) -> None:
        if len(data.encode("utf-8")) > self.max_bytes:
            raise ModelResponseError("model_response_too_large")
        if data == "[DONE]":
            if self._finish != "stop":
                raise ModelResponseError("incomplete_model_stream")
            self._done = True
            return
        try:
            event = json.loads(data)
        except (ValueError, UnicodeError):
            raise ModelResponseError() from None
        if not isinstance(event, dict):
            raise ModelResponseError()
        for key in ("id", "model", "system_fingerprint"):
            value = event.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or (
                key in self._metadata and self._metadata[key] != value
            ):
                raise ModelResponseError()
            self._metadata[key] = value
        usage = event.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ModelResponseError()
            # Keep token counters only; never retain provider text in accounting.
            counts = {
                key: value
                for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(value) is int
                and value >= 0
            }
            if "usage" in self._metadata and self._metadata["usage"] != counts:
                raise ModelResponseError()
            self._metadata["usage"] = counts
        self.accounting_response = self._response_for(self._metadata)
        if event.get("error") is not None:
            raise ModelResponseError(
                "model_stream_upstream_error",
                retryable=retryable_provider_error(event["error"]),
            )
        choices = event.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ModelResponseError()
        for choice in choices:
            if (
                not isinstance(choice, dict)
                or type(choice.get("index")) is not int
                or choice["index"] != 0
            ):
                raise ModelResponseError()
            delta = choice.get("delta", {})
            if not isinstance(delta, dict) or delta.get("tool_calls") or delta.get("function_call"):
                raise ModelResponseError()
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str) or (self._finish is not None and content):
                    raise ModelResponseError()
                try:
                    self._content_bytes += len(content.encode("utf-8"))
                except UnicodeError:
                    raise ModelResponseError() from None
                if self._content_bytes > self.max_bytes:
                    raise ModelResponseError("model_response_too_large")
                if content:
                    self._content.append(content)
            finish = choice.get("finish_reason")
            if finish is not None:
                if self._finish is not None or finish != "stop":
                    raise ModelResponseError("incomplete_model_stream")
                self._finish = finish

    def _completed_response(self) -> httpx.Response:
        response = self._response_for(
            {
                **self._metadata,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "".join(self._content)},
                        "finish_reason": self._finish,
                    }
                ],
            }
        )
        if len(response.content) > self.max_bytes:
            raise ModelResponseError("model_response_too_large")
        return response

    async def read(self, response: httpx.Response) -> httpx.Response:
        self._response = response
        self.accounting_response = self._response_for({})
        is_stream = self.streaming and response.is_success
        wire_limit = self.max_stream_bytes if is_stream else self.max_bytes
        if (
            is_stream
            and response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "text/event-stream"
        ):
            raise ModelResponseError()
        length = response.headers.get("content-length")
        if length is not None and length.isdecimal() and int(length) > wire_limit:
            raise ModelResponseError("model_response_too_large")
        total = 0
        buffer = b""
        data: list[str] = []
        data_bytes = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > wire_limit:
                raise ModelResponseError("model_response_too_large")
            buffer += chunk
            if not is_stream:
                continue
            offset = 0
            while match := _SSE_LINE_END.search(buffer, offset):
                if match.group() == b"\r" and match.end() == len(buffer):
                    break  # CRLF can straddle two transport chunks.
                if match.start() - offset > self.max_bytes:
                    raise ModelResponseError("model_response_too_large")
                try:
                    line = buffer[offset : match.start()].decode("utf-8")
                except UnicodeError:
                    raise ModelResponseError() from None
                offset = match.end()
                if not line and data:
                    self._event("\n".join(data))
                    data.clear()
                    data_bytes = 0
                    if self._done:
                        return self._completed_response()
                elif line.startswith("data:"):
                    value = line[5:].removeprefix(" ")
                    data_bytes += len(value.encode("utf-8")) + bool(data)
                    if data_bytes > self.max_bytes:
                        raise ModelResponseError("model_response_too_large")
                    data.append(value)
            buffer = buffer[offset:]
            if len(buffer) > self.max_bytes:
                raise ModelResponseError("model_response_too_large")
        if is_stream:
            if buffer == b"\r" and data:
                self._event("\n".join(data))
                if self._done:
                    return self._completed_response()
            raise ModelResponseError("incomplete_model_stream")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        }
        result = httpx.Response(
            response.status_code, content=buffer, headers=headers, request=response.request
        )
        self.accounting_response = result
        return result
