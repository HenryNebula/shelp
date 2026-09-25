"""Runtime configuration — all via environment.

One provider model: any OpenAI-compatible chat-completions endpoint.
Default is OpenRouter; point SHELP_BASE_URL at a local llama-server
(`http://localhost:30000/v1`) to go fully offline.
"""

import os

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
#: free OpenRouter tier (rate-limited); any OpenRouter slug works — free
#: variants end in `:free`
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def base_url() -> str:
    return os.environ.get("SHELP_BASE_URL", DEFAULT_BASE_URL)


def api_key() -> str:
    return (os.environ.get("SHELP_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or "none")  # local servers without auth


def model() -> str:
    return os.environ.get("SHELP_MODEL", DEFAULT_MODEL)


def chat_model() -> str:
    return os.environ.get("SHELP_CHAT_MODEL") or model()


def cache_root() -> str:
    if d := os.environ.get("SHELP_CACHE_DIR"):
        return d
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "shelp")


def sheets_dir() -> str:
    return os.path.join(cache_root(), "cheatsheets")


def chats_dir() -> str:
    return os.path.join(cache_root(), "chats")


def no_pager() -> bool:
    return bool(os.environ.get("SHELP_NO_PAGER"))
