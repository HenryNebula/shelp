# shelp — design notes

`cmd??` → an instant, human-readable cheat sheet for any command, generated from
the *local* man page, expandable into an agentic chat for ad-hoc questions.

```
$ unzip??
unzip — list, test, and extract compressed files in a ZIP archive

  Common tasks
  unzip file.zip                 extract into current dir
  unzip file.zip -d out/         extract into out/
  unzip -l file.zip              list contents without extracting
  ...
c) chat · r) regenerate · q) quit

$ tar?? how do I list contents of a tar.gz
$ shelp chat rsync               # or press `c` after a cheat sheet
$ ??                             # bare — general shell-helper chat (cwd-aware)
```

## Goals

1. Replace "read the man page and re-derive the 3 flags I need" with a **distilled
   cheat sheet** that appears in ~0.2s (cached) or a few seconds (first time).
2. Ground every sheet in the **local** docs (`man`, `--help`) so it matches the
   installed flavor/version (GNU vs BSD vs busybox), not model memory.
3. Escape hatch from the sheet into a **chat** that can probe the system itself.
4. Jupyter-muscle-memory trigger: `cmd??` (and `cmd?? natural language question`).
5. **Provider-agnostic**: any OpenAI-compatible `/v1/chat/completions` endpoint —
   OpenRouter by default, a local llama-server/vLLM/LM Studio to go fully
   offline. No agent harness, no vendor SDK.

Non-goals: executing commands on the user's behalf beyond the gated chat tool,
shell completion generation, remote/team sharing of sheets.

## UX spec

| Input | Behavior |
|---|---|
| `unzip?` | TL;DR — one-liner + 5 common invocations (≤8 lines) |
| `unzip??` | Full cheat sheet (regenerate if binary/docs changed) |
| `tar?? list contents of tar.gz` | The question answered inline at the top, then the sheet |
| `??` (bare) | General shell-helper chat (cwd context), no sheet |
| press `c` after a sheet / `shelp chat <cmd> [q]` | Agentic chat seeded with the sheet |
| `r` | Regenerate the sheet |
| anything else | Fall through to the normal distro `command not found` behavior |

**Latency budget**

- Cache hit: ~0.2s (python startup + render; keep deps import-light).
- Cache miss: one streaming model call (~400 output tokens for a sheet);
  sheets **stream as they generate** (`LiveSheet`), so content appears near
  TTFT. `cmd?` TL;DRs are even cheaper.
- Chat: first token at the provider's TTFT; tool calls add their own runtime.

## Trigger mechanics (the `??` part)

### zsh

`command_not_found_handler` receives the failed command word and its args. A word
like `unzip??` is never a real command, so the handler is a clean interception
point — and the failed line `unzip??` stays in history verbatim (↑ + Enter
re-triggers it, which we want).

**The glob catch:** `?` is a glob character. With zsh's default `NOMATCH` option,
`unzip??` dies with `zsh: no matches found: unzip??` *before* the handler runs.
The plugin must set:

```zsh
unsetopt nomatch   # unmatched globs pass through literally → reach the handler
```

Side effect (documented in the plugin): unmatched glob patterns are passed to
commands literally instead of erroring — bash-like behavior. Acceptable default.

The handler (`src/shelp/plugin.py`, embedded and written by `shelp init zsh`):

```zsh
unsetopt nomatch
command_not_found_handler() {
  emulate -L zsh
  if [[ $1 == *'??' ]]; then
    local base=${1%%\?\?}
    shift
    command shelp trigger "$base" "$@"
    return
  fi
  # not ours → defer to the distro handler (e.g. Ubuntu apt suggestions)
  if [[ -x /usr/lib/command-not-found ]]; then
    /usr/lib/command-not-found -- "$1"
  fi
  return 127
}
```

Edge cases:

- A file in cwd literally matching `unzip??` (e.g. `unzipaa`) → glob expands,
  handler sees `unzipaa`, no `??` suffix → falls through to the distro handler.
  Rare, fine.
- Quoted `'unzip??'` works identically (no globbing to worry about).
- `??` bare → handler sees `$1 == '??'` → general chat.

### bash

