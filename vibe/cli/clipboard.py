from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import tempfile

import pyperclip
from textual.app import App

from vibe.core.types import ImageContent

_PREVIEW_MAX_LENGTH = 40


def _copy_osc52(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    osc52_seq = f"\033]52;c;{encoded}\a"
    if os.environ.get("TMUX"):
        osc52_seq = f"\033Ptmux;\033{osc52_seq}\033\\"

    with open("/dev/tty", "w") as tty:
        tty.write(osc52_seq)
        tty.flush()


def _shorten_preview(texts: list[str]) -> str:
    dense_text = "⏎".join(texts).replace("\n", "⏎")
    if len(dense_text) > _PREVIEW_MAX_LENGTH:
        return f"{dense_text[: _PREVIEW_MAX_LENGTH - 1]}…"
    return dense_text


def copy_selection_to_clipboard(app: App) -> None:
    selected_texts = []

    for widget in app.query("*"):
        if not hasattr(widget, "text_selection"):
            continue

        try:
            selection = widget.text_selection
        except Exception:
            continue

        if not selection:
            continue

        try:
            result = widget.get_selection(selection)
        except Exception:
            continue

        if not result:
            continue

        selected_text, _ = result
        if selected_text.strip():
            selected_texts.append(selected_text)

    if not selected_texts:
        return

    combined_text = "\n".join(selected_texts)

    for copy_fn in [_copy_osc52, pyperclip.copy, app.copy_to_clipboard]:
        try:
            copy_fn(combined_text)
        except:
            pass
        else:
            app.notify(
                f'"{_shorten_preview(selected_texts)}" copied to clipboard',
                severity="information",
                timeout=2,
            )
            break
    else:
        app.notify(
            "Failed to copy - no clipboard method available",
            severity="warning",
            timeout=3,
        )


def get_image_from_clipboard() -> ImageContent | None:
    """Try to get an image from the system clipboard.

    Returns ImageContent if an image is found, None otherwise.
    Currently supports macOS via pbpaste/osascript.
    """
    # Try macOS approach using osascript
    if os.uname().sysname == "Darwin":
        return _get_image_from_clipboard_macos()

    # TODO: Add Linux (xclip) and Windows support
    return None


def _get_image_from_clipboard_macos() -> ImageContent | None:
    """Get image from clipboard on macOS."""
    try:
        # Create a temporary file to save the clipboard image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        # Use osascript to save clipboard image to file
        script = f'''
        set theFile to POSIX file "{tmp_path}"
        try
            set imageData to the clipboard as «class PNGf»
            set fileRef to open for access theFile with write permission
            write imageData to fileRef
            close access fileRef
            return "success"
        on error
            return "no_image"
        end try
        '''

        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )

        if result.returncode != 0 or "no_image" in result.stdout:
            tmp_path.unlink(missing_ok=True)
            return None

        # Check if file was created and has content
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            tmp_path.unlink(missing_ok=True)
            return None

        # Read the image and create ImageContent
        try:
            image = ImageContent.from_file(tmp_path)
            return image
        finally:
            tmp_path.unlink(missing_ok=True)

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return None
