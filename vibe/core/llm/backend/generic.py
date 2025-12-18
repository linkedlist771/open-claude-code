from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import json
import os
import types
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol, TypeVar

import httpx

from vibe.core.llm.exceptions import BackendErrorBuilder
from vibe.core.types import (
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
    ToolCall,
)
from vibe.core.utils import async_generator_retry, async_retry

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, ProviderConfig


class PreparedRequest(NamedTuple):
    endpoint: str
    headers: dict[str, str]
    body: bytes


class APIAdapter(Protocol):
    endpoint: ClassVar[str]

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
    ) -> PreparedRequest: ...

    def parse_response(self, data: dict[str, Any]) -> LLMChunk | None: ...


BACKEND_ADAPTERS: dict[str, APIAdapter] = {}

T = TypeVar("T", bound=APIAdapter)


def register_adapter(
    adapters: dict[str, APIAdapter], name: str
) -> Callable[[type[T]], type[T]]:

    def decorator(cls: type[T]) -> type[T]:
        adapters[name] = cls()
        return cls

    return decorator


@register_adapter(BACKEND_ADAPTERS, "anthropic")
class AnthropicAdapter(APIAdapter):
    """Adapter for Anthropic's native /v1/messages API."""

    endpoint: ClassVar[str] = "/v1/messages"

    # Track streaming state per request (tool index, accumulated tool input)
    _stream_state: ClassVar[dict[str, Any]] = {}

    def _convert_messages_for_anthropic(
        self, messages: list[LLMMessage]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert messages to Anthropic format.
        Extract system prompt and convert tool calls/results.
        Supports multimodal content with images.
        """
        system_prompt: str | None = None
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            match msg.role:
                case Role.system:
                    system_prompt = msg.content or ""

                case Role.user:
                    # Build content blocks for user message (supports images)
                    content_blocks: list[dict[str, Any]] = []

                    # Add images first if present
                    if msg.images:
                        for img in msg.images:
                            content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": img.media_type,
                                    "data": img.data,
                                },
                            })

                    # Add text content
                    if msg.content:
                        content_blocks.append({"type": "text", "text": msg.content})

                    # If we have content blocks, use them; otherwise use simple string
                    if content_blocks:
                        anthropic_messages.append({
                            "role": "user",
                            "content": content_blocks,
                        })
                    else:
                        anthropic_messages.append({"role": "user", "content": ""})

                case Role.assistant:
                    content_blocks: list[dict[str, Any]] = []

                    # Add text content if present
                    if msg.content:
                        content_blocks.append({"type": "text", "text": msg.content})

                    # Convert tool_calls to Anthropic tool_use blocks
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            tool_input = {}
                            if tc.function.arguments:
                                try:
                                    tool_input = json.loads(tc.function.arguments)
                                except json.JSONDecodeError:
                                    tool_input = {"raw": tc.function.arguments}

                            content_blocks.append({
                                "type": "tool_use",
                                "id": tc.id or f"tool_{id(tc)}",
                                "name": tc.function.name or "",
                                "input": tool_input,
                            })

                    anthropic_messages.append({
                        "role": "assistant",
                        "content": content_blocks if content_blocks else "",
                    })

                case Role.tool:
                    # Anthropic expects tool results as user messages with tool_result content
                    anthropic_messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id or "",
                                "content": msg.content or "",
                            }
                        ],
                    })

        return system_prompt, anthropic_messages

    def _convert_tools_for_anthropic(
        self, tools: list[AvailableTool] | None
    ) -> list[dict[str, Any]] | None:
        """Convert OpenAI tools format to Anthropic tools format.

        OpenAI: {"type": "function", "function": {"name": ..., "parameters": ...}}
        Anthropic: {"name": ..., "input_schema": ...}
        """
        if not tools:
            return None

        anthropic_tools = []
        for tool in tools:
            anthropic_tools.append({
                "name": tool.function.name,
                "description": tool.function.description,
                "input_schema": tool.function.parameters,
            })

        return anthropic_tools

    def build_payload(
        self,
        model_name: str,
        system_prompt: str | None,
        converted_messages: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": converted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 8192,  # Anthropic requires max_tokens
        }

        if system_prompt:
            payload["system"] = system_prompt

        anthropic_tools = self._convert_tools_for_anthropic(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        # Convert tool_choice
        if tool_choice:
            match tool_choice:
                case "auto":
                    payload["tool_choice"] = {"type": "auto"}
                case "none":
                    # Anthropic doesn't have "none", just don't send tools
                    payload.pop("tools", None)
                case "any" | "required":
                    payload["tool_choice"] = {"type": "any"}
                case AvailableTool() as specific_tool:
                    payload["tool_choice"] = {
                        "type": "tool",
                        "name": specific_tool.function.name,
                    }

        return payload

    def build_headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
    ) -> PreparedRequest:
        system_prompt, converted_messages = self._convert_messages_for_anthropic(
            messages
        )

        payload = self.build_payload(
            model_name,
            system_prompt,
            converted_messages,
            temperature,
            tools,
            max_tokens,
            tool_choice,
        )

        if enable_streaming:
            payload["stream"] = True

        headers = self.build_headers(api_key)
        body = json.dumps(payload).encode("utf-8")

        return PreparedRequest(self.endpoint, headers, body)

    def parse_response(self, data: dict[str, Any]) -> LLMChunk | None:
        """Parse Anthropic response (both streaming and non-streaming)."""
        event_type = data.get("type")

        # Handle streaming events
        match event_type:
            case "message_start":
                # Initial message event
                return LLMChunk(
                    message=LLMMessage(role=Role.assistant, content=""),
                    usage=self._parse_usage(data.get("message", {}).get("usage")),
                    finish_reason=None,
                )

            case "content_block_start":
                content_block = data.get("content_block", {})
                if content_block.get("type") == "tool_use":
                    # Start of tool use block
                    tool_call = ToolCall(
                        id=content_block.get("id"),
                        index=data.get("index", 0),
                        function=FunctionCall(
                            name=content_block.get("name"), arguments=""
                        ),
                    )
                    return LLMChunk(
                        message=LLMMessage(role=Role.assistant, tool_calls=[tool_call]),
                        finish_reason=None,
                    )
                # Text block start - no useful data
                return None

            case "content_block_delta":
                delta = data.get("delta", {})
                delta_type = delta.get("type")

                if delta_type == "text_delta":
                    return LLMChunk(
                        message=LLMMessage(
                            role=Role.assistant, content=delta.get("text", "")
                        ),
                        finish_reason=None,
                    )
                elif delta_type == "input_json_delta":
                    # Tool arguments delta
                    tool_call = ToolCall(
                        index=data.get("index", 0),
                        function=FunctionCall(arguments=delta.get("partial_json", "")),
                    )
                    return LLMChunk(
                        message=LLMMessage(role=Role.assistant, tool_calls=[tool_call]),
                        finish_reason=None,
                    )
                # Unknown delta type - skip
                return None

            case "content_block_stop":
                # Just a marker, no useful data
                return None

            case "message_delta":
                delta = data.get("delta", {})
                stop_reason = delta.get("stop_reason")
                finish_reason = self._convert_stop_reason(stop_reason)
                usage = self._parse_usage(data.get("usage"))
                return LLMChunk(
                    message=LLMMessage(role=Role.assistant, content=""),
                    usage=usage,
                    finish_reason=finish_reason,
                )

            case "message_stop":
                # Don't yield a chunk for message_stop - it has no useful data
                # The usage and finish_reason were already sent in message_delta
                return None

            case "message":
                # Non-streaming full response
                return self._parse_full_message(data)

            case _:
                # Try to parse as non-streaming response
                if "content" in data and "role" in data:
                    return self._parse_full_message(data)

                # Unknown event type - skip
                return None

    def _parse_full_message(self, data: dict[str, Any]) -> LLMChunk:
        """Parse a complete Anthropic message response."""
        content_blocks = data.get("content", [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for idx, block in enumerate(content_blocks):
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_input = block.get("input", {})
                arguments = (
                    json.dumps(tool_input)
                    if isinstance(tool_input, dict)
                    else str(tool_input)
                )
                tool_calls.append(
                    ToolCall(
                        id=block.get("id"),
                        index=idx,
                        function=FunctionCall(
                            name=block.get("name"), arguments=arguments
                        ),
                    )
                )

        content = "\n".join(text_parts) if text_parts else ""
        stop_reason = data.get("stop_reason")
        finish_reason = self._convert_stop_reason(stop_reason)
        usage = self._parse_usage(data.get("usage"))

        return LLMChunk(
            message=LLMMessage(
                role=Role.assistant,
                content=content if content else None,
                tool_calls=tool_calls if tool_calls else None,
            ),
            usage=usage,
            finish_reason=finish_reason,
        )

    def _convert_stop_reason(self, stop_reason: str | None) -> str | None:
        """Convert Anthropic stop_reason to OpenAI-style finish_reason."""
        if not stop_reason:
            return None
        mapping = {
            "end_turn": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
            "stop_sequence": "stop",
        }
        return mapping.get(stop_reason, "stop")

    def _parse_usage(self, usage_data: dict[str, Any] | None) -> LLMUsage:
        """Parse Anthropic usage data."""
        if not usage_data:
            return LLMUsage(prompt_tokens=0, completion_tokens=0)
        return LLMUsage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
        )


@register_adapter(BACKEND_ADAPTERS, "openai")
class OpenAIAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def _convert_message_for_openai(self, msg: LLMMessage) -> dict[str, Any]:
        """Convert a single LLMMessage to OpenAI format, handling images."""
        result = msg.model_dump(exclude_none=True, exclude={"images"})

        # If message has images, convert content to multimodal format
        if msg.images and msg.role == Role.user:
            content_parts: list[dict[str, Any]] = []

            # Add images first
            for img in msg.images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{img.data}"},
                })

            # Add text content
            if msg.content:
                content_parts.append({"type": "text", "text": msg.content})

            result["content"] = content_parts

        return result

    def build_payload(
        self,
        model_name: str,
        converted_messages: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> dict[str, Any]:
        payload = {
            "model": model_name,
            "messages": converted_messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = [tool.model_dump(exclude_none=True) for tool in tools]
        if tool_choice:
            payload["tool_choice"] = (
                tool_choice
                if isinstance(tool_choice, str)
                else tool_choice.model_dump()
            )
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return payload

    def build_headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
    ) -> PreparedRequest:
        # Convert messages with image support
        converted_messages = [self._convert_message_for_openai(msg) for msg in messages]

        payload = self.build_payload(
            model_name, converted_messages, temperature, tools, max_tokens, tool_choice
        )

        if enable_streaming:
            payload["stream"] = True
            stream_options = {"include_usage": True}
            if provider.name == "mistral":
                stream_options["stream_tool_calls"] = True
            payload["stream_options"] = stream_options

        headers = self.build_headers(api_key)

        body = json.dumps(payload).encode("utf-8")

        return PreparedRequest(self.endpoint, headers, body)

    def parse_response(self, data: dict[str, Any]) -> LLMChunk:
        if data.get("choices"):
            if "message" in data["choices"][0]:
                message = LLMMessage.model_validate(data["choices"][0]["message"])
            elif "delta" in data["choices"][0]:
                message = LLMMessage.model_validate(data["choices"][0]["delta"])
            else:
                raise ValueError("Invalid response data")
            finish_reason = data["choices"][0].get("finish_reason", None)

        elif "message" in data:
            message = LLMMessage.model_validate(data["message"])
            finish_reason = data["choices"][0].get("finish_reason", None)
        elif "delta" in data:
            message = LLMMessage.model_validate(data["delta"])
            finish_reason = None
        else:
            message = LLMMessage(role=Role.assistant, content="")
            finish_reason = None

        usage_data = data.get("usage") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )

        return LLMChunk(message=message, usage=usage, finish_reason=finish_reason)


class GenericBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        provider: ProviderConfig,
        timeout: float = 720.0,
    ) -> None:
        """Initialize the backend.

        Args:
            client: Optional httpx client to use. If not provided, one will be created.
        """
        self._client = client
        self._owns_client = client is None
        self._provider = provider
        self._timeout = timeout

    async def __aenter__(self) -> GenericBackend:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
            self._owns_client = True
        return self._client

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> LLMChunk:
        api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = BACKEND_ADAPTERS[api_style]

        endpoint, headers, body = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=False,
            provider=self._provider,
            api_key=api_key,
        )

        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._provider.get_api_base()}{endpoint}"

        try:
            res_data, _ = await self._make_request(url, body, headers)
            result = adapter.parse_response(res_data)
            if result is None:
                raise ValueError("Non-streaming response returned None")
            return result

        except httpx.HTTPStatusError as e:
            raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=url,
                response=e.response,
                headers=dict(e.response.headers.items()),
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e
        except httpx.RequestError as e:
            raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=url,
                error=e,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e

    async def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = BACKEND_ADAPTERS[api_style]

        endpoint, headers, body = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=True,
            provider=self._provider,
            api_key=api_key,
        )

        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._provider.get_api_base()}{endpoint}"

        try:
            async for res_data in self._make_streaming_request(url, body, headers):
                if (chunk := adapter.parse_response(res_data)) is not None:
                    yield chunk

        except httpx.HTTPStatusError as e:
            raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=url,
                response=e.response,
                headers=dict(e.response.headers.items()),
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e
        except httpx.RequestError as e:
            raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=url,
                error=e,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e

    class HTTPResponse(NamedTuple):
        data: dict[str, Any]
        headers: dict[str, str]

    @async_retry(tries=3)
    async def _make_request(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> HTTPResponse:
        client = self._get_client()
        response = await client.post(url, content=data, headers=headers)
        response.raise_for_status()

        response_headers = dict(response.headers.items())
        response_body = response.json()
        return self.HTTPResponse(response_body, response_headers)

    @async_generator_retry(tries=3)
    async def _make_streaming_request(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> AsyncGenerator[dict[str, Any]]:
        client = self._get_client()
        async with client.stream(
            method="POST", url=url, content=data, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip() == "":
                    continue

                DELIM_CHAR = ":"
                assert f"{DELIM_CHAR} " in line, "line should look like `key: value`"
                delim_index = line.find(DELIM_CHAR)
                key = line[0:delim_index]
                value = line[delim_index + 2 :]

                if key != "data":
                    # This might be the case with openrouter, so we just ignore it
                    continue
                if value == "[DONE]":
                    return
                yield json.loads(value.strip())

    async def count_tokens(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        tools: list[AvailableTool] | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> int:
        probe_messages = list(messages)
        if not probe_messages or probe_messages[-1].role != Role.user:
            probe_messages.append(LLMMessage(role=Role.user, content=""))

        result = await self.complete(
            model=model,
            messages=probe_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=16,  # Minimal amount for openrouter with openai models
            tool_choice=tool_choice,
            extra_headers=extra_headers,
        )
        assert result.usage is not None, (
            "Usage should be present in non-streaming completions"
        )

        return result.usage.prompt_tokens

    async def close(self) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None
