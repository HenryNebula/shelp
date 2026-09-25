"""Terminal rendering: rich markdown, optional pager, and the c/r/q key prompt."""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.markdown import Markdown

from . import config


def _console() -> Console:
    return Console()


def render_markdown(body: str, title: str | None = None) -> None:
    con = _console()
    md = Markdown(body, hyperlinks=False)
    is_tty = sys.stdout.isatty()
    too_tall = body.count("\n") > con.height - 4
    if is_tty and too_tall and not config.no_pager():
        with con.pager(styles=True):
            con.print(md)
    else:
        if title:
            con.print(title, style="dim")
        con.print(md)


def key_prompt() -> str | None:
    """Read one key: 'c' → chat, 'r' → regenerate, anything else → None.

    Returns None when stdin isn't a terminal (pipes, tests).
    """
    if not sys.stdin.isatty():
        return None
    con = _console()
    con.print("[dim]c) chat · r) regenerate · q) quit[/dim]", end=" ")
    try:
        from rich.getchar import getchar
        while True:
            k = getchar()
            if k in ("c", "C"):
                con.print()
                return "chat"
            if k in ("r", "R"):
                con.print()
                return "regen"
            if k in ("q", "Q", "\r", "\n", "\x03", "\x04", "\x1b"):
                con.print()
                return None
    except ImportError:
        pass
    # fallback: line-based input
    try:
        line = input().strip().lower()
    except EOFError:
        return None
    if line.startswith("c"):
        return "chat"
    if line.startswith("r"):
        return "regen"
    return None


def warn(msg: str) -> None:
    _console().print(f"[yellow]shelp:[/yellow] {msg}")


class LiveSheet:
    """Progressively renders markdown as generation deltas arrive (tty only).

    Turns the ~20s cold generation wait into content appearing from ~5s.
    No-ops on non-tty so pipes/tests are unaffected.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._live = None
        self._last_refresh = 0.0

    def start(self) -> None:
        if sys.stdout.isatty():
            from rich.live import Live

            self._live = Live(console=_console(), refresh_per_second=6,
                              vertical_overflow="visible")
            self._live.start()

    def add_delta(self, text: str) -> None:
        if not text or self._live is None:
            return
        self._parts.append(text)
        now = time.monotonic()
        if now - self._last_refresh >= 0.15:
            self._live.update(Markdown("".join(self._parts), hyperlinks=False))
            self._last_refresh = now

    def stop(self) -> None:
        if self._live is not None:
            self._live.update(Markdown("".join(self._parts), hyperlinks=False))
            self._live.stop()
            self._live = None

    def __enter__(self) -> "LiveSheet":
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False
