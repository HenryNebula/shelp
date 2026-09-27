"""E2E: a real zsh running the real installed plugin, end to end.

Types `ls??` into an interactive zsh (fake HOME, seeded cache) and checks
the whole chain: accept-line widget → `shelp trigger` → rendered sheet →
single-key quit → prompt returns. No LLM involved — the sheet comes from
the seeded cache, so this stays offline and deterministic.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("pty")
from pty_driver import Pty  # noqa: E402

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not shutil.which("zsh"),
                                 reason="zsh not installed")]

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


def test_zsh_widget_cached_sheet_round_trip(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    cache_dir = tmp_path / "cache"
    home.mkdir(); xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("SHELP_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("SHELP_NO_PAGER", "1")

    from shelp import cache
    from shelp.harvest import harvest
    from shelp.plugin import install
    install("zsh")                                   # real plugin, fake HOME
    rc = home / ".zshrc"
    rc.write_text(rc.read_text() + "\nPROMPT='SHLP-E2E> '\n")
    cache.save("ls", SHEET_BODY, harvest("ls").hash, "stub")

    venv_bin = str(Path(sys.executable).parent)      # provides `shelp`
    # ctty=True: the widget reopens /dev/tty for shelp's stdin — that only
    # works if zsh actually owns a controlling terminal.
    with Pty(["zsh", "-i"], ctty=True,
             env={"PATH": venv_bin + os.pathsep + os.environ["PATH"]}) as p:
        p.wait_for(PROMPT_MARK)
        p.send(b"ls??\r")                            # type, Enter → widget
        p.wait_for(CRQ)                              # sheet + key prompt
        p.send(b"q")                                 # single-key quit
        p.wait_for(PROMPT_MARK, occurrence=2, timeout=20)  # prompt back
        p.send(b"exit\r")
        rc_code = p.finish()

    assert rc_code == 0
    assert b"ls??\r\n" in p.out        # query kept its own line…
    assert b"ls -l" in p.out           # …sheet rendered below it


def test_zsh_widget_cached_sheet_into_chat(tmp_path, monkeypatch):
    """The user's exact path (2026-09-27): cached sheet, `c` into chat, ask
    a question. zle leaves INLCR on — without clearing it, Enter (LF) turns
    into CR after ICRNL's translation and the question line never completes."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir(); xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("SHELP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SHELP_NO_PAGER", "1")

    from shelp import cache
    from shelp.harvest import harvest
    from shelp.plugin import install
    install("zsh")
    rc = home / ".zshrc"
    rc.write_text(rc.read_text() + "\nPROMPT='SHLP-E2E> '\n")
    cache.save("ls", SHEET_BODY, harvest("ls").hash, "stub")

    venv_bin = str(Path(sys.executable).parent)
    env = {"PATH": venv_bin + os.pathsep + os.environ["PATH"]}
    env.update(_fake_env(tmp_path))
    with Pty(["zsh", "-i"], ctty=True, env=env) as p:
        p.wait_for(PROMPT_MARK)
        p.send(b"ls??\r")
        p.wait_for(CRQ)
        p.send(b"c")                       # single keystroke into chat
        p.wait_for(b"shelp chat")
        p.wait_for(YOU)
        p.send(b"hello\n")                 # must complete despite zle's INLCR
        p.wait_for(b"stub reply")
        p.wait_for(YOU, occurrence=2, timeout=20)
        p.send(b"q\n")
        p.wait_for(b"bye.")
        p.send(b"exit\r")
        rc_code = p.finish()
    assert rc_code == 0


def test_zsh_plugin_survives_resourcing(tmp_path, monkeypatch):
    """`source ~/.zshrc` twice must not chain the widget onto itself —
    that made every plain command die with "No such widget"."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir(); xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("SHELP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SHELP_NO_PAGER", "1")

    from shelp.plugin import install
    install("zsh")
    rc = home / ".zshrc"
    rc.write_text(rc.read_text() + "\nPROMPT='SHLP-E2E> '\n")

    venv_bin = str(Path(sys.executable).parent)
    with Pty(["zsh", "-i"], ctty=True,
             env={"PATH": venv_bin + os.pathsep + os.environ["PATH"]}) as p:
        p.wait_for(PROMPT_MARK)
        p.send(b"echo one\r")
        p.wait_for(b"one")
        p.send(b"source ~/.zshrc\r")     # the user's exact repro
        p.wait_for(PROMPT_MARK, occurrence=2, timeout=10)
        p.send(b"echo two\r")
        p.wait_for(b"two", timeout=10)   # plain commands still work
        assert b"No such widget" not in p.out
        p.send(b"exit\r")
        rc_code = p.finish()
    assert rc_code == 0
