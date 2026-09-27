"""Shell integration: the `cmd??` handlers, embedded and installed by `shelp init`."""

from __future__ import annotations

import os
from pathlib import Path

ZSH_PLUGIN = r'''# shelp — `cmd?` TL;DR, `cmd??` cheat sheet, `??` chat.
# Installed by `shelp init zsh`. Remove this file and its source line in ~/.zshrc
# to uninstall.
#
# `?` is a glob character: without this, zsh aborts `unzip??` with
# "no matches found" before command_not_found_handler can see it.
unsetopt nomatch

# Intercept trigger lines at accept-line, BEFORE zsh parses the buffer:
# everything after `cmd?` reaches shelp verbatim — globs (*.log), pipes,
# quotes and leading-dash words (`ls? -l …`) survive untouched. Without
# this, the shell would expand/parse the line first and argparse would
# choke on words that start with `-`.
_shelp_accept_line() {
  emulate -L zsh
  setopt extended_glob
  local buf base mark rest
  local prev=$_shelp_prev_accept
  # re-sourcing the plugin chains us to ourselves — call the builtin instead
  [[ $prev == _shelp_accept_line ]] && prev=
  buf=$BUFFER
  if [[ $buf == *$'\n'* ]]; then       # multi-line buffer: not ours
    zle ${prev:-.accept-line}
    return
  fi
  buf=${buf##[[:space:]]##}
  if [[ $buf =~ '^([A-Za-z0-9][A-Za-z0-9_.+@-]*)(\?\?|\?)([[:space:]].*)?$' ]]; then
    base=$match[1] mark=$match[2] rest=$match[3]
  elif [[ $buf =~ '^(\?\?|\?)([[:space:]].*)?$' ]]; then   # bare ?? chat
    base= mark=$match[1] rest=$match[2]
  else
    zle ${prev:-.accept-line}
    return
  fi
  rest=${rest##[[:space:]]##}
  print -s -- "$buf"                   # keep the typed line in history
  BUFFER=
  local -a opts
  opts=()
  [[ $mark == '?' ]] && opts=(--short)
  # zle redirects widget commands' stdin from /dev/null, which would kill
  # the c/r/q prompt and chat — give shelp the real terminal when we can.
  if [[ -r /dev/tty ]]; then
    command shelp trigger "$base" $opts -- ${rest:+"$rest"} < /dev/tty
  else
    command shelp trigger "$base" $opts -- ${rest:+"$rest"}
  fi
  zle .reset-prompt
}

# Chain to any pre-existing accept-line wrapper (e.g. zsh-autosuggestions).
# On re-source the existing binding is OURSELVES — chaining to it would
# recurse ("No such widget" on every plain command), so skip that.
typeset -g _shelp_prev_accept=
_shelp_ol=$(zle -l -L accept-line 2>/dev/null)
if [[ $_shelp_ol == "zle -N accept-line "* ]]; then
  typeset -g _shelp_prev_accept=${_shelp_ol##* }
fi
[[ $_shelp_prev_accept == _shelp_accept_line ]] && typeset -g _shelp_prev_accept=
zle -N accept-line _shelp_accept_line
unset _shelp_ol

# Fallback for lines the widget never sees (pasted after a prompt refresh,
# or when another plugin re-wraps accept-line after this one).
command_not_found_handler() {
  emulate -L zsh
  local base
  if [[ $1 == *'??' ]]; then
    base=${1%%\?\?}
    shift
    command shelp trigger "$base" -- "$@"    # base empty → bare ?? chat
    return
  elif [[ $1 == *'?' ]]; then          # single ? → TL;DR
    base=${1%\?}
    shift
    command shelp trigger "$base" --short -- "$@"
    return
  fi
  # Not ours → defer to the distro suggestion helper (Ubuntu/Debian), same as
  # the stock /etc/zsh_command_not_found handler does.
  if [[ -x /usr/lib/command-not-found ]]; then
    /usr/lib/command-not-found -- "$1"
  fi
  return 127
}
'''

BASH_PLUGIN = r'''# shelp — `cmd??` cheat sheets + chat for shell commands.
# Installed by `shelp init bash`. Remove this file and its source line in
# ~/.bashrc to uninstall.

# preserve any pre-existing handler (e.g. Ubuntu's apt suggestions)
if declare -F command_not_found_handle >/dev/null 2>&1; then
  eval "_shelp_prev_cnh() $(declare -f command_not_found_handle | tail -n +2)"
fi

command_not_found_handle() {
  case "$1" in
    *'??')
      local base="${1%%\?\?}"
      shift
      command shelp trigger "${base:-}" -- "$@"
      return
      ;;
    *'?')
      local base="${1%\?}"
      shift
      command shelp trigger "$base" --short -- "$@"
      return
      ;;
  esac
  if declare -F _shelp_prev_cnh >/dev/null 2>&1; then
    _shelp_prev_cnh "$@"
  elif [[ -x /usr/lib/command-not-found ]]; then
    /usr/lib/command-not-found -- "$1"
  fi
  return 127
}
'''

MARKER = "# shelp (cmd?? cheat sheets)"

_PLUGINS = {"zsh": ("shelp.zsh", ZSH_PLUGIN, "~/.zshrc"),
            "bash": ("shelp.bash", BASH_PLUGIN, "~/.bashrc")}


def install(shell: str) -> Path:
    fname, content, rc_s = _PLUGINS[shell]
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    dest_dir = Path(config_home) / "shelp"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    dest.write_text(content, encoding="utf-8")

    rc = Path(os.path.expanduser(rc_s))
    sh = f'"${{XDG_CONFIG_HOME:-$HOME/.config}}/shelp/{fname}"'
    source_line = f'{MARKER}\n[ -f {sh} ] && source {sh}\n'
    if rc.exists():
        existing = rc.read_text(encoding="utf-8", errors="replace")
    else:
        existing = ""
    if MARKER not in existing:
        with rc.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n" + source_line)
        touched_rc = True
    else:
        touched_rc = False
    print(f"wrote {dest}")
    print(f"{'added source line to' if touched_rc else 'source line already in'} {rc}")
    return dest
