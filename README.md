# Open Claude Code

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/github/license/anthropics/claude-code)](LICENSE)

```
 ██████╗ ██████╗ ███████╗███╗   ██╗     ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗
██╔═══██╗██╔══██╗██╔════╝████╗  ██║    ██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝
██║   ██║██████╔╝█████╗  ██╔██╗ ██║    ██║     ██║     ███████║██║   ██║██║  ██║█████╗
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║    ██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝
╚██████╔╝██║     ███████╗██║ ╚████║    ╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝     ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝
                               ██████╗ ██████╗ ██████╗ ███████╗
                              ██╔════╝██╔═══██╗██╔══██╗██╔════╝
                              ██║     ██║   ██║██║  ██║█████╗
                              ██║     ██║   ██║██║  ██║██╔══╝
                              ╚██████╗╚██████╔╝██████╔╝███████╗
                               ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
```

**An open-source CLI coding assistant that reproduces Claude Code with full transparency.**




<video src="https://github.com/user-attachments/assets/94822b51-8adb-43e3-87d2-a8b05290f3e7" width="100%" controls autoplay loop muted playsinline></video>



> 🔄 **This project is a fork of [Mistral Vibe](https://github.com/mistralai/mistral-vibe)**, modified to be fully compatible with Anthropic's Claude API format. Our goal is to provide an open-source alternative that lets you see exactly how an AI coding assistant works under the hood.

## Why Open Claude Code?

While Claude Code is a powerful coding assistant, it operates as a closed-source tool. **Open Claude Code** aims to:

- 🔓 **Full Transparency**: See exactly how the AI assistant processes your requests, makes tool calls, and generates responses
- 📖 **Streaming Output**: Support streaming ouput, you don't have to wait for serveral minutes in `claude code` terminal without any feedback due the non-streaming
- 🔌 **Claude API Compatible**: Native support for Anthropic's `/v1/messages` API format, including streaming
- 🛠️ **Extensible**: Add custom tools, providers, and configurations
- 🌐 **Provider Flexibility**: Use Anthropic's API directly, or configure custom endpoints (proxies, self-hosted models)
- 📖 **Learn by Doing**: Understand how agentic AI coding assistants work by examining the source code

## Key Features

- **Native Anthropic API Support**: First-class support for Claude models via Anthropic's native API format
- **Multi-Provider Architecture**: Supports OpenAI-compatible APIs, Anthropic, and custom providers
- **Interactive Chat**: A conversational AI agent that understands your requests and breaks down complex tasks
- **Image Paste Support**: Paste images directly from clipboard with `Ctrl+V` - images appear as `[image#1]` placeholders (macOS supported)
- **Powerful Toolset**:
  - Read, write, and patch files (`read_file`, `write_file`, `search_replace`)
  - Execute shell commands in a stateful terminal (`bash`)
  - Recursively search code with `grep` (with `ripgrep` support)
  - Manage a `todo` list to track the agent's work
- **Project-Aware Context**: Automatically scans your project's file structure and Git status
- **Advanced CLI Experience**: Autocompletion, persistent history, beautiful themes
- **Safety First**: Tool execution approval system

> [!WARNING]
> Works on Windows, but we officially support and target UNIX environments.

## Installation

### Using pip (recommended)

```bash
uv pip install ccvibe --sytem
```

### From source

```bash
git clone https://github.com/linkedlist771/open-claude-code
cd open-claude-code
uv sync
uv run ccvibe
```

## Quick Start

1. Navigate to your project's root directory:

   ```bash
   cd /path/to/your/project
   ```

2. Run Open Claude Code:

   ```bash
   ccvibe
   ```

3. If this is your first time running, it will:
   - Create a default configuration file at `~/.vibe/config.toml`
   - Prompt you to enter your API key
   - Prompt you to configure the API base URL (for custom endpoints)
   - Save your settings for future use

4. Start interacting with the agent!

## Configuration

### Provider Configuration

Open Claude Code supports multiple API providers. Configure them in `~/.vibe/config.toml`:

```toml
# Anthropic (Claude) - Native API support
[[providers]]
name = "anthropic"
api_base = "https://api.anthropic.com"
api_key_env_var = "ANTHROPIC_API_KEY"
api_base_env_var = "ANTHROPIC_API_BASE"  # Optional: override via environment
api_style = "anthropic"  # Use native Anthropic API format
backend = "generic"

# OpenAI-compatible provider
[[providers]]
name = "openai"
api_base = "https://api.openai.com/v1"
api_key_env_var = "OPENAI_API_KEY"
api_style = "openai"
backend = "generic"

# Custom endpoint (e.g., proxy or self-hosted)
[[providers]]
name = "custom"
api_base = "https://your-proxy.example.com"
api_key_env_var = "CUSTOM_API_KEY"
api_style = "anthropic"  # or "openai" depending on the API format
backend = "generic"
```

### API Styles

- **`anthropic`**: Native Anthropic `/v1/messages` API format with full streaming support
- **`openai`**: OpenAI-compatible `/chat/completions` format

### API Key Configuration

1. **Interactive Setup**: Run `ccvibe` and follow the prompts
2. **Environment Variables**:
   ```bash
   export ANTHROPIC_API_KEY="your_api_key"
   export ANTHROPIC_API_BASE="https://your-custom-endpoint.com"  # Optional
   ```
3. **`.env` File**: Create `~/.vibe/.env`:
   ```bash
   ANTHROPIC_API_KEY=your_api_key
   ANTHROPIC_API_BASE=https://your-custom-endpoint.com
   ```

### Model Configuration

```toml
[[models]]
name = "claude-sonnet-4-5-20250929"
provider = "anthropic"
alias = "claude-sonnet-4-5"
input_price = 3.0
output_price = 15.0

# Set the active model
active_model = "claude-sonnet-4-5"
```

## Usage

### Interactive Mode

```bash
ccvibe
```

- **Multi-line Input**: Press `Ctrl+J` or `Shift+Enter`
- **Paste Images**: Press `Ctrl+V` to paste image from clipboard (粘贴图片：按 `Ctrl+V` 从剪贴板粘贴图片)
- **Clear Images**: Press `Ctrl+Shift+X` to clear all attached images (清除图片：按 `Ctrl+Shift+X` 清除所有附加的图片)
- **File Paths**: Reference files with `@` (e.g., `@src/main.py`)
- **Shell Commands**: Prefix with `!` to bypass the agent (e.g., `!ls -l`)

### Programmatic Mode

```bash
ccvibe --prompt "Refactor the main function to be more modular."
```

### With Auto-Approve

```bash
ccvibe --auto-approve "Fix all linting errors in the project"
```

## Architecture

Open Claude Code uses a modular adapter pattern for API communication:

```
┌─────────────────────────────────────────────────────────┐
│                    Open Claude Code                      │
├─────────────────────────────────────────────────────────┤
│                     Agent Core                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   Tools     │  │   Context   │  │   History   │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
├─────────────────────────────────────────────────────────┤
│                   Backend Layer                          │
│  ┌─────────────────────┐  ┌─────────────────────┐      │
│  │  Anthropic Adapter  │  │   OpenAI Adapter    │      │
│  │  (/v1/messages)     │  │ (/chat/completions) │      │
│  └─────────────────────┘  └─────────────────────┘      │
├─────────────────────────────────────────────────────────┤
│                   Provider Config                        │
│         api_base, api_key, api_style, etc.              │
└─────────────────────────────────────────────────────────┘
```

## MCP Server Support

Extend capabilities with Model Context Protocol servers:

```toml
[[mcp_servers]]
name = "fetch_server"
transport = "stdio"
command = "uvx"
args = ["mcp-server-fetch"]
```

## Custom Tools

Add custom tools by placing Python files in `~/.vibe/tools/`:

```python
from vibe.core.tools.base import BaseTool, BaseToolConfig

class MyCustomTool(BaseTool):
    name = "my_tool"
    description = "Does something useful"

    async def execute(self, **kwargs):
        # Your implementation
        pass
```

## Acknowledgments

This project is based on [Mistral Vibe](https://github.com/mistralai/mistral-vibe) by Mistral AI. We thank them for open-sourcing their work, which made this project possible.

## License

Copyright 2025 Mistral AI (original work)
Copyright 2025 Open Claude Code Contributors (modifications)

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
