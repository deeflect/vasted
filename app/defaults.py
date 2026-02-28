from __future__ import annotations

APP_NAME = "vasted"
SCHEMA_VERSION = 1

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 4318
DEFAULT_IDLE_TIMEOUT_MINUTES = 30

DEFAULT_VAST_BASE_URL = "https://console.vast.ai/api/v0"
# Template image that autostarts llama-server and is configured by env vars.
DEFAULT_LLAMA_CPP_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda-b5507"
