"""Shell integration: the `cmd??` handlers, embedded and installed by `shelp init`."""

from __future__ import annotations

import os
import shutil
import subprocess
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
  # SHELP_SHELL tells chat which shell its `shell` tool should speak.
  if [[ -r /dev/tty ]]; then
    SHELP_SHELL=zsh command shelp trigger "$base" $opts -- ${rest:+"$rest"} < /dev/tty
  else
    SHELP_SHELL=zsh command shelp trigger "$base" $opts -- ${rest:+"$rest"}
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
    SHELP_SHELL=zsh command shelp trigger "$base" -- "$@"    # base empty → bare ?? chat
    return
  elif [[ $1 == *'?' ]]; then          # single ? → TL;DR
    base=${1%\?}
    shift
    SHELP_SHELL=zsh command shelp trigger "$base" --short -- "$@"
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
      # MSYS_NO_PATHCONV / MSYS2_ARG_CONV_EXCL: without them, Git Bash
      # rewrites POSIX-looking question words ("/etc") into Windows paths
      # on their way to shelp.exe. Harmless no-ops under Linux bash.
      SHELP_SHELL=bash MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
        command shelp trigger "${base:-}" -- "$@"
      return
      ;;
    *'?')
      local base="${1%\?}"
      shift
      SHELP_SHELL=bash MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
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

# PowerShell, 5.1-parseable throughout (no `??`/ternary). Two layers, like
# zsh: an Enter key handler as primary, CommandNotFoundHandler as fallback.
POWERSHELL_PLUGIN = r'''# shelp — `cmd?` TL;DR, `cmd??` cheat sheet, `??` chat (PowerShell).
# Installed by `shelp init powershell`. To uninstall: remove this file and
# its dot-source line in $PROFILE, then either restart the shell or run
#   Remove-PSReadLineKeyHandler -Key Enter
#   Set-PSReadLineOption -CommandNotFoundHandler $null
#
# Primary layer — an Enter key handler that intercepts the raw buffer
# BEFORE PowerShell parses it: everything after `cmd?` reaches shelp
# verbatim (quotes, $vars, leading-dash words). Bare `??` works even though
# PS 7 parses `??` as an operator — the line is never parsed. The question
# travels via the SHELP_QUESTION env var: PS 5.1 mangles embedded quotes in
# native-command arguments, env vars don't. PSReadLine keeps one handler per
# key, so a pre-existing custom Enter binding is replaced (re-binding is
# idempotent — no re-source recursion like zsh's widget chaining).

function global:Test-ShelpLine {
  # $null when the line is not ours; otherwise @{ Base; Rest; Short }.
  param([string]$Line)
  if ($Line -match "`n") { return $null }    # continuation line: not ours
  $t = $Line.TrimStart()
  if ($t -match '^(\?\?|\?)(\s+(.*))?$') {   # bare ?? chat / ? chat
    return @{ Base = ''; Rest = $Matches[3]; Short = ($Matches[1] -eq '?') }
  }
  if ($t -match '^([A-Za-z0-9][A-Za-z0-9_.+@-]*)(\?\?|\?)(\s+(.*))?$') {
    return @{ Base = $Matches[1]; Rest = $Matches[4]
              Short = ($Matches[2] -eq '?') }
  }
  return $null
}

function global:Invoke-ShelpTrigger {
  param([string]$Base, [string]$Rest, [switch]$Short)
  if (-not (Get-Command shelp -ErrorAction SilentlyContinue)) {
    Write-Host 'shelp: not on PATH — run `shelp doctor`'
    return
  }
  $env:SHELP_SHELL = 'powershell'   # chat's shell tool speaks PowerShell
  $env:SHELP_QUESTION = $Rest       # verbatim; immune to 5.1 arg quoting
  $prevUtf8 = $env:PYTHONUTF8       # box-drawing chars in legacy conhost
  $env:PYTHONUTF8 = '1'
  $a = @('trigger', $Base)
  if ($Short) { $a += '--short' }
  # PSReadLine runs key handlers with child stdout/stderr swallowed (fd 2 is
  # a capture pipe), and pwsh holds no controlling terminal on Unix, so
  # /dev/tty is ENXIO. Stdin still points at the terminal, though: dup it
  # onto stdout/stderr and the sheet renders where the user is looking.
  # ($a's elements are all shell-safe: 'trigger', a validated base name,
  # '--short'.) Windows console API passes child output through natively.
  try {
    if ($env:OS -ne 'Windows_NT') {
      & sh -c ("exec >&0 2>&1; exec shelp " + ($a -join ' '))
    } else {
      & shelp @a
    }
  }
  finally {
    $env:SHELP_SHELL = $null
    $env:SHELP_QUESTION = $null
    if ($null -eq $prevUtf8) { $env:PYTHONUTF8 = $null }
    else { $env:PYTHONUTF8 = $prevUtf8 }
  }
}

$_shelpRL = Get-Module PSReadLine -ErrorAction SilentlyContinue
if (-not $_shelpRL) {
  try { Import-Module PSReadLine -ErrorAction Stop
        $_shelpRL = Get-Module PSReadLine } catch { }
}
if ($_shelpRL) {
  Set-PSReadLineKeyHandler -Key Enter `
      -BriefDescription shelpTrigger `
      -LongDescription 'cmd?? cheat sheet before PowerShell parses the line' `
      -ScriptBlock {
    param($key, $arg)
    # 2-arg GetBufferState overload: the raw buffer, before PS parses it.
    # (The 4-arg one is for AST/tokens; there is no "inline" bool — the
    # multi-line case is handled by Test-ShelpLine's newline check.)
    $line = $null; $cursor = $null
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState(
      [ref]$line, [ref]$cursor)
    $hit = Test-ShelpLine $line
    if ($hit) {
      # history keeps the typed line; RevertLine clears the buffer so the
      # sheet renders below it and AcceptLine yields a fresh prompt.
      [Microsoft.PowerShell.PSConsoleReadLine]::AddToHistory($line)
      [Microsoft.PowerShell.PSConsoleReadLine]::RevertLine()
      try { Invoke-ShelpTrigger -Base $hit.Base -Rest $hit.Rest -Short:$hit.Short }
      finally { [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine() }
    } else {
      [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
    }
  }

  # Fallback for lines that slip past the Enter handler. CommandNotFound
  # still prints its own error above the sheet — cosmetic; safety net only.
  # Probe the parameter, not the version: docs say CommandNotFoundHandler
  # arrived in PSReadLine 2.3.4, but 2.3.5 (PS 7.4.2) demonstrably lacks it.
  $_shelpHasCNF = (Get-Command Set-PSReadLineOption -ErrorAction SilentlyContinue)
  if ($_shelpHasCNF -and $_shelpHasCNF.Parameters.ContainsKey('CommandNotFoundHandler')) {
    Set-PSReadLineOption -CommandNotFoundHandler {
      param($commandLine)
      $hit = Test-ShelpLine ([string]$commandLine)
      if ($hit) {
        Invoke-ShelpTrigger -Base $hit.Base -Rest $hit.Rest -Short:$hit.Short
      }
    }
  }
  Remove-Variable _shelpRL, _shelpHasCNF -ErrorAction SilentlyContinue
}
'''

