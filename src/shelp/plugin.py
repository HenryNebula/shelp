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

command_not_found_handler() {
  emulate -L zsh
  local base
  if [[ $1 == *'??' ]]; then
    base=${1%%\?\?}
    shift
    if [[ -z $base ]]; then
      command shelp trigger '' "$@"    # bare ?? → general chat
    else
      command shelp trigger "$base" "$@"
    fi
    return
  elif [[ $1 == *'?' ]]; then          # single ? → TL;DR
    base=${1%\?}
    shift
    command shelp trigger "$base" --short "$@"
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
      if [ -z "$base" ]; then
        command shelp trigger '' "$@"
      else
        command shelp trigger "$base" "$@"
      fi
      return
      ;;
    *'?')
      local base="${1%\?}"
      shift
      command shelp trigger "$base" --short "$@"
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
