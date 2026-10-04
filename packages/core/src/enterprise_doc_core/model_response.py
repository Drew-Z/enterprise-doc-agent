"""Bounded OpenAI response framing; business validation belongs to the gateways."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx


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

    def __init__(self, *, streaming: bool, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("positive response limit required")
        self.streaming = streaming
        self.max_bytes = max_bytes
        self.accounting_response: httpx.Response | None = None
        self._metadata: dict[str, Any] = {}
        self._content: list[str] = []
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
        return httpx.Response(
            self._response.status_code,
            json=payload,
            headers=headers,
            request=self._response.request,
        )

    def _event(self, data: str) -> None:
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
                self._content.append(content)
            finish = choice.get("finish_reason")
            if finish is not None:
                if self._finish is not None or finish != "stop":
                    raise ModelResponseError("incomplete_model_stream")
                self._finish = finish

    def _completed_response(self) -> httpx.Response:
        return self._response_for(
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

    async def read(self, response: httpx.Response) -> httpx.Response:
        self._response = response
        self.accounting_response = self._response_for({})
        is_stream = self.streaming and response.is_success
        if (
            is_stream
            and response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "text/event-stream"
        ):
            raise ModelResponseError()
        length = response.headers.get("content-length")
        if length is not None and length.isdecimal() and int(length) > self.max_bytes:
            raise ModelResponseError("model_response_too_large")
        total = 0
        buffer = b""
        data: list[str] = []
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise ModelResponseError("model_response_too_large")
            buffer += chunk
            if not is_stream:
                continue
            while match := re.search(rb"\r\n|\r|\n", buffer):
                if match.group() == b"\r" and match.end() == len(buffer):
                    break  # CRLF can straddle two transport chunks.
                try:
                    line = buffer[: match.start()].decode("utf-8")
                except UnicodeError:
                    raise ModelResponseError() from None
                buffer = buffer[match.end() :]
                if not line and data:
                    self._event("\n".join(data))
                    data.clear()
                    if self._done:
                        return self._completed_response()
                elif line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
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
