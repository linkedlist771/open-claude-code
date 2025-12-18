from __future__ import annotations

import os
from typing import ClassVar

from dotenv import set_key
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical
from textual.events import MouseUp
from textual.validation import Length, Regex
from textual.widgets import Input, Link, Static

from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.core.config import (
    _DEFAULT_API_BASES,
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
    MissingAPIKeyError,
    ProviderConfig,
    VibeConfig,
)
from vibe.core.config_path import GLOBAL_ENV_FILE
from vibe.setup.onboarding.base import OnboardingScreen

PROVIDER_HELP = {
    "anthropic": ("https://console.anthropic.com/settings/keys", "Anthropic Console"),
    "openai": ("https://platform.openai.com/api-keys", "OpenAI Platform"),
}

CONFIG_DOCS_URL = (
    "https://github.com/anthropics/claude-vibe?tab=readme-ov-file#configuration"
)


def _save_to_env_file(key: str, value: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, key, value)


def _get_default_provider() -> ProviderConfig:
    """Get the default provider for the active model."""
    try:
        config = VibeConfig()
        active_model = config.get_active_model()
        return config.get_provider_for_model(active_model)
    except MissingAPIKeyError:
        # Fall back to defaults when API key is not set
        default_model = DEFAULT_MODELS[0] if DEFAULT_MODELS else None
        if default_model:
            for provider in DEFAULT_PROVIDERS:
                if provider.name == default_model.provider:
                    return provider
        # Ultimate fallback: first default provider or create one
        if DEFAULT_PROVIDERS:
            return DEFAULT_PROVIDERS[0]
        return ProviderConfig(
            name="anthropic",
            api_base="https://api.anthropic.com",
            api_key_env_var="ANTHROPIC_API_KEY",
            api_style="anthropic",
        )


class ApiKeyScreen(OnboardingScreen):
    """Step 1: Enter API Key"""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = "base_url"

    def __init__(self) -> None:
        super().__init__()
        self.provider = _get_default_provider()

    def _compose_provider_link(self, provider_name: str) -> ComposeResult:
        if self.provider.name not in PROVIDER_HELP:
            yield Static(f"Enter your {provider_name} API key:")
            yield Static("[dim](or use a custom API provider)[/]")
            return

        help_url, help_name = PROVIDER_HELP[self.provider.name]
        yield Static(f"Get your API key from {help_name}:")
        yield Center(
            Horizontal(
                Static("→ ", classes="link-chevron"),
                Link(help_url, url=help_url),
                classes="link-row",
            )
        )
        yield Static("")
        yield Static("[dim]Or use your own API key from a custom provider[/]")

    def compose(self) -> ComposeResult:
        provider_name = self.provider.name.capitalize()

        self.api_key_input = Input(
            password=True,
            id="key",
            placeholder="Paste your API key here",
            validators=[Length(minimum=1, failure_description="No API key provided.")],
        )

        with Vertical(id="api-key-outer"):
            yield Static("", classes="spacer")
            yield Center(Static("Step 1: API Key", id="api-key-title"))
            with Center():
                with Vertical(id="api-key-content"):
                    yield from self._compose_provider_link(provider_name)
                    yield Static("Paste your API key below:", id="paste-hint")
                    yield Center(Horizontal(self.api_key_input, id="input-box"))
                    yield Static("", id="feedback")
            yield Static("", classes="spacer")

    def on_mount(self) -> None:
        self.api_key_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "key":
            return

        feedback = self.query_one("#feedback", Static)
        input_box = self.query_one("#input-box")

        if event.validation_result is None:
            return

        input_box.remove_class("valid", "invalid")
        feedback.remove_class("error", "success")

        if event.validation_result.is_valid:
            feedback.update("Press Enter to continue →")
            feedback.add_class("success")
            input_box.add_class("valid")
            return

        descriptions = event.validation_result.failure_descriptions
        feedback.update(descriptions[0])
        feedback.add_class("error")
        input_box.add_class("invalid")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "key":
            if event.validation_result and event.validation_result.is_valid:
                # Store API key temporarily in app state
                self.app._api_key_temp = self.api_key_input.value  # type: ignore
                self.app._provider_temp = self.provider  # type: ignore
                self.action_next()

    def on_mouse_up(self, event: MouseUp) -> None:
        copy_selection_to_clipboard(self.app)


