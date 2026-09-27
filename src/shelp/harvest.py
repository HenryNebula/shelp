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
import sys
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

#: Verb-Noun shaped (`Get-ChildItem`) — a PowerShell cmdlet/alias. Not a
#: path or flag: the dash sits between letters and nothing else is odd.
CMDLET_RE = re.compile(r"[A-Za-z]+-[A-Za-z][A-Za-z0-9]*")


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


_PS_UTF8 = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def ps_exe() -> str | None:
    """Any available PowerShell — PS7 first (better encoding, faster)."""
    return shutil.which("pwsh") or shutil.which("powershell")


def _ps_run(script: str, timeout: float = 15) -> str | None:
    """Run a PowerShell snippet, forcing UTF-8 output: PS 5.1 otherwise
    emits the OEM codepage and non-ASCII help text decodes into mojibake."""
    exe = ps_exe()
    if exe is None:
        return None
    return _run([exe, "-NoProfile", "-Command", _PS_UTF8 + script],
                timeout=timeout, combine_stderr=True)


def _harvest_cmdlet(cmd: str) -> tuple[str, str, str]:
    """(help_text, whatis, flavor) from Get-Help/Get-Command, one PS spawn.
    Resolves aliases (`gci` → `Get-ChildItem`) so help is found."""
    script = (
        "$c = Get-Command " + cmd + " -ErrorAction SilentlyContinue\n"
        "if (-not $c) { return }\n"
        "$n = if ($c.CommandType -eq 'Alias') { $c.Definition } else { $c.Name }\n"
        "Write-Output \"whatis: $n [$($c.CommandType)]\"\n"
        "Get-Help $n -Full | Out-String -Width 110"
    )
    out = (_ps_run(script) or "").strip()
    if not out:
        return "", "", ""
    whatis = ""
    help_text = out
    if out.startswith("whatis: "):
        first, _, rest = out.partition("\n")
        whatis = first[len("whatis: "):].strip()
        help_text = rest.strip()
    flavor = "powershell" if out else ""
    return help_text, whatis, flavor


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


def wants_ps(cmd: str, hv: "Harvest | None" = None) -> bool:
    """True when a deep harvest could add anything: Verb-Noun names, or
    dashless aliases (`gci`) of cmdlets — anything man/--help can't cover."""
    if not ps_exe():
        return False
    if CMDLET_RE.fullmatch(cmd):
        return True
    if hv is not None:
        return hv.empty and not shutil.which(cmd)
    return not shutil.which(cmd)


def harvest(cmd: str, deep: bool = False) -> Harvest:
    """Gather documentation for *cmd*. Never raises for missing docs.

    `deep=True` (generation path only) may spawn PowerShell for cmdlet
    help. PS cold start is ~0.5-1s — far over the 0.2s cache-hit budget —
    so the cheap hash path stays PS-free and cmdlet sheets simply never
    auto-regenerate (use `r` / `--refresh`; `pinned` semantics unchanged).
    """
    man = _run(["man", cmd], timeout=8) or ""
    man = _strip_overstruck(man).strip()

    installed = shutil.which(cmd) is not None
    help_text = ""
    version = ""
    ps_flavor = ""
    whatis = ""

    # PowerShell cmdlets (`Get-ChildItem`) and their dashless aliases
    # (`gci`): man/--help/whatis know nothing about them.
    if deep and ps_exe() and (CMDLET_RE.fullmatch(cmd)
                              or (not installed and not man)):
        ps_help, whatis, ps_flavor = _harvest_cmdlet(cmd)
        if ps_help:
            man = ps_help   # Get-Help is the cmdlet man-page analog

    if installed:
        # combined streams: many tools print usage to stderr
        help_text = (_run([cmd, "--help"], timeout=6, combine_stderr=True) or "").strip()
        if len(help_text) < 40:
            alt = (_run([cmd, "-h"], timeout=6, combine_stderr=True) or "").strip()
            if len(alt) > len(help_text):
                help_text = alt
        if len(help_text) < 40 and sys.platform == "win32":
            # native exes answer /?, not --help: ipconfig, robocopy, netsh…
            alt = (_run([cmd, "/?"], timeout=6, combine_stderr=True) or "").strip()
            if len(alt) > len(help_text):
                help_text = alt
        ver = (_run([cmd, "--version"], timeout=6, combine_stderr=True) or "").strip()
        if ver and len(ver) < 200:
            version = ver.splitlines()[0]

    if not whatis:
        w = (_run(["whatis", cmd], timeout=5) or "").strip()
        if w.startswith(cmd):
            whatis = w.splitlines()[0]

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
        flavor=ps_flavor or flavor,
    )
