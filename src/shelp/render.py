"""Terminal rendering: rich markdown, optional pager, and the c/r/q key prompt."""

from __future__ import annotations

import contextlib
import os
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


def _getchar() -> str:
    """One keystroke without waiting for Enter (POSIX cbreak).

    rich has no getchar (the old `from rich.getchar import getchar` always
    hit ImportError and silently degraded the key prompt to line input);
    termios is all it takes. ISIG stays on, so Ctrl-C still interrupts.
    Windows uses msvcrt: getch() returns b'\\x03' for Ctrl-C without
    raising — key_prompt already treats that byte as quit.
    """
    if sys.platform == "win32":
        import msvcrt

        return msvcrt.getch().decode("utf-8", errors="ignore")

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        # TCSANOW, not setcbreak's default TCSAFLUSH: FLUSH would discard a
        # keystroke pressed between the prompt rendering and this switch —
        # `c` the instant the sheet finishes would vanish, chat would look
        # dead. TCSANOW keeps pending input readable across the switch.
        tty.setcbreak(fd, when=termios.TCSANOW)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def key_prompt() -> str | None:
    """Read one key: 'c' → chat, 'r' → regenerate, anything else → None.

    Single keystroke, no Enter needed. Returns None when stdin isn't a
    terminal (pipes, tests) or the key was q/Enter/Esc.
    """
    if not sys.stdin.isatty():
        return None
    con = _console()
    con.print("[dim]c) chat · r) regenerate · q) quit[/dim]", end=" ")
    try:
        while True:
            k = _getchar()
            if k in ("c", "C"):
                con.print()
                return "chat"
            if k in ("r", "R"):
                con.print()
                return "regen"
            if k in ("q", "Q", "\r", "\n", "\x03", "\x04", "\x1b") or not k:
                con.print()
                return None
    except Exception:  # noqa: BLE001 — no termios / odd fd → line input
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


def _stderr_is_ansi() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "") not in ("", "dumb")


@contextlib.contextmanager
def cooked_stdin():
    """Ensure stdin is in canonical+echo mode while the block runs.

    The zsh widget runs us with stdin on the real terminal but zle has left
    it in raw mode: input() would neither echo nor see Enter (raw passes
    \\r, and only ICRNL makes it a newline). Restore zle's modes on exit —
    it reasserts them anyway on the next prompt, but leave things as found.
    No-op when stdin isn't a tty or is already cooked.
    """
    try:
        import termios

        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001 — not a tty / no termios
        yield
        return
    lflag = attrs[3]
    if lflag & termios.ICANON and lflag & termios.ECHO:
        yield
        return
    cooked = list(attrs)
    # INLCR (left on by zle) is poison: it turns our Enter (LF) into CR
    # *after* ICRNL's translation, so the line never completes — input()
    # hangs with the keystroke echoed as ^M. Clear it, then make sure CR
    # and LF both terminate lines like a normal cooked terminal.
    cooked[0] &= ~(termios.INLCR | termios.IGNCR | termios.ISTRIP)
    cooked[0] |= termios.ICRNL
    cooked[3] |= (termios.ICANON | termios.ECHO | termios.ECHOE
                  | termios.ECHOK | termios.ISIG)
    termios.tcsetattr(fd, termios.TCSANOW, cooked)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)


def notice(msg: str) -> None:
    """stderr status line. Line placement is the caller's business — for the
    trigger flow, cmd_trigger gets output off the typed query line."""
    print(f"shelp: {msg}", file=sys.stderr, flush=True)


@contextlib.contextmanager
def ephemeral(msg: str):
    """notice that erases itself when the block finishes — the user only
    wanted the answer, and the wait line shouldn't survive into scrollback.
    Assumes the cursor already sits at column 0 (see cmd_trigger)."""
    if _stderr_is_ansi():
        print(f"shelp: {msg}", end="", file=sys.stderr, flush=True)
        try:
            yield
        finally:
            print("\r\x1b[2K", end="", file=sys.stderr, flush=True)
    else:
        print(f"shelp: {msg}", file=sys.stderr, flush=True)
        yield


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