class BaseUrlScreen(OnboardingScreen):
    """Step 2: Confirm/Edit Base URL"""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None  # Final screen

    def __init__(self) -> None:
        super().__init__()
        self.provider = _get_default_provider()
        self.default_base_url: str = (
            _DEFAULT_API_BASES.get(self.provider.name) or self.provider.api_base
        )

    def _compose_provider_info(self, provider_name: str) -> ComposeResult:
        yield Static(f"Configure the API endpoint for {provider_name}:")
        yield Static(
            "[dim](Press Enter to use the default, or edit for custom endpoints)[/]"
        )

    def _compose_config_docs(self) -> ComposeResult:
        yield Static("[dim]Learn more about Vibe configuration:[/]")
        yield Center(
            Horizontal(
                Static("→ ", classes="link-chevron"),
                Link(CONFIG_DOCS_URL, url=CONFIG_DOCS_URL),
                classes="link-row",
            )
        )

    def compose(self) -> ComposeResult:
        provider_name = self.provider.name.capitalize()

        self.base_url_input = Input(
            id="base_url",
            placeholder=self.default_base_url,
            value=self.default_base_url,
            validators=[
                Regex(
                    r"^https?://.*",
                    failure_description="Must be a valid URL starting with http:// or https://",
                )
            ],
        )

        with Vertical(id="api-key-outer"):
            yield Static("", classes="spacer")
            yield Center(Static("Step 2: API Base URL", id="api-key-title"))
            with Center():
                with Vertical(id="api-key-content"):
                    yield from self._compose_provider_info(provider_name)
                    yield Static("Enter the API base URL below:", id="paste-hint")
                    yield Center(Horizontal(self.base_url_input, id="input-box"))
                    yield Static("", id="feedback")
                    yield Static("")
                    yield from self._compose_config_docs()
            yield Static("", classes="spacer")

    def on_mount(self) -> None:
        self.base_url_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "base_url":
            return

        feedback = self.query_one("#feedback", Static)
        input_box = self.query_one("#input-box")

        if event.validation_result is None:
            return

        input_box.remove_class("valid", "invalid")
        feedback.remove_class("error", "success")

        if event.validation_result.is_valid:
            feedback.update("Press Enter to finish setup →")
            feedback.add_class("success")
            input_box.add_class("valid")
            return

        descriptions = event.validation_result.failure_descriptions
        feedback.update(descriptions[0])
        feedback.add_class("error")
        input_box.add_class("invalid")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "base_url":
            if event.validation_result and event.validation_result.is_valid:
                self._save_and_finish()

    def _save_and_finish(self) -> None:
        # Get API key from previous screen
        api_key = getattr(self.app, "_api_key_temp", "")
        provider = getattr(self.app, "_provider_temp", self.provider)

        base_url = self.base_url_input.value.strip() or self.default_base_url

        env_key = provider.api_key_env_var
        os.environ[env_key] = api_key

        try:
            _save_to_env_file(env_key, api_key)

            # Save custom base URL if different from default
            if base_url != self.default_base_url:
                # Use provider's api_base_env_var if available, otherwise generate one
                base_url_env_key = (
                    provider.api_base_env_var
                    if provider.api_base_env_var
                    else f"{provider.name.upper()}_API_BASE"
                )
                os.environ[base_url_env_key] = base_url
                _save_to_env_file(base_url_env_key, base_url)

        except OSError as err:
            self.app.exit(f"save_error:{err}")
            return

        self.app.exit("completed")

    def on_mouse_up(self, event: MouseUp) -> None:
        copy_selection_to_clipboard(self.app)
