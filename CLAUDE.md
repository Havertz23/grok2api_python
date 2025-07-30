# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Grok API proxy service that provides OpenAI-compatible endpoints for accessing Grok models through reverse engineering. The project supports multiple languages (Python primary with Node.js auxiliary) and deployment methods.

## Architecture

### Core Components
- **app.py**: Main Flask application serving the API endpoints
- **templates/**: HTML templates for web management interface
- **package.json + index.js**: Node.js auxiliary service
- **pyproject.toml**: Python project configuration with UV package management

### API Structure
The service exposes OpenAI-compatible endpoints:
- `/v1/models` - List available Grok models
- `/v1/chat/completions` - Chat completions with streaming support
- Token management endpoints (`/add/token`, `/delete/token`, `/get/tokens`)
- Management interface at `/manager`

### Supported Models
- grok-2, grok-2-imageGen, grok-2-search
- grok-3, grok-3-search, grok-3-imageGen  
- grok-3-deepsearch, grok-3-deepersearch
- grok-3-reasoning

## Development Commands

### Python (Primary)
Using UV package manager (recommended):
```bash
# Setup environment
uv venv
uv sync

# Run the Flask application
uv run python app.py

# Install new dependencies
uv add package_name

# Development dependencies
uv add --dev pytest black flake8 mypy

# Run tests (if available)
uv run pytest

# Code formatting
uv run black .

# Type checking
uv run mypy .

# Linting
uv run flake8 .
```

### Node.js (Auxiliary)
```bash
# Install dependencies
npm install

# Run Node.js service
npm start
```

## Environment Configuration

Copy `env.example` to `.env` and configure:
- `SSO`: Grok website SSO cookies (comma-separated for multiple accounts)
- `API_KEY`: Custom authentication key (default: sk-123456)
- `PORT`: Service port (default: 3000 for Node, 5200 for Python)
- `CF_CLEARANCE`: Cloudflare clearance token for bypassing protection
- `PROXY`: HTTP/SOCKS5 proxy settings
- `PICGO_KEY` or `TUMY_KEY`: Image hosting keys for streaming image generation

## Key Features
- Automatic Cloudflare shield bypass
- Multi-account SSO token rotation
- Streaming support for all models
- Image generation and recognition
- Search capabilities with deep search
- OpenAI format compatibility
- Web management interface

## Important Notes
- The service uses reverse engineering to access Grok API
- Requires valid SSO cookies from grok.com
- IP restrictions may apply (check if IP is rate-limited)
- Context automatically converts to file upload when approaching 40k tokens
- Historical images in conversations are replaced with placeholders