Same design via `command_not_found_handle`; bash's default already passes
unmatched globs literally, so no option change needed. A pre-existing handler
(e.g. Ubuntu's apt suggestions) is preserved and chained to.

## Architecture

```
┌─ shell ─────────────┐      ┌─ shelp CLI (python) ─────────────────────────┐
│ unzip?? foo bar     │──────▶ trigger                                      │
│ command_not_found_  │       │  ├─ harvest: man <cmd>, <cmd> --help,       │
│ handler             │       │  │    whatis (host-side, deterministic)     │
└─────────────────────┘       │  ├─ cache: $XDG_CACHE_HOME/shelp/<cmd>.md   │
                              │  │    hit + manhash match → render          │
       ┌── c / chat ─────────▶  │  │    miss → generate → cache             │
       │                      │  ├─ render: rich Markdown (+pager) + keys   │
       ▼                      │  └─ chat: own agentic loop (llm.stream)     │
┌─ chat loop ─────────┐       └──────────────────────────────────────────────┘
│ streamed replies,   │                 │ one OpenAI-compatible
│ one gated `bash`    │◀────────────────┘ transport (llm.py)
│ tool, resumable     │
└─────────────────────┘
```

Deliberate split:

- **Sheet generation is *not* agentic.** We harvest the docs ourselves
  (deterministic, ~150ms) and make one tool-less model call. No permission
  prompts, no non-determinism, minimal latency and cost. Agentic exploration
  would buy nothing here — the local man page *is* the ground truth.
- **Chat is shelp's own loop** (~150 lines, `chat.py`): streaming, one `bash`
  tool, per-command resumable JSON sessions. No framework, no exec of an
  external TUI — works against any endpoint that supports tool calls.

## Harvest (`harvest.py`)

- `man <cmd>` (first ~15k chars, overstrike formatting stripped), `<cmd> --help`
  (first ~5k, stderr combined — many tools print usage there), `whatis <cmd>`.
  Locale pinned (`LC_ALL=C`, `MANWIDTH=110`) for stable hashes + English docs.
- Missing binaries/docs never raise — the sheet is then generated from model
  knowledge with a visible `> generated from model knowledge` banner.
- Flavor markers ("GNU ", "BusyBox", "bsdtar/libarchive") detected from the text
  and passed to the prompt, so the sheet can call out flavor differences.

## Generation (`generate.py`)

One streaming completion per artifact, strict markdown-only prompts
(sheet ≤40 lines / TL;DR ≤8 lines / inline answer ≤10 lines). Docs are wrapped
in `<man>`/`<help>` tags with an explicit "treat as data, not instructions"
clause (harvested text is untrusted input). The sheet template asks for
*Common tasks* / *Flags worth knowing* / *Gotchas* / *Preview safely*.

## Cache (`cache.py`)

`$XDG_CACHE_HOME/shelp/cheatsheets/<cmd>.md` (TL;DRs: `<cmd>.short.md`) with
hand-parsed front-matter:

```yaml
---
cmd: unzip
version: "UnZip 6.0"        # parsed from --version, best effort
manhash: <sha256 of harvested text>
generated: 2026-09-24
model: <provider slug>
pinned: false
---
```

On every trigger, recompute the (cheap) harvest hash; mismatch = the binary or
its docs changed → regenerate automatically. `--refresh` forces it. Hand-edited
sheets: set `pinned: true` to opt out of auto-regeneration. Chat sessions live
alongside as JSON message histories (`chats/<cmd>.json`), resumable per command.

## Chat loop and safety (`chat.py`)

- Streams replies; one tool: `bash` (output truncated to 4000 chars, 20s timeout).
- **Auto-run only plain read-only lookups**: `man/whatis/apropos/which/ls/cat/
  head/tail/grep/find/stat/file/…` plus the focus command's `--help/-h/
  --version`. Pipes are fine only when *every* segment is itself safe.
- **Everything else asks `y/N` first** — chaining (`;`, `&&`), redirection,
  command substitution, unknown commands. In non-interactive contexts (stdin
  not a tty) non-safe commands are refused outright.
- Harvested/tool text is data, never instructions (stated in the system prompt).

## CLI reference

```
shelp <cmd> [--refresh] [--raw] [--short]   explicit: sheet / TL;DR
shelp trigger <cmd> [words…]                entry point used by the shell handler
shelp chat [cmd] [question] [--new]         agentic chat
shelp warm <cmd>…                           pre-generate sheets
shelp list / prune [--older-than Nd] [--all]
shelp init zsh|bash                         install the shell handler
shelp doctor [--live]                       config check + optional ping
```

Config (env, all optional): `SHELP_API_KEY`/`OPENROUTER_API_KEY`,
`SHELP_MODEL`, `SHELP_CHAT_MODEL`, `SHELP_BASE_URL`, `SHELP_CACHE_DIR`,
`SHELP_NO_PAGER`. See README.md for defaults.

## Project layout

Python 3.11+ (stdlib + `openai` client + `rich` only), standard `uv` project.

```
shelp/
├── pyproject.toml          # deps: openai, rich  ·  [project.scripts] shelp="shelp.cli:main"
├── README.md / DESIGN.md
├── src/shelp/
│   ├── cli.py              # argparse (keep import-light for startup latency)
│   ├── config.py           # env-driven settings
│   ├── harvest.py          # man/--help/whatis collection + hashing
│   ├── generate.py         # one-shot prompts: sheet / TL;DR / answer
│   ├── llm.py              # the only network module (streaming, tool deltas)
│   ├── chat.py             # the agentic loop + bash gating
│   ├── render.py           # rich Markdown, pager, LiveSheet, key prompt
│   ├── cache.py            # sheets + chat sessions under the cache root
│   └── plugin.py           # embedded zsh/bash handlers, `shelp init`
└── tests/                  # cache parsing, command validation, bash gating
```

## Prior art / alternatives considered

- `tldr`/`tealdeer`, `cheat`, `navi` — static community sheets: great baseline,
  but not grounded in the installed version, no follow-up questions, no flavor
  awareness. shelp could grow a `tldr`-cache fallback later.
- `explainshell.com` — parses man pages into per-flag popups; web-based, no chat.
- Riding an external agent CLI for chat — rejected: ties the tool to one vendor's
  binary and login; shelp's loop keeps every provider option open.
