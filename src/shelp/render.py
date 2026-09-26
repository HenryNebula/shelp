"""Terminal rendering: rich markdown, optional pager, and the c/r/q key prompt."""

from __future__ import annotations

import re
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

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


_BULLET = re.compile(r"^[-*]\s+")


def short_lines(body: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Split a TL;DR body into (tagline, [(command, description), …]).

    The bodies are line lists, not markdown documents, and models drift:
    backticked commands, `- ` bullets, trailing two-space hard breaks, a
    missing blank line after the tagline. Normalize all of that here.
    """
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    tagline: str | None = None
    entries: list[tuple[str, str]] = []
    for ln in lines:
        ln = _BULLET.sub("", ln)
        cmd, sep, desc = ln.partition(" — ")
        cmd = cmd.strip("`").strip()
        desc = desc.strip()
        if sep and tagline is None and re.fullmatch(r"\S+", cmd):
            tagline = f"{cmd} — {desc}" if desc else cmd
            continue
        entries.append((cmd, desc))
    return tagline, entries


def render_short(body: str, hint: str | None = None) -> None:
    """Render a TL;DR sheet: commands in an aligned column, descs folding.

    Deliberately not rich Markdown — these bodies have no markdown
    structure, and Markdown would soft-wrap consecutive lines into a
    single paragraph. A two-column grid keeps long descriptions wrapping
    under themselves instead of under the next command.
    """
    con = _console()
    tagline, entries = short_lines(body)
    if tagline:
        con.print(Text(tagline, style="bold"))
        if entries:
            con.print()
    if entries:
        grid = Table.grid(expand=True, pad_edge=False)
        grid.add_column(no_wrap=True, overflow="ignore", style="cyan")
        grid.add_column(overflow="fold")
        for cmd, desc in entries:
            grid.add_row(cmd, desc)
        con.print(grid)
    if hint:
        con.print()
        con.print(Text(hint, style="dim"))


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
