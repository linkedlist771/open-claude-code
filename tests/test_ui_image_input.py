from __future__ import annotations

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
import vibe.cli.textual_ui.widgets.chat_input.text_area as text_area_module
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import ImageContent


@pytest.fixture
def vibe_app() -> VibeApp:
    config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )
    return VibeApp(config=config)


@pytest.mark.asyncio
async def test_ctrl_v_pastes_image_and_inserts_placeholder(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = ImageContent.from_base64(data="AA==", media_type="image/png")
    monkeypatch.setattr(text_area_module, "get_image_from_clipboard", lambda: image)

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        await pilot.press("ctrl+v")
        await pilot.pause(0)

        assert chat_input.value == "[image#1]"
        assert chat_input.has_images
        assert chat_input.attached_images == [image]


@pytest.mark.asyncio
async def test_backspace_deletes_placeholder_and_detaches_image(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = ImageContent.from_base64(data="AA==", media_type="image/png")
    monkeypatch.setattr(text_area_module, "get_image_from_clipboard", lambda: image)

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        await pilot.press("ctrl+v")
        await pilot.pause(0)

        await pilot.press("backspace")
        await pilot.pause(0)

        assert chat_input.value == ""
        assert not chat_input.has_images


@pytest.mark.asyncio
async def test_deleting_first_placeholder_renumbers_remaining(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    images = [
        ImageContent.from_base64(data="AA==", media_type="image/png"),
        ImageContent.from_base64(data="AQ==", media_type="image/png"),
    ]

    def _fake_clipboard() -> ImageContent:
        return images.pop(0)

    monkeypatch.setattr(text_area_module, "get_image_from_clipboard", _fake_clipboard)

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)

        await pilot.press("ctrl+v")
        await pilot.press("ctrl+v")
        await pilot.pause(0)

        assert chat_input.value == "[image#1][image#2]"
        assert len(chat_input.attached_images) == 2

        for _ in range(len("[image#2]") + len("[image#1]")):
            await pilot.press("left")

        await pilot.press("delete")
        await pilot.pause(0)

        assert chat_input.value == "[image#1]"
        assert len(chat_input.attached_images) == 1
