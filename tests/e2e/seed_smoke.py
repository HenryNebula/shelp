"""Seed a cached `ls` sheet for ps_smoke.ps1 (Windows CI).

A file, not `python -c`: under Windows PowerShell 5.1 the native-argument
quoting mangles embedded double quotes — the same hazard the shell plugin
sidesteps with SHELP_QUESTION.
"""

from shelp import cache
from shelp.harvest import harvest

cache.save("ls", "## ls - list directory contents\n- long listing\n",
           harvest("ls").hash, "stub")
print("seeded")
