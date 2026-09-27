"""E2E: the real CLI on a pty, LLM stubbed in-process — fully offline.

These encode the UX invariants fixed on 2026-09-27:

- output starts below the typed query line (never glued onto it),
- the generating notice sits on its own line and erases itself,
- the c/r/q prompt reacts to a single keystroke (no Enter),
- one failed model call doesn't kill the chat or doom-loop the saved
  session.
"""

from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("pty")
from pty_driver import Pty  # noqa: E402

pytestmark = pytest.mark.e2e

SHORT_BODY = ("ls — list directory contents\n"
              "ls -l — long listing\n"
              "ls -a — all files")
SHEET_BODY = ("## ls — list directory contents\n"
              "- `ls -l` — long listing\n"
              "- `ls -a` — all files")
SHEET_CHUNKS = ["## ls — list directory contents\n",
                "- `ls -l` — long listing\n",
                "- `ls -a` — all files\n"]
REPLY = "Use `ls -l` for long listings."

TAGLINE = "ls —".encode()      # "ls —" (first bytes of the rendered tagline)
YOU = "you ›".encode()         # "you ›" chat prompt
CRQ = "chat · r".encode()      # c/r/q line (contiguous inside rich styling)
ERASE = b"\r\x1b[2K"

# Child bootstrap: patch generate/llm before cli.main so no test touches
# the network. Behavior switches via SHELP_E2E_MODE ("ok" | "fail").
BOOTSTRAP = '''\
import os, sys, time
from shelp import cli, generate, llm

SHORT_BODY = __SHORT_BODY__
SHEET_BODY = __SHEET_BODY__
SHEET_CHUNKS = __SHEET_CHUNKS__
REPLY = __REPLY__
MODE = os.environ.get("SHELP_E2E_MODE", "ok")

generate.generate_short = lambda cmd, hv: SHORT_BODY

def fake_sheet(cmd, hv, on_delta=None):
    for chunk in SHEET_CHUNKS:
        if on_delta:
            on_delta(chunk)
            time.sleep(0.02)
    return SHEET_BODY

def fake_stream(messages, *, model=None, tools=None, on_delta=None,
                max_tokens=1500):
    if MODE == "fail":
        raise llm.LLMError("AuthenticationError: Error code: 401")
    if on_delta:
        on_delta(REPLY)
    return REPLY, []

generate.generate_sheet = fake_sheet
llm.stream = fake_stream
sys.exit(cli.main(sys.argv[1:]))
'''


def _bootstrap() -> str:
    return (BOOTSTRAP
            .replace("__SHORT_BODY__", repr(SHORT_BODY))
            .replace("__SHEET_BODY__", repr(SHEET_BODY))
            .replace("__SHEET_CHUNKS__", repr(SHEET_CHUNKS))
            .replace("__REPLY__", repr(REPLY)))


def test_short_cached_renders_below_query(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELP_CACHE_DIR", str(tmp_path))
    from shelp import cache
    from shelp.harvest import harvest
    cache.save("ls", SHORT_BODY, harvest("ls").hash, "stub", suffix=".short")

    p = Pty([sys.executable, "-c", _bootstrap(), "trigger", "ls", "--short"],
            env={"SHELP_CACHE_DIR": str(tmp_path), "SHELP_NO_PAGER": "1",
                 "SHELP_E2E_MODE": "ok"})
    with p:
        p.send(b"% ls?")  # typed query, cursor left at its end (widget-like)
        p.wait_for(TAGLINE)
        rc = p.finish()
    assert p.out.startswith(b"% ls?")
    assert b"ls?shelp:" not in p.out        # nothing glued onto the query
    assert b"generating TL;DR" not in p.out  # cache hit: no notice at all
    assert TAGLINE in p.out
    assert rc == 0


def test_short_generating_notice_own_line_and_ephemeral(tmp_path):
    with Pty([sys.executable, "-c", _bootstrap(), "trigger", "ls", "--short"],
             env={"SHELP_CACHE_DIR": str(tmp_path), "SHELP_NO_PAGER": "1",
                  "SHELP_E2E_MODE": "ok"}) as p:
        p.send(b"% ls?")
        p.wait_for(b"generating TL;DR")
        p.wait_for(TAGLINE)
        rc = p.finish()
    assert b"ls?\r\nshelp: generating TL;DR" in p.out  # notice below query
    assert ERASE in p.out                             # …and erased after
    assert p.out.find(ERASE) < p.out.find(TAGLINE)    # before the results
    assert rc == 0


def test_sheet_renders_then_single_key_quit(tmp_path):
    with Pty([sys.executable, "-c", _bootstrap(), "trigger", "ls"],
             env={"SHELP_CACHE_DIR": str(tmp_path), "SHELP_NO_PAGER": "1",
                  "SHELP_E2E_MODE": "ok"}) as p:
        p.wait_for(CRQ)
        p.send(b"q")  # single keystroke, no Enter
        rc = p.finish()
    assert b"ls -l" in p.out
    assert rc == 0


def test_chat_via_single_c_then_reply(tmp_path):
    with Pty([sys.executable, "-c", _bootstrap(), "trigger", "ls"],
             env={"SHELP_CACHE_DIR": str(tmp_path), "SHELP_NO_PAGER": "1",
                  "SHELP_E2E_MODE": "ok"}) as p:
        p.wait_for(CRQ)
        p.send(b"c")                          # single keystroke opens chat
        p.wait_for("shelp chat — ls".encode())
        p.wait_for(YOU)
        p.send(b"how do I list long?\n")      # chat prompt is line-based
        # the reply streams as markdown: backticks are consumed by styling,
        # so match a plain slice rather than the raw stub text
        p.wait_for(b"for long listings")
        p.wait_for(YOU, occurrence=2)
        p.send(b"q\n")
        rc = p.finish()
    assert rc == 0
    assert b"bye." in p.out
    msgs = json.loads((tmp_path / "chats" / "ls.json").read_text())
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == REPLY


def test_chat_llm_failure_returns_to_prompt(tmp_path):
    with Pty([sys.executable, "-c", _bootstrap(), "trigger", "ls"],
             env={"SHELP_CACHE_DIR": str(tmp_path), "SHELP_NO_PAGER": "1",
                  "SHELP_E2E_MODE": "fail"}) as p:
        p.wait_for(CRQ)
        p.send(b"c")
        p.wait_for(YOU)
        p.send(b"hello?\n")
        p.wait_for(b"401")               # failure is reported…
        p.wait_for(YOU, occurrence=2)    # …but chat survives at the prompt
        p.send(b"q\n")
        rc = p.finish()
    assert rc == 0
    msgs = json.loads((tmp_path / "chats" / "ls.json").read_text())
    assert msgs[-1]["role"] != "user"    # no reply-less turn saved
