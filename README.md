# shelp

[![CI](https://github.com/HenryNebula/shelp/actions/workflows/ci.yml/badge.svg)](https://github.com/HenryNebula/shelp/actions/workflows/ci.yml)

`cmd?` → TL;DR. `cmd??` → a full, man-page-grounded cheat sheet. `??` → chat.
All backed by your choice of model via **OpenRouter** (or any local
OpenAI-compatible server) — no agent harness, no vendor lock.

```console
$ jq?                     # TL;DR — one-liner + 5 common invocations
$ ffmpeg??                # full sheet: tasks · flags · gotchas · preview safely
c) chat · r) regenerate · q) quit
$ tar?? list a tar.gz     # sheet + inline answer
$ ??                      # general chat (shelp's own agentic loop)
```

(IPython muscle memory: `obj?` → docstring, `obj??` → source.)

## Setup

Python 3.11+, plus either an [OpenRouter](https://openrouter.ai/keys) key or
any OpenAI-compatible endpoint:

```bash
uv tool install /path/to/shelp    # or: pipx install /path/to/shelp
export OPENROUTER_API_KEY=sk-or-…
shelp init zsh && exec zsh        # or: shelp init bash && exec bash
```

For development: clone, `uv sync`, then `uv run shelp …`
(`.envrc.example` shows optional uv cache/venv relocation).
Releases: push a `v*` tag — CI builds the wheel and attaches it to a
GitHub Release; see `.github/workflows/release.yml` to enable PyPI.

Config (environment, all optional except the key with remote providers):

| var | default | |
|---|---|---|
| `SHELP_API_KEY` / `OPENROUTER_API_KEY` | — | provider key (unneeded for local servers) |
| `SHELP_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b:free` | any OpenRouter slug |
| `SHELP_CHAT_MODEL` | = `SHELP_MODEL` | stronger model for chat if you like |
| `SHELP_BASE_URL` | `https://openrouter.ai/api/v1` | any OpenAI-compatible endpoint |
| `SHELP_CACHE_DIR` | `$XDG_CACHE_HOME/shelp` (else `~/.cache/shelp`) | sheets + chat sessions |
| `SHELP_NO_PAGER` | unset | never page long sheets |

The default model is on OpenRouter's free tier (≈20 req/min, 200 req/day —
plenty for sheet generation). Free variants come and go, and some keys
restrict which providers they may use — if you get a 404 "no allowed
providers", point `SHELP_MODEL` at another `:free` slug.

**Fully offline:** `export SHELP_BASE_URL=http://localhost:30000/v1
SHELP_API_KEY=local SHELP_MODEL=qwen3.5-4b` (llama-server's OpenAI endpoint).
Tool calling in chat depends on the local model's support for it.

## The chat (`??`, `c`, `shelp chat <cmd>`)

shelp's own agentic loop — ~150 lines, no framework:

- streams replies; one tool: `bash`
- read-only lookups (`man`, `--help`, `which`, `ls`, `cat`, `grep`, pipes of
  those) auto-run; **anything else asks `y/N` first** (chaining, redirection,
  substitution, or unknown commands)
- sessions persist per command under the cache dir and **resume** on re-entry;
  `shelp chat <cmd> --new` starts clean; `q`/Ctrl-D exits

## Commands

| | |
|---|---|
| `shelp <cmd> [--refresh] [--raw] [--short]` | sheet / TL;DR |
| `shelp chat [cmd] [question] [--new]` | agentic chat |
| `shelp warm tar rsync ffmpeg` | pre-generate sheets |
| `shelp list` / `prune [--older-than Nd] [--all]` | cache management |
| `shelp doctor [--live]` | provider check + ping |

Sheets are grounded in the **local** man page + `--help` (so they match the
installed flavor/version), cached with hash invalidation, and hand-editable —
`shelp show --raw <cmd>` then set `pinned: true` in the front-matter to opt out
of auto-regeneration. First generation per command: ~5–15s depending on
provider (it streams while generating); after that: ~0.2s.

Design notes: [DESIGN.md](DESIGN.md).

## License

MIT — see [LICENSE](LICENSE).
