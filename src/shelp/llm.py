"""The only module that talks to the network: OpenAI-compatible streaming.

OpenRouter by default; works with any /v1/chat/completions server
(llama-server, vllm, LM Studio, …). Tool-call deltas are accumulated across
stream chunks so callers get a plain (text, tool_calls) pair per turn.
"""

from __future__ import annotations

from . import config


class LLMError(RuntimeError):
    pass


def _client():
    from openai import OpenAI

    return OpenAI(
        base_url=config.base_url(),
        api_key=config.api_key(),
        default_headers={"X-Title": "shelp"},
        timeout=180,
    )


def stream(messages: list[dict], *, model: str | None = None,
           tools: list[dict] | None = None, on_delta=None,
           max_tokens: int = 1500) -> tuple[str, list[dict]]:
    """One chat-completions turn. Returns (text, tool_calls)."""
    kwargs: dict = {
        "model": model or config.model(),
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
    if "openrouter.ai" in config.base_url():
        # Some providers nondeterministically leak chain-of-thought into
        # delta.content; OpenRouter's unified `reasoning` switch avoids it.
        # extra_body: non-standard param → must bypass the SDK's typed kwargs.
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    try:
        resp = _client().chat.completions.create(**kwargs)
    except Exception as e:  # noqa: BLE001 — surface as our error type
        raise LLMError(f"{type(e).__name__}: {str(e)[:300]}") from e

    text_parts: list[str] = []
    calls: dict[int, dict] = {}
    try:
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text_parts.append(delta.content)
                if on_delta:
                    on_delta(delta.content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                slot = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if fn.name:
                        slot["name"] += fn.name
                    if fn.arguments:
                        slot["args"] += fn.arguments
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"stream interrupted: {str(e)[:300]}") from e

    tool_calls = [
        {"id": c["id"] or f"call_{i}", "type": "function",
         "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
        for i, c in sorted(calls.items())
    ]
    return "".join(text_parts), tool_calls
