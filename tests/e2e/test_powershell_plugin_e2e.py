"""E2E: a real pwsh running the real installed PowerShell plugin.

Types `ls??` into an interactive PowerShell (fake HOME, seeded cache) and
checks the whole chain: Enter key handler → `shelp trigger` → rendered
sheet → single-key quit → fresh prompt. No LLM involved — the sheet comes
from the seeded cache, so this stays offline and deterministic. Runs
wherever pwsh exists (Linux CI via the powershell docker image, or Windows).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("pty")
pytest.importorskip("pyte", reason="dsr emulation for PSReadLine")
from pty_driver import Pty  # noqa: E402

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not shutil.which("pwsh"),
                                 reason="pwsh not installed")]

SHEET_BODY = ("## ls — list directory contents\n"
              "- `ls -l` — long listing\n")
PROMPT_MARK = b"SHLP-E2E> "
CRQ = "chat · r".encode()      # contiguous slice of the c/r/q prompt line
YOU = "you ›".encode()


def _fake_env(tmp_path: Path) -> dict[str, str]:
    """PYTHONPATH stub so the real `shelp` entry point never touches the
    network: sitecustomize patches llm.stream before cli.main runs."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sitecustomize.py").write_text(
        "from shelp import llm\n"
        "def _fake(messages, *, model=None, tools=None, on_delta=None,\n"
        "          max_tokens=1500):\n"
        "    if on_delta: on_delta('stub reply')\n"
        "    return 'stub reply', []\n"
        "llm.stream = _fake\n")
    return {"PYTHONPATH": str(stub)}


def _setup(tmp_path, monkeypatch):
    """Fake HOME + plugin installed into the profile pwsh actually loads
    ($HOME/.config/powershell/profile.ps1 on Linux), seeded `ls` sheet.
    XDG_CONFIG_HOME must be redirected too: pwsh prefers it over
    $HOME/.config, and CI runners point it at the real home's config."""
    home = tmp_path / "home"
    cache_dir = tmp_path / "cache"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("SHELP_PROFILE",
                       str(home / ".config" / "powershell" / "profile.ps1"))
    monkeypatch.setenv("SHELP_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("SHELP_NO_PAGER", "1")

    from shelp import cache
    from shelp.harvest import harvest
    from shelp.plugin import install
    install("powershell")
    profile = home / ".config" / "powershell" / "profile.ps1"
    profile.write_text(profile.read_text()
                       + "\nfunction global:prompt { 'SHLP-E2E> ' }\n")
    cache.save("ls", SHEET_BODY, harvest("ls").hash, "stub")

    venv_bin = str(Path(sys.executable).parent)      # provides `shelp`
    return {"PATH": venv_bin + os.pathsep + os.environ["PATH"]}


def test_pwsh_cached_sheet_round_trip(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    with Pty(["pwsh", "-NoLogo"], ctty=True, dsr=True, env=env) as p:
        p.wait_for(PROMPT_MARK)
        p.send(b"ls??\r")                     # type, Enter → key handler
        p.wait_for(CRQ, timeout=30)           # sheet + key prompt
        p.send(b"q")                          # single-key quit
        p.wait_for(PROMPT_MARK, occurrence=2, timeout=20)   # fresh prompt
        p.send(b"exit\r")
        rc_code = p.finish()

    assert rc_code == 0
    assert b"ls -l" in p.out                  # sheet rendered
    assert b"not recognized" not in p.out     # never hit command-not-found


def test_pwsh_bare_double_question_into_chat(tmp_path, monkeypatch):
    """`??` bare — general chat. PS 7 would parse `??` as the null-coalescing
    operator; the Enter handler sees the raw buffer first, so it must work
    regardless."""
    env = _setup(tmp_path, monkeypatch)
    env.update(_fake_env(tmp_path))
    with Pty(["pwsh", "-NoLogo"], ctty=True, dsr=True, env=env) as p:
        p.wait_for(PROMPT_MARK)
        p.send(b"?? how do I pipe\r")
        p.wait_for(b"shelp chat", timeout=30)
        p.wait_for(YOU)
        p.send(b"hello\n")
        p.wait_for(b"stub reply")
        p.wait_for(YOU, occurrence=2, timeout=20)
        p.send(b"q\n")
        p.wait_for(b"bye.")
        p.wait_for(PROMPT_MARK, occurrence=2, timeout=20)
        p.send(b"exit\r")
        rc_code = p.finish()
    assert rc_code == 0


def test_pwsh_plain_commands_survive_resourcing(tmp_path, monkeypatch):
    """Re-dot-sourcing the profile must not break plain Enter — the PS
    equivalent of the zsh re-source recursion bug."""
    env = _setup(tmp_path, monkeypatch)
    with Pty(["pwsh", "-NoLogo"], ctty=True, dsr=True, env=env) as p:
        p.wait_for(PROMPT_MARK)
        p.send(b"echo one\r")
        p.wait_for(b"one")
        p.send(b". $PROFILE\r")               # the re-source repro
        p.wait_for(PROMPT_MARK, occurrence=2, timeout=10)
        p.send(b"echo two\r")
        p.wait_for(b"two", timeout=10)        # plain Enter still accepts
        p.send(b"exit\r")
        rc_code = p.finish()
    assert rc_code == 0
