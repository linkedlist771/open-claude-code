from __future__ import annotations

from abc import ABC
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from vibe.core.tools.base import BaseTool


@dataclass
class ResumeSessionInfo:
    type: Literal["continue", "resume"]
    session_id: str
    session_time: str

    def message(self) -> str:
        action = None
        match self.type:
            case "continue":
                action = "Continuing"
            case "resume":
                action = "Resuming"
        return f"{action} session `{self.session_id}` from {self.session_time}"


class AgentStats(BaseModel):
    steps: int = 0
    session_prompt_tokens: int = 0
    session_completion_tokens: int = 0
    tool_calls_agreed: int = 0
    tool_calls_rejected: int = 0
    tool_calls_failed: int = 0
    tool_calls_succeeded: int = 0

    context_tokens: int = 0

    last_turn_prompt_tokens: int = 0
    last_turn_completion_tokens: int = 0
    last_turn_duration: float = 0.0
    tokens_per_second: float = 0.0

    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0

    @computed_field
    @property
    def session_total_llm_tokens(self) -> int:
        return self.session_prompt_tokens + self.session_completion_tokens

    @computed_field
    @property
    def last_turn_total_tokens(self) -> int:
        return self.last_turn_prompt_tokens + self.last_turn_completion_tokens

    @computed_field
    @property
    def session_cost(self) -> float:
        """Calculate the total session cost in dollars based on token usage and pricing.

        NOTE: This is a rough estimate and is worst-case scenario.
        The actual cost may be lower due to prompt caching.
        If the model changes mid-session, this uses current pricing for all tokens.
        """
        input_cost = (
            self.session_prompt_tokens / 1_000_000
        ) * self.input_price_per_million
        output_cost = (
            self.session_completion_tokens / 1_000_000
        ) * self.output_price_per_million
        return input_cost + output_cost

    def update_pricing(self, input_price: float, output_price: float) -> None:
        """Update pricing info when model changes.

        NOTE: session_cost will be recalculated using new pricing for all
        accumulated tokens. This is a known approximation when models change.
        This should not be a big issue, pricing is only used for max_price which is in
        programmatic mode, so user should not update models there.
        """
        self.input_price_per_million = input_price
        self.output_price_per_million = output_price

    def reset_context_state(self) -> None:
        """Reset context-related fields while preserving cumulative session stats.

        Used after config reload or similar operations where the context
        changes but we want to preserve session totals.
        """
        self.context_tokens = 0
        self.last_turn_prompt_tokens = 0
        self.last_turn_completion_tokens = 0
        self.last_turn_duration = 0.0
        self.tokens_per_second = 0.0


class SessionInfo(BaseModel):
    session_id: str
    start_time: str
    message_count: int
    stats: AgentStats
    save_dir: str


class SessionMetadata(BaseModel):
    session_id: str
    start_time: str
    end_time: str | None
    git_commit: str | None
    git_branch: str | None
    environment: dict[str, str | None]
    auto_approve: bool = False
    username: str


StrToolChoice = Literal["auto", "none", "any", "required"]


class AvailableFunction(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class AvailableTool(BaseModel):
    type: Literal["function"] = "function"
    function: AvailableFunction


class FunctionCall(BaseModel):
    name: str | None = None
    arguments: str | None = None


class ToolCall(BaseModel):
    id: str | None = None
    index: int | None = None
    function: FunctionCall = Field(default_factory=FunctionCall)
    type: str = "function"


# Image content types for multimodal messages
class ImageContent(BaseModel):
    """Represents an image in a message, either as base64 data or a URL."""

    model_config = ConfigDict(frozen=True)

    type: Literal["image"] = "image"
    media_type: str  # e.g., "image/png", "image/jpeg", "image/gif", "image/webp"
    data: str  # base64-encoded image data
    source_path: str | None = None  # Optional: original file path for display

    @classmethod
    def from_file(cls, path: Path | str) -> ImageContent:
        """Create ImageContent from a file path."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        # Determine media type from extension
        extension = path.suffix.lower()
        media_type_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_type_map.get(extension)
        if not media_type:
            raise ValueError(
                f"Unsupported image format: {extension}. "
                f"Supported formats: {', '.join(media_type_map.keys())}"
            )

        # Read and encode the image
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        return cls(media_type=media_type, data=data, source_path=str(path))

    @classmethod
    def from_base64(
        cls, data: str, media_type: str, source_path: str | None = None
    ) -> ImageContent:
        """Create ImageContent from base64-encoded data."""
        return cls(media_type=media_type, data=data, source_path=source_path)


class TextContent(BaseModel):
    """Represents text content in a multimodal message."""

    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


# Union type for content parts
ContentPart = TextContent | ImageContent


def _content_before(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts: list[str] = []
        for p in v:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(v)


Content = Annotated[str, BeforeValidator(_content_before)]


class Role(StrEnum):
    system = auto()
    user = auto()
    assistant = auto()
    tool = auto()


class ApprovalResponse(StrEnum):
    YES = "y"
    NO = "n"


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Role
    content: Content | None = None
    images: list[ImageContent] | None = None  # Optional list of images for multimodal
    tool_calls: list[ToolCall] | None = None
    name: str | None = None
    tool_call_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_any(cls, v: Any) -> dict[str, Any] | Any:
        if isinstance(v, dict):
            v.setdefault("content", "")
            v.setdefault("role", "assistant")
            return v
        return {
            "role": str(getattr(v, "role", "assistant")),
            "content": getattr(v, "content", ""),
            "images": getattr(v, "images", None),
            "tool_calls": getattr(v, "tool_calls", None),
            "name": getattr(v, "name", None),
            "tool_call_id": getattr(v, "tool_call_id", None),
        }

    def has_images(self) -> bool:
        """Check if this message contains images."""
        return bool(self.images)


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMChunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    message: LLMMessage
    finish_reason: str | None = None
    usage: LLMUsage | None = None


class BaseEvent(BaseModel, ABC):
    """Abstract base class for all agent events."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AssistantEvent(BaseEvent):
    content: str
    stopped_by_middleware: bool = False


class ToolCallEvent(BaseEvent):
    tool_name: str
    tool_class: type[BaseTool]
    args: BaseModel
    tool_call_id: str


class ToolResultEvent(BaseEvent):
    tool_name: str
    tool_class: type[BaseTool] | None
    result: BaseModel | None = None
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    duration: float | None = None
    tool_call_id: str


class CompactStartEvent(BaseEvent):
    current_context_tokens: int
    threshold: int


class CompactEndEvent(BaseEvent):
    old_context_tokens: int
    new_context_tokens: int
    summary_length: int


class OutputFormat(StrEnum):
    TEXT = auto()
    JSON = auto()
    STREAMING = auto()


type AsyncApprovalCallback = Callable[
    [str, dict[str, Any], str], Awaitable[tuple[ApprovalResponse, str | None]]
]

type SyncApprovalCallback = Callable[
    [str, dict[str, Any], str], tuple[ApprovalResponse, str | None]
]

type ApprovalCallback = AsyncApprovalCallback | SyncApprovalCallback