MARKER = "# shelp (cmd?? cheat sheets)"

_PLUGINS = {"zsh": ("shelp.zsh", ZSH_PLUGIN, "~/.zshrc"),
            "bash": ("shelp.bash", BASH_PLUGIN, "~/.bashrc")}


def _powershell_profile() -> Path:
    """CurrentUserAllHosts profile, resolved by PowerShell itself — survives
    OneDrive-redirected Documents and the PS5.1/PS7 profile split."""
    if p := os.environ.get("SHELP_PROFILE"):
        return Path(p).expanduser()
    for exe in ("pwsh", "powershell"):
        if not shutil.which(exe):
            continue
        try:
            out = subprocess.run(
                [exe, "-NoProfile", "-Command",
                 "Write-Output $PROFILE.CurrentUserAllHosts"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
        if lines:
            return Path(lines[-1])
    raise SystemExit(
        "shelp init powershell: no PowerShell found — install PowerShell 7 "
        "(winget install Microsoft.PowerShell) or point SHELP_PROFILE at "
        "your profile file")


def install(shell: str) -> Path:
    if shell == "powershell":
        profile = _powershell_profile()
        profile.parent.mkdir(parents=True, exist_ok=True)
        dest = profile.parent / "shelp.ps1"
        # LF-only: CRLF in a dot-sourced file is fine for PS, but one rule
        # for all plugin writes keeps the bash/MSYS story simple.
        dest.write_text(POWERSHELL_PLUGIN, encoding="utf-8", newline="\n")
        existing = (profile.read_text(encoding="utf-8", errors="replace")
                    if profile.exists() else "")
        if MARKER not in existing:
            with profile.open("a", encoding="utf-8", newline="\n") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"\n{MARKER}\n. '{dest}'\n")
            touched = True
        else:
            touched = False
        print(f"wrote {dest}")
        print(f"{'added source line to' if touched else 'source line already in'} {profile}")
        return dest

    fname, content, rc_s = _PLUGINS[shell]
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    dest_dir = Path(config_home) / "shelp"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    # newline="\n": Git Bash chokes on CRLF with `$'\r': command not found`
    dest.write_text(content, encoding="utf-8", newline="\n")

    rc = Path(os.path.expanduser(rc_s))
    sh = f'"${{XDG_CONFIG_HOME:-$HOME/.config}}/shelp/{fname}"'
    source_line = f'{MARKER}\n[ -f {sh} ] && source {sh}\n'
    if rc.exists():
        existing = rc.read_text(encoding="utf-8", errors="replace")
    else:
        existing = ""
    if MARKER not in existing:
        with rc.open("a", encoding="utf-8", newline="\n") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n" + source_line)
        touched_rc = True
    else:
        touched_rc = False
    print(f"wrote {dest}")
    print(f"{'added source line to' if touched_rc else 'source line already in'} {rc}")
    return dest
