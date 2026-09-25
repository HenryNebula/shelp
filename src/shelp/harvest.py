"""Collect local documentation for a command: man page, --help, whatis.

Everything is harvested host-side so the generated sheet reflects the
*installed* flavor and version, and so the cache key is stable.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

MAN_LIMIT = 15_000
HELP_LIMIT = 5_000

#: locale pinned for stable hashes + English docs
ENV = {
    **os.environ,
    "LC_ALL": "C",
    "MANPAGER": "cat",
    "PAGER": "cat",
    "MANWIDTH": "110",
}

CMD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+@-]*")


class InvalidCommand(ValueError):
    pass


def validate_cmd(cmd: str) -> str:
    cmd = cmd.strip()
    if not CMD_RE.fullmatch(cmd):
        raise InvalidCommand(f"not a command name: {cmd!r}")
    return cmd


def _strip_overstruck(text: str) -> str:
    """Undo man's overstrike formatting: `X\\x08X` (bold), `_\\x08C` (underline)."""
    text = re.sub(r".\x08", "", text)
    return text.replace("\x08", "")


def _run(argv: list[str], timeout: float, combine_stderr: bool = False) -> str | None:
    try:
        proc = subprocess.run(
            argv,
            env=ENV,
            timeout=timeout,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = proc.stdout or ""
    if combine_stderr:
        out += (proc.stderr or "") if proc.stderr else ""
    return out


@dataclass
class Harvest:
    cmd: str
    man: str
    help_text: str
    whatis: str
    version: str
    flavor: str

    @property
    def hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.man[:MAN_LIMIT].encode())
        h.update(b"\x00")
        h.update(self.help_text[:HELP_LIMIT].encode())
        return h.hexdigest()[:16]

    @property
    def empty(self) -> bool:
        return not (self.man.strip() or self.help_text.strip())

    def doc_blob(self) -> str:
        parts = []
        if self.whatis:
            parts.append(f"whatis: {self.whatis}")
        if self.man:
            parts.append(f"<man page (truncated)>\n{self.man[:MAN_LIMIT]}\n</man>")
        if self.help_text:
            parts.append(f"<--help output (truncated)>\n{self.help_text[:HELP_LIMIT]}\n</help>")
        return "\n\n".join(parts) if parts else "(no local documentation found)"


def harvest(cmd: str) -> Harvest:
    """Gather documentation for *cmd*. Never raises for missing docs."""
    man = _run(["man", cmd], timeout=8) or ""
    man = _strip_overstruck(man).strip()

    installed = shutil.which(cmd) is not None
    help_text = ""
    version = ""
    if installed:
        # combined streams: many tools print usage to stderr
        help_text = (_run([cmd, "--help"], timeout=6, combine_stderr=True) or "").strip()
        if len(help_text) < 40:
            alt = (_run([cmd, "-h"], timeout=6, combine_stderr=True) or "").strip()
            if len(alt) > len(help_text):
                help_text = alt
        ver = (_run([cmd, "--version"], timeout=6, combine_stderr=True) or "").strip()
        if ver and len(ver) < 200:
            version = ver.splitlines()[0]

    whatis = (_run(["whatis", cmd], timeout=5) or "").strip()
    if whatis.startswith(cmd):
        whatis = whatis.splitlines()[0]
    else:
        whatis = ""

    blob = man + help_text
    flavor = ""
    for marker, name in (
        ("bsdtar", "bsdtar/libarchive"),
        ("libarchive", "bsdtar/libarchive"),
        ("BusyBox", "busybox"),
        ("GNU ", "GNU"),
    ):
        if marker in blob:
            flavor = name
            break

    return Harvest(
        cmd=cmd,
        man=man,
        help_text=help_text[:HELP_LIMIT],
        whatis=whatis,
        version=version,
        flavor=flavor,
    )
