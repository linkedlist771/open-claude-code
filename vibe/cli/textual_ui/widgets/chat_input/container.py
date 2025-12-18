from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from vibe.cli.autocompletion.path_completion import PathCompletionController
from vibe.cli.autocompletion.slash_command import SlashCommandController
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.core.autocompletion.completers import CommandCompleter, PathCompleter
from vibe.core.types import ImageContent

_IMAGE_PLACEHOLDER_RE = re.compile(r"\[image#(?P<index>[1-9][0-9]*)\]")


class ImagePreview(Static):
    """Widget to display attached image previews."""

    DEFAULT_CSS = """
    ImagePreview {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    ImagePreview .image-badge {
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        margin-right: 1;
    }
    ImagePreview .remove-btn {
        color: $error;
    }
    """

    def __init__(self, images: list[ImageContent], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._images = images

    def render(self) -> str:
        if not self._images:
            return ""
        count = len(self._images)
        names = []
        for img in self._images:
            if img.source_path:
                name = Path(img.source_path).name
            else:
                name = f"image.{img.media_type.split('/')[-1]}"
            names.append(name)
        return f"📎 {count} image(s): {', '.join(names)} [dim](Ctrl+Shift+X to clear)[/dim]"


class ChatInputContainer(Vertical):
    ID_INPUT_BOX = "input-box"
    BORDER_WARNING_CLASS = "border-warning"

    class Submitted(Message):
        def __init__(
            self, value: str, images: list[ImageContent] | None = None
        ) -> None:
            self.value = value
            self.images = images or []
            super().__init__()

    def __init__(
        self,
        history_file: Path | None = None,
        command_registry: CommandRegistry | None = None,
        show_warning: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._history_file = history_file
        self._command_registry = command_registry or CommandRegistry()
        self._show_warning = show_warning
        self._attached_images: list[ImageContent] = []
        self._image_preview: ImagePreview | None = None

        command_entries = [
            (alias, command.description)
            for command in self._command_registry.commands.values()
            for alias in sorted(command.aliases)
        ]

        self._completion_manager = MultiCompletionManager([
            SlashCommandController(CommandCompleter(command_entries), self),
            PathCompletionController(PathCompleter(), self),
        ])
        self._completion_popup: CompletionPopup | None = None
        self._body: ChatInputBody | None = None

    def compose(self) -> ComposeResult:
        self._completion_popup = CompletionPopup()
        yield self._completion_popup

        # Image preview area (hidden by default)
        self._image_preview = ImagePreview([], id="image-preview")
        self._image_preview.display = False
        yield self._image_preview

        with Vertical(
            id=self.ID_INPUT_BOX, classes="border-warning" if self._show_warning else ""
        ):
            self._body = ChatInputBody(history_file=self._history_file, id="input-body")

            yield self._body

    def on_mount(self) -> None:
        if not self._body:
            return

        self._body.set_completion_reset_callback(self._completion_manager.reset)
        if self._body.input_widget:
            self._body.input_widget.set_completion_manager(self._completion_manager)
            self._body.focus_input()

    @property
    def input_widget(self) -> ChatTextArea | None:
        return self._body.input_widget if self._body else None

    @property
    def value(self) -> str:
        if not self._body:
            return ""
        return self._body.value

    @value.setter
    def value(self, text: str) -> None:
        if not self._body:
            return
        self._body.value = text
        widget = self._body.input_widget
        if widget:
            self._completion_manager.on_text_changed(
                widget.text, widget.get_cursor_offset()
            )

    def focus_input(self) -> None:
        if self._body:
            self._body.focus_input()

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        if self._completion_popup:
            self._completion_popup.update_suggestions(suggestions, selected_index)

    def clear_completion_suggestions(self) -> None:
        if self._completion_popup:
            self._completion_popup.hide()

    def _format_insertion(self, replacement: str, suffix: str) -> str:
        """Format the insertion text with appropriate spacing.

        Args:
            replacement: The text to insert
            suffix: The text that follows the insertion point

        Returns:
            The formatted insertion text with spacing if needed
        """
        if replacement.startswith("@"):
            if replacement.endswith("/"):
                return replacement
            # For @-prefixed completions, add space unless suffix starts with whitespace
            return replacement + (" " if not suffix or not suffix[0].isspace() else "")

        # For other completions, add space only if suffix exists and doesn't start with whitespace
        return replacement + (" " if suffix and not suffix[0].isspace() else "")

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        widget = self.input_widget
        if not widget or not self._body:
            return

        text = widget.text
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))

        prefix = text[:start]
        suffix = text[end:]
        insertion = self._format_insertion(replacement, suffix)
        new_text = f"{prefix}{insertion}{suffix}"

        self._body.replace_input(new_text, cursor_offset=start + len(insertion))

    def on_chat_input_body_submitted(self, event: ChatInputBody.Submitted) -> None:
        event.stop()
        # Include attached images in the submission
        images = self._attached_images.copy() if self._attached_images else None
        self.post_message(self.Submitted(event.value, images=images))
        # Clear images after submission
        self.clear_images()

    def on_chat_text_area_image_pasted(self, event: ChatTextArea.ImagePasted) -> None:
        event.stop()
        self._attach_image_with_placeholder(event.image)

    def on_chat_text_area_image_placeholders_deleted(
        self, event: ChatTextArea.ImagePlaceholdersDeleted
    ) -> None:
        event.stop()

        # Indices are 1-based; remove from the end to keep positions stable.
        for idx in sorted(set(event.indices), reverse=True):
            if 1 <= idx <= len(self._attached_images):
                self._attached_images.pop(idx - 1)
        self._update_image_preview()

    def set_show_warning(self, show_warning: bool) -> None:
        self._show_warning = show_warning

        input_box = self.get_widget_by_id(self.ID_INPUT_BOX)
        if show_warning:
            input_box.add_class(self.BORDER_WARNING_CLASS)
        else:
            input_box.remove_class(self.BORDER_WARNING_CLASS)

    def add_image(self, image: ImageContent) -> None:
        """Add an image to the attached images list."""
        self._attached_images.append(image)
        self._update_image_preview()

    def _attach_image_with_placeholder(self, image: ImageContent) -> None:
        self.add_image(image)
        widget = self.input_widget
        if widget:
            widget.insert(f"[image#{len(self._attached_images)}]")

    def attach_image_with_placeholder(self, image: ImageContent) -> None:
        """Attach an image and insert a numbered placeholder into the input."""
        self._attach_image_with_placeholder(image)

    def add_image_from_path(self, path: Path | str) -> bool:
        """Add an image from a file path. Returns True if successful."""
        try:
            image = ImageContent.from_file(path)
            self.add_image(image)
            return True
        except (FileNotFoundError, ValueError):
            return False

    def clear_images(self) -> None:
        """Clear all attached images."""
        placeholder_max = len(self._attached_images)
        self._attached_images.clear()
        self._update_image_preview()

        widget = self.input_widget
        if widget and placeholder_max > 0 and self._body:
            original = widget.text

            def _strip(match: re.Match[str]) -> str:
                idx = int(match["index"])
                return "" if idx <= placeholder_max else match.group(0)

            updated = _IMAGE_PLACEHOLDER_RE.sub(_strip, original)
            if updated != original:
                self._body.replace_input(
                    updated, cursor_offset=min(widget.get_cursor_offset(), len(updated))
                )

    def _update_image_preview(self) -> None:
        """Update the image preview widget."""
        if self._image_preview:
            self._image_preview._images = self._attached_images
            self._image_preview.display = bool(self._attached_images)
            self._image_preview.refresh()

    @property
    def attached_images(self) -> list[ImageContent]:
        """Get the list of attached images."""
        return self._attached_images.copy()

    @property
    def has_images(self) -> bool:
        """Check if there are any attached images."""
        return bool(self._attached_images)
