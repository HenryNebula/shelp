# Windows CI smoke: the PowerShell plugin under BOTH editions — this very
# file must parse under 5.1, which doubles as the plugin's 5.1 parse check.
# Exercises line parsing and a seeded-cache trigger through the real
# Invoke-ShelpTrigger (no LLM, offline).
#
# Usage: powershell|pwsh -NoProfile -File ps_smoke.ps1 -PluginPath <ps1>
# Env:   SHELP_CACHE_DIR=<scratch dir>; run from the repo root (needs uv).

param([Parameter(Mandatory = $true)][string]$PluginPath)

$ErrorActionPreference = 'Stop'
. $PluginPath

# seed a cached `ls` sheet using shelp's own cache module
& uv run python -c 'import os; from shelp import cache; from shelp.harvest import harvest; cache.save("ls", "## ls - list directory contents\n- long listing\n", harvest("ls").hash, "stub")'
if ($LASTEXITCODE) { throw "seeding failed" }

$h = Test-ShelpLine 'tar?? extract a tgz'
if ($h.Base -ne 'tar' -or $h.Rest -ne 'extract a tgz' -or $h.Short) {
  throw "parse FAIL: $($h | Out-String)"
}
$h2 = Test-ShelpLine 'gci?'
if ($h2.Base -ne 'gci' -or -not $h2.Short) { throw "short parse FAIL" }
if (Test-ShelpLine 'echo hi') { throw "plain line must not match" }
if (Test-ShelpLine "unzip?`nmore") { throw "multi-line buffer must not match" }

# On Windows the child's stdout returns through the pipeline; on Unix the
# plugin dups fd 0 onto stdout (PSReadLine swallows the pipes), so the sheet
# renders straight to the console. Assert on the CONSOLE stream: caller
# greps the combined output for 'long listing'.
Invoke-ShelpTrigger -Base 'ls' -Rest ''

"$($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion) smoke OK"
