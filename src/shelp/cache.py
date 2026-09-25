"""Sheet + chat-state storage under the cache root.

Sheets carry a small hand-parsed front-matter block:

    ---
    cmd: unzip
    manhash: ab12cd...
    generated: 2026-09-24
    ...
    ---

`pinned: true` opts a hand-edited sheet out of auto-regeneration.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path

from . import config

_KEYS = ("cmd", "version", "flavor", "manhash", "generated", "model", "pinned")


def _parse(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            return meta, text[end + 5 :].lstrip("\n")
    return meta, text


def path_for(cmd: str, suffix: str = "") -> Path:
    return Path(config.sheets_dir()) / f"{cmd}{suffix}.md"


def load(cmd: str, suffix: str = "") -> tuple[dict, str] | None:
    p = path_for(cmd, suffix)
    try:
        return _parse(p.read_text(encoding="utf-8"))
    except OSError:
        return None


def save(cmd: str, body: str, manhash: str, model: str,
         version: str = "", flavor: str = "", suffix: str = "") -> dict:
    p = path_for(cmd, suffix)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "cmd": cmd,
        "version": version,
        "flavor": flavor,
        "manhash": manhash,
        "generated": _dt.date.today().isoformat(),
        "model": model,
        "pinned": "false",
    }
    fm = "---\n" + "\n".join(f"{k}: {meta[k]}" for k in _KEYS) + "\n---\n"
    p.write_text(fm + body.strip() + "\n", encoding="utf-8")
    return meta


def is_pinned(meta: dict) -> bool:
    return str(meta.get("pinned", "")).lower() == "true"


def list_sheets() -> list[tuple[str, dict, float]]:
    out: list[tuple[str, dict, float]] = []
    d = Path(config.sheets_dir())
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        if p.stem.endswith(".short"):  # TL;DRs (`cmd?`) — not listed separately
            continue
        meta, _ = _parse(p.read_text(encoding="utf-8", errors="replace"))
        out.append((p.stem, meta, p.stat().st_mtime))
    return out


def prune(older_than_days: int | None = None, all_: bool = False) -> list[str]:
    removed: list[str] = []
    cutoff = (time.time() - older_than_days * 86400) if older_than_days is not None else None
    if all_:
        for p in Path(config.sheets_dir()).glob("*.md"):
            p.unlink(missing_ok=True)
            removed.append(p.stem)
    else:
        for name, _meta, mtime in list_sheets():
            if cutoff is not None and mtime < cutoff:
                path_for(name).unlink(missing_ok=True)
                removed.append(name)
    if all_:
        chats = Path(config.chats_dir())
        if chats.is_dir():
            for p in chats.iterdir():
                p.unlink(missing_ok=True)
                removed.append(f"chat:{p.stem}")
    return removed


# --- per-command chat sessions (our own agent loop, JSON message history) ----

def chat_session_path(cmd: str) -> Path:
    return Path(config.chats_dir()) / f"{cmd}.json"


def load_chat(cmd: str) -> list[dict] | None:
    try:
        data = json.loads(chat_session_path(cmd).read_text(encoding="utf-8"))
        return data if isinstance(data, list) and data else None
    except (OSError, ValueError):
        return None


def save_chat(cmd: str, messages: list[dict]) -> None:
    p = chat_session_path(cmd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(messages, ensure_ascii=False, indent=1),
                 encoding="utf-8")


def clear_chat(cmd: str) -> None:
    chat_session_path(cmd).unlink(missing_ok=True)
