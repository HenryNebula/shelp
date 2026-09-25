"""shelp CLI — `cmd??` cheat sheets and chat for shell commands."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import __version__, cache, chat, config, generate, render
from .harvest import CMD_RE, InvalidCommand, harvest, validate_cmd
from .plugin import install as install_plugin

SUBCOMMANDS = {"show", "trigger", "chat", "warm", "list", "prune", "init", "doctor"}


def _die(msg: str) -> None:
    print(f"shelp: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _validate_or_die(cmd: str) -> str:
    try:
        return validate_cmd(cmd)
    except InvalidCommand as e:
        _die(str(e))
    raise AssertionError  # unreachable


def ensure_sheet(cmd: str, refresh: bool = False, stream: bool = False):
    """Return (harvest, body, streamed). body is None only on total failure."""
    hv = harvest(cmd)
    cached = cache.load(cmd)
    needs = (
        refresh
        or cached is None
        or (
            not cache.is_pinned(cached[0])
            and not hv.empty
            and cached[0].get("manhash") != hv.hash
        )
    )
    if not needs:
        return hv, cached[1], False
    print(f"shelp: generating sheet for {cmd} (first time)…", file=sys.stderr)
    streamed = False
    try:
        if stream and sys.stdout.isatty():
            live = render.LiveSheet()
            live.start()
            try:
                body = generate.generate_sheet(cmd, hv, on_delta=live.add_delta)
            finally:
                live.stop()
            streamed = True
        else:
            body = generate.generate_sheet(cmd, hv)
    except generate.GenerateError as e:
        if cached and not refresh:
            render.warn(f"regeneration failed ({e}) — showing the cached sheet")
            return hv, cached[1], False
        raise
    cache.save(cmd, body, hv.hash, config.model(),
               version=hv.version, flavor=hv.flavor)
    return hv, body, streamed


def _interactive_loop(cmd: str, body: str, already_rendered: bool = False) -> None:
    """Render + c/r/q loop. 'chat' drops into a chat session, then returns."""
    while True:
        if not already_rendered:
            render.render_markdown(body)
        action = render.key_prompt()
        if action == "chat":
            chat.chat_session(cmd, None, sheet=body)
            return
        elif action == "regen":
            hv = harvest(cmd)
            try:
                live = render.LiveSheet()
                live.start()
                try:
                    body = generate.generate_sheet(cmd, hv, on_delta=live.add_delta)
                finally:
                    live.stop()
                cache.save(cmd, body, hv.hash, config.model(),
                           version=hv.version, flavor=hv.flavor)
            except generate.GenerateError as e:
                render.warn(f"regeneration failed: {e}")
            already_rendered = True
        else:
            return


def ensure_short(cmd: str, refresh: bool = False):
    """TL;DR variant (`cmd?`): one-liner + 5 common invocations, ≤8 lines."""
    hv = harvest(cmd)
    cached = cache.load(cmd, suffix=".short")
    needs = (
        refresh
        or cached is None
        or (
            not cache.is_pinned(cached[0])
            and not hv.empty
            and cached[0].get("manhash") != hv.hash
        )
    )
    if not needs:
        return hv, cached[1]
    print(f"shelp: generating TL;DR for {cmd}…", file=sys.stderr)
    try:
        body = generate.generate_short(cmd, hv)
    except generate.GenerateError as e:
        if cached and not refresh:
            render.warn(f"regeneration failed ({e}) — showing the cached TL;DR")
            return hv, cached[1]
        raise
    cache.save(cmd, body, hv.hash, config.model(),
               version=hv.version, flavor=hv.flavor, suffix=".short")
    return hv, body


def cmd_show(args) -> int:
    cmd = _validate_or_die(args.cmd)
    try:
        if args.short:
            _hv, body = ensure_short(cmd, refresh=args.refresh)
            if sys.stdout.isatty():
                render.render_markdown(body)
                render._console().print(f"[dim]full sheet: {cmd}??[/dim]")
            else:
                print(body)
            return 0
        _hv, body, streamed = ensure_sheet(cmd, refresh=args.refresh,
                                           stream=not args.raw)
    except generate.GenerateError as e:
        _die(f"generation failed: {e} — try `shelp doctor`")
    if args.raw or not sys.stdout.isatty():
        print(body)
        return 0
    _interactive_loop(cmd, body, already_rendered=streamed)
    return 0


def cmd_trigger(args) -> int:
    base = (args.cmd or "").strip()
    if not base:  # bare `??`/`?` → general chat
        chat.chat_session(None, " ".join(args.words).strip() or None)
        return 0
    cmd = _validate_or_die(base)
    question = " ".join(args.words).strip() or None
    if args.short:  # `cmd?` — TL;DR, optionally topped by a tight answer
        try:
            hv, body = ensure_short(cmd)
        except generate.GenerateError as e:
            _die(f"generation failed: {e} — try `shelp doctor`")
        if question:
            try:
                answer = generate.generate_answer(cmd, hv, body, question)
                print(answer)
                print()
            except generate.GenerateError as e:
                render.warn(f"could not answer ({e})")
        if sys.stdout.isatty():
            render.render_markdown(body)
            render._console().print(f"[dim]full sheet: {cmd}??[/dim]")
        else:
            print(body)
        return 0
    tty = sys.stdout.isatty()

    if question:  # answer first, from local docs (+ cached sheet if any)
        hv = harvest(cmd)
        cached = cache.load(cmd)
        try:
            live = render.LiveSheet()
            live.start()
            try:
                answer = generate.generate_answer(
                    cmd, hv, cached[1] if cached else "", question,
                    on_delta=live.add_delta)
            finally:
                live.stop()
            if not tty:
                print(answer)
            print()
        except generate.GenerateError as e:
            render.warn(f"could not answer ({e}) — showing the sheet anyway")

    try:
        _hv, body, streamed = ensure_sheet(cmd, stream=True)
    except generate.GenerateError as e:
        _die(f"generation failed: {e} — try `shelp doctor`")
    if not tty:
        if body:
            print(body)
        return 0
    if body:
        _interactive_loop(cmd, body, already_rendered=streamed)
    return 0


def cmd_chat(args) -> int:
    words = list(args.words or [])
    cmd = None
    if words and CMD_RE.fullmatch(words[0]) and shutil.which(words[0]):
        cmd = words[0]
        words = words[1:]
    question = " ".join(words).strip() or None
    sheet = None
    if cmd:
        try:
            _hv, sheet, _s = ensure_sheet(cmd, stream=True)
        except generate.GenerateError as e:
            render.warn(f"no cheat sheet available ({e}) — chatting without one")
    chat.chat_session(cmd, question, sheet=sheet, new=args.new)
    return 0


def cmd_warm(args) -> int:
    failures = 0
    for raw in args.cmds:
        cmd = _validate_or_die(raw)
        try:
            _hv, body, _s = ensure_sheet(cmd, refresh=args.refresh)
            status = "ok" if body else "no doc, not generated"
            print(f"{cmd}: {status}" + (f" ({body.count(chr(10)) + 1} lines)" if body else ""))
        except generate.GenerateError as e:
            failures += 1
            print(f"{cmd}: FAILED — {e}")
    return 1 if failures else 0


def cmd_list(_args) -> int:
    from rich.table import Table

    table = Table(title="shelp cheat sheets", title_justify="left")
    for col in ("command", "generated", "model", "version/flavor", "pinned"):
        table.add_column(col)
    rows = cache.list_sheets()
    if not rows:
        print("no cached sheets yet — try `shelp <cmd>` or `shelp warm tar rsync`")
        return 0
    import datetime as dt

    for name, meta, _mtime in rows:
        age = meta.get("generated", "?")
        try:
            age = f"{(dt.date.today() - dt.date.fromisoformat(age)).days}d ago"
        except ValueError:
            pass
        vf = " / ".join(x for x in (meta.get("flavor"), meta.get("version")) if x)
        table.add_row(name, age, meta.get("model", "?"), vf or "—",
                      "📌" if cache.is_pinned(meta) else "")
    render._console().print(table)
    return 0


def cmd_prune(args) -> int:
    if args.all:
        removed = cache.prune(all_=True)
    else:
        days = args.older_than if args.older_than is not None else 90
        removed = cache.prune(older_than_days=days)
    print(f"pruned {len(removed)} item(s)" + (f": {', '.join(removed)}" if removed else ""))
    return 0


def cmd_init(args) -> int:
    dest = install_plugin(args.shell)
    rc = "~/.zshrc" if args.shell == "zsh" else "~/.bashrc"
    print()
    print(f"Done. Start a new shell, or: source {rc}")
    print("Then try:  unzip??   ·   tar?? list a tar.gz   ·   ?? (general chat)")
    if not shutil.which("shelp"):
        print("note: `shelp` is not on PATH outside its project venv — see `shelp doctor`")
    return 0


def cmd_doctor(args) -> int:
    from rich.console import Console
    from rich.table import Table

    con = Console()
    table = Table(title="shelp doctor", title_justify="left")
    table.add_column("check")
    table.add_column("status")

    table.add_row("version", __version__)
    table.add_row("cache", config.cache_root())
    table.add_row("base_url", config.base_url())
    table.add_row("model", f"{config.model()} (chat: {config.chat_model()})")
    has_key = bool(os.environ.get("SHELP_API_KEY")
                   or os.environ.get("OPENROUTER_API_KEY"))
    if has_key:
        table.add_row("api key", "SHELP_API_KEY/OPENROUTER_API_KEY set")
    elif "openrouter.ai" in config.base_url():
        table.add_row("api key", "[red]missing — set OPENROUTER_API_KEY[/red]")
    else:
        table.add_row("api key", "[dim]none (custom base_url — ok if local)[/dim]")
    shelp_path = shutil.which("shelp")
    table.add_row("shelp on PATH", shelp_path or "[yellow]not found[/yellow]")
    con.print(table)

    if args.live:
        print("live ping (one tiny completion)…")
        try:
            reply = generate.complete("Reply with exactly: OK", "ping",
                                      max_tokens=60)
            ok = reply.strip().upper().startswith("OK")
            print(f"live: {'OK' if ok else 'unexpected reply: ' + reply[:80]}")
        except (generate.GenerateError, Exception) as e:  # noqa: BLE001
            print(f"live: FAILED — {e}")
            return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shelp",
        description="`cmd??` — cheat sheets and chat for shell commands",
    )
    p.add_argument("--version", action="version", version=f"shelp {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("show", help="show a command's cheat sheet")
    sp.add_argument("cmd")
    sp.add_argument("--refresh", action="store_true", help="regenerate the sheet")
    sp.add_argument("--raw", action="store_true", help="print raw markdown, no UI")
    sp.add_argument("--short", action="store_true", help="TL;DR (`cmd?` mode)")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("trigger", help="entry point used by the shell handler")
    sp.add_argument("cmd", nargs="?")
    sp.add_argument("words", nargs="*", metavar="question")
    sp.add_argument("--short", action="store_true", help="TL;DR (`cmd?` mode)")
    sp.set_defaults(fn=cmd_trigger)

    sp = sub.add_parser("chat", help="agentic chat (optionally about a command)")
    sp.add_argument("words", nargs="*", metavar="[cmd] [question]")
    sp.add_argument("--new", action="store_true", help="start a fresh thread")
    sp.set_defaults(fn=cmd_chat)

    sp = sub.add_parser("warm", help="pre-generate sheets")
    sp.add_argument("cmds", nargs="+", metavar="cmd")
    sp.add_argument("--refresh", action="store_true")
    sp.set_defaults(fn=cmd_warm)

    sp = sub.add_parser("list", help="list cached sheets")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("prune", help="delete old sheets")
    sp.add_argument("--older-than", type=int, metavar="DAYS",
                    help="default 90")
    sp.add_argument("--all", action="store_true", help="everything, incl. chat state")
    sp.set_defaults(fn=cmd_prune)

    sp = sub.add_parser("init", help="install the shell handler")
    sp.add_argument("shell", choices=["zsh", "bash"])
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("doctor", help="check install and auth")
    sp.add_argument("--live", action="store_true", help="run a tiny completion")
    sp.set_defaults(fn=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-") and argv[0] not in SUBCOMMANDS:
        argv.insert(0, "show")
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
