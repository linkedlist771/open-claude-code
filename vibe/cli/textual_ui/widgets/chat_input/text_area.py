from __future__ import annotations

from bisect import bisect_left
import re
from typing import Any, ClassVar

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from vibe.cli.autocompletion.base import CompletionResult
from vibe.cli.clipboard import get_image_from_clipboard
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.core.types import ImageContent

_IMAGE_PLACEHOLDER_RE = re.compile(r"\[image#(?P<index>[1-9][0-9]*)\]")


class ChatTextArea(TextArea):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding(
            "shift+enter,ctrl+j",
            "insert_newline",
            "New Line",
            show=False,
            priority=True,
        ),
        Binding("ctrl+v", "paste_with_image", "Paste", show=False, priority=True),
    ]

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class HistoryPrevious(Message):
        def __init__(self, prefix: str) -> None:
            self.prefix = prefix
            super().__init__()

    class HistoryNext(Message):
        def __init__(self, prefix: str) -> None:
            self.prefix = prefix
            super().__init__()

    class HistoryReset(Message):
        """Message sent when history navigation should be reset."""

    class ImagePasted(Message):
        """Message sent when an image is pasted from clipboard."""

        def __init__(self, image: ImageContent) -> None:
            self.image = image
            super().__init__()

    class ImagePlaceholdersDeleted(Message):
        """Message sent when one or more image placeholders are deleted."""

        def __init__(self, indices: list[int]) -> None:
            self.indices = indices
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._history_prefix: str | None = None
        self._last_text = ""
        self._navigating_history = False
        self._last_cursor_col: int = 0
        self._last_used_prefix: str | None = None
        self._original_text: str = ""
        self._cursor_pos_after_load: tuple[int, int] | None = None
        self._cursor_moved_since_load: bool = False
        self._completion_manager: MultiCompletionManager | None = None

    def action_paste_with_image(self) -> None:
        """Paste from clipboard, preferring images when available."""
        if image := get_image_from_clipboard():
            self.post_message(self.ImagePasted(image))
            return

        self.action_paste()

    async def _on_paste(self, event: events.Paste) -> None:
        """Handle paste event, checking for images first.

        This overrides the parent TextArea's _on_paste to intercept paste events
        and check for images in the clipboard before falling back to text paste.
        """
        if image := get_image_from_clipboard():
            self.post_message(self.ImagePasted(image))
            event.prevent_default()
            return

        # No image found, use parent's default paste behavior
        await super()._on_paste(event)

    def _location_to_offset(self, location: tuple[int, int]) -> int:
        text = self.text
        row, col = location

        if not text:
            return 0

        lines = text.split("\n")
        row = max(0, min(row, len(lines) - 1))
        col = max(0, col)

        offset = sum(len(lines[i]) + 1 for i in range(row))
        return offset + min(col, len(lines[row]))

    def _get_selection_offset_range(self) -> tuple[int, int] | None:
        selection = self.selection
        if selection.is_empty:
            return None

        start = self._location_to_offset(selection.start)
        end = self._location_to_offset(selection.end)
        return (min(start, end), max(start, end))

    def _get_placeholders_overlapping_range(
        self, start: int, end: int
    ) -> list[tuple[int, int, int]]:
        overlaps: list[tuple[int, int, int]] = []
        for match in _IMAGE_PLACEHOLDER_RE.finditer(self.text):
            if match.start() < end and match.end() > start:
                overlaps.append((match.start(), match.end(), int(match["index"])))
        return overlaps

    def _renumber_placeholders_after_removal(
        self, text: str, removed_indices: list[int]
    ) -> str:
        unique_removed = sorted(set(removed_indices))
        if not unique_removed:
            return text

        def _renumber(match: re.Match[str]) -> str:
            old = int(match["index"])
            shift = bisect_left(unique_removed, old)
            return f"[image#{old - shift}]"

        return _IMAGE_PLACEHOLDER_RE.sub(_renumber, text)

    def _delete_range(
        self, start_offset: int, end_offset: int, *, removed_indices: list[int]
    ) -> None:
        text = self.text
        start_offset = max(0, min(start_offset, len(text)))
        end_offset = max(start_offset, min(end_offset, len(text)))

        updated = f"{text[:start_offset]}{text[end_offset:]}"
        updated = self._renumber_placeholders_after_removal(updated, removed_indices)
        self.load_text(updated)
        self.set_cursor_offset(start_offset)

        unique_indices = sorted(set(removed_indices))
        if unique_indices:
            self.post_message(self.ImagePlaceholdersDeleted(unique_indices))

    def _handle_placeholder_delete(self, key: str) -> bool:
        selection_range = self._get_selection_offset_range()
        if selection_range:
            start, end = selection_range
            overlaps = self._get_placeholders_overlapping_range(start, end)
            if not overlaps:
                return False

            removed_indices = [idx for _, _, idx in overlaps]
            delete_start = min([start, *(s for s, _, _ in overlaps)])
            delete_end = max([end, *(e for _, e, _ in overlaps)])
            self._delete_range(
                delete_start, delete_end, removed_indices=removed_indices
            )
            return True

        cursor_offset = self.get_cursor_offset()
        match key:
            case "backspace":
                if cursor_offset <= 0:
                    return False
                target_offset = cursor_offset - 1
            case "delete":
                if cursor_offset >= len(self.text):
                    return False
                target_offset = cursor_offset
            case _:
                return False

        for placeholder in _IMAGE_PLACEHOLDER_RE.finditer(self.text):
            if placeholder.start() <= target_offset < placeholder.end():
                idx = int(placeholder["index"])
                self._delete_range(
                    placeholder.start(), placeholder.end(), removed_indices=[idx]
                )
                return True

        return False

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)

    def on_click(self, event: events.Click) -> None:
        self._mark_cursor_moved_if_needed()

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not self._navigating_history and self.text != self._last_text:
            self._reset_prefix()
            self._original_text = ""
            self._cursor_pos_after_load = None
            self._cursor_moved_since_load = False
            self.post_message(self.HistoryReset())
        self._last_text = self.text
        was_navigating_history = self._navigating_history
        self._navigating_history = False

        if self._completion_manager and not was_navigating_history:
            self._completion_manager.on_text_changed(
                self.text, self.get_cursor_offset()
            )

    def _reset_prefix(self) -> None:
        self._history_prefix = None
        self._last_used_prefix = None

    def _mark_cursor_moved_if_needed(self) -> None:
        if (
            self._cursor_pos_after_load is not None
            and not self._cursor_moved_since_load
            and self.cursor_location != self._cursor_pos_after_load
        ):
            self._cursor_moved_since_load = True
            self._reset_prefix()

    def _get_prefix_up_to_cursor(self) -> str:
        cursor_row, cursor_col = self.cursor_location
        lines = self.text.split("\n")
        if cursor_row < len(lines):
            return lines[cursor_row][:cursor_col]
        return ""

    def _handle_history_up(self) -> bool:
        cursor_row, cursor_col = self.cursor_location
        if cursor_row == 0:
            if self._history_prefix is not None and cursor_col != self._last_cursor_col:
                self._reset_prefix()
                self._last_cursor_col = 0

            if self._history_prefix is None:
                self._history_prefix = self._get_prefix_up_to_cursor()

            self._navigating_history = True
            self.post_message(self.HistoryPrevious(self._history_prefix))
            return True
        return False

    def _handle_history_down(self) -> bool:
        cursor_row, cursor_col = self.cursor_location
        total_lines = self.text.count("\n") + 1

        on_first_line_unmoved = cursor_row == 0 and not self._cursor_moved_since_load
        on_last_line = cursor_row == total_lines - 1

        should_intercept = (
            on_first_line_unmoved and self._history_prefix is not None
        ) or on_last_line

        if not should_intercept:
            return False

        if self._history_prefix is not None and cursor_col != self._last_cursor_col:
            self._reset_prefix()
            self._last_cursor_col = 0

        if self._history_prefix is None:
            self._history_prefix = self._get_prefix_up_to_cursor()

        self._navigating_history = True
        self.post_message(self.HistoryNext(self._history_prefix))
        return True

    async def _on_key(self, event: events.Key) -> None:
        self._mark_cursor_moved_if_needed()

        manager = self._completion_manager
        if manager:
            match manager.on_key(event, self.text, self.get_cursor_offset()):
                case CompletionResult.HANDLED:
                    event.prevent_default()
                    event.stop()
                    return
                case CompletionResult.SUBMIT:
                    event.prevent_default()
                    event.stop()
                    value = self.text.strip()
                    if value:
                        self._reset_prefix()
                        self.post_message(self.Submitted(value))
                    return

        if event.key in {"backspace", "delete"} and self._handle_placeholder_delete(
            event.key
        ):
            event.prevent_default()
            event.stop()
            return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            value = self.text.strip()
            if value:
                self._reset_prefix()
                self.post_message(self.Submitted(value))
            return

        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            return

        if event.key == "up" and self._handle_history_up():
            event.prevent_default()
            event.stop()
            return

        if event.key == "down" and self._handle_history_down():
            event.prevent_default()
            event.stop()
            return

        await super()._on_key(event)
        self._mark_cursor_moved_if_needed()

    def set_completion_manager(self, manager: MultiCompletionManager | None) -> None:
        self._completion_manager = manager
        if self._completion_manager:
            self._completion_manager.on_text_changed(
                self.text, self.get_cursor_offset()
            )

    def get_cursor_offset(self) -> int:
        text = self.text
        row, col = self.cursor_location

        if not text:
            return 0

        lines = text.split("\n")
        row = max(0, min(row, len(lines) - 1))
        col = max(0, col)

        offset = sum(len(lines[i]) + 1 for i in range(row))
        return offset + min(col, len(lines[row]))

    def set_cursor_offset(self, offset: int) -> None:
        text = self.text
        if offset <= 0:
            self.move_cursor((0, 0))
            return

        if offset >= len(text):
            lines = text.split("\n")
            if not lines:
                self.move_cursor((0, 0))
                return
            last_row = len(lines) - 1
            self.move_cursor((last_row, len(lines[last_row])))
            return

        remaining = offset
        lines = text.split("\n")

        for row, line in enumerate(lines):
            line_length = len(line)
            if remaining <= line_length:
                self.move_cursor((row, remaining))
                return
            remaining -= line_length + 1

        last_row = len(lines) - 1
        self.move_cursor((last_row, len(lines[last_row])))

    def reset_history_state(self) -> None:
        self._reset_prefix()
        self._original_text = ""
        self._cursor_pos_after_load = None
        self._cursor_moved_since_load = False
        self._last_text = self.text

    def clear_text(self) -> None:
        self.clear()
        self.reset_history_state()
