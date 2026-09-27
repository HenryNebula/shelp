"""Pty driver for shelp's e2e tests.

Spawns the real CLI (or a real shell) with stdin/stdout/stderr on a pty
slave and lets tests wait for byte markers in the terminal stream before
sending keystrokes. Raw bytes are the point — line-placement, glue and
erase-sequence bugs only exist in the actual terminal stream, never in
capsys.

Two hard-won rules (each cost real debugging time on 2026-09-27):

- Never inject the typed query into stdin for flows that later READ stdin
  (`cmd??` key prompt, chat): in canonical mode it joins the first
  input() line and silently changes what the prompt returns. Only
  `cmd?`/short flows never read stdin, so only those may simulate the
  widget's mid-line cursor by writing the query to the master.
- Reads after child exit raise EIO, and every wait needs a deadline —
  unguarded loops are how harnesses hang forever.

Rule three (2026-09-27 again, PowerShell): PSReadLine sends `\x1b[6n`
cursor-position queries and stalls until something answers them — a dumb
pty master never does. `dsr=True` emulates a terminal's side of the
conversation: every chunk is fed through a pyte screen and each DSR gets a
position report. zsh/zle never asks, so POSIX tests keep dsr=False.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time

_DSR = re.compile(rb"\x1b\[6n")


class Pty:
    """A child process on a pseudo-terminal, with marker-based waits."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None,
                 cols: int = 80, rows: int = 24, ctty: bool = False,
                 dsr: bool = False):
        master, slave = pty.openpty()
        # Give rich a real terminal size — openpty defaults to 0x0.
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
        full_env = dict(os.environ, TERM="xterm-256color")
        if env:
            full_env.update(env)
        self._dsr = dsr
        self._vt = None
        if dsr:
            import pyte  # dev dependency; POSIX e2e only

            self._vt = pyte.Screen(cols, rows)
            self._vts = pyte.Stream(self._vt)
        if ctty:
            # A real controlling terminal: needed when testing flows that
            # reopen /dev/tty (the zsh widget's stdin redirect). The child
            # becomes a session leader and claims the pty as its ctty.
            def _claim_tty():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                os.dup2(slave, 0)
                os.dup2(slave, 1)
                os.dup2(slave, 2)
                if slave > 2:
                    os.close(slave)
            self.proc = subprocess.Popen(argv, preexec_fn=_claim_tty,
                                         env=full_env, close_fds=True)
        else:
            self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave,
                                         stderr=slave, start_new_session=True,
                                         env=full_env)
        os.close(slave)
        self._master = master
        self.out = b""

    def _ingest(self, chunk: bytes) -> None:
        """Record output; under dsr, keep the pyte screen fed and answer
        PSReadLine's cursor queries with the emulated position."""
        self.out += chunk
        if self._vt is not None:
            self._vts.feed(chunk.decode("utf-8", errors="replace"))
            for _ in _DSR.findall(chunk):
                reply = f"\x1b[{self._vt.cursor.y + 1};{self._vt.cursor.x + 1}R"
                os.write(self._master, reply.encode())

    def send(self, data: bytes) -> None:
        os.write(self._master, data)

    def wait_for(self, marker: bytes, timeout: float = 15.0,
                 occurrence: int = 1) -> None:
        """Block until `marker` has been seen `occurrence` times."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self._master], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(self._master, 4096)
            except OSError:  # EIO: child exited, stream drained
                break
            if not chunk:
                if self.proc.poll() is not None:
                    break
                continue
            self._ingest(chunk)
            if self.out.count(marker) >= occurrence:
                return
        if self.out.count(marker) >= occurrence:
            return
        raise AssertionError(
            f"timed out after {timeout}s waiting for {marker!r} "
            f"(x{occurrence}); stream tail: {self.out[-400:]!r}")

    def finish(self, timeout: float = 10.0) -> int:
        """Wait for a clean exit, draining the stream while doing so —
        self.out only grows while someone reads, so output printed after
        the last wait_for (final lines, exit messages) must be collected
        here or it vanishes into the kernel buffer."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self._master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(self._master, 4096)
                except OSError:  # EIO: stream fully drained
                    break
                if not chunk:
                    break
                self._ingest(chunk)
                continue
            if self.proc.poll() is not None:
                # give stragglers one more beat, then stop
                r, _, _ = select.select([self._master], [], [], 0.3)
                if not r:
                    break
        try:
            return self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise AssertionError(
                f"child did not exit within {timeout}s; "
                f"stream tail: {self.out[-400:]!r}") from None

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        try:
            os.close(self._master)
        except OSError:
            pass

    def __enter__(self) -> "Pty":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False
