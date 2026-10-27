#!/bin/sh
# List every reference to the given hex addresses, with the function each is in (read-only).
#   app1/tools/xrefs.sh 80146260 [more addresses...]
# Fails if the Ghidra GUI has the project open (Ghidra locks it); close it first.
root=$(cd "$(dirname "$0")/.." && pwd)
ghidra.analyzeHeadless "$root/ghidra" sp404mk2 -process app1.elf -noanalysis -readOnly \
    -scriptPath "$root/tools/ghidra_scripts" -postScript Xrefs.java "$@" 2>&1 |
    awk '/@@@/{p=1} /INFO  (REPORT|ANALYZING|Headless)/{p=0} p' |
    sed 's/^INFO  Xrefs.java> //; s/ (GhidraScript) *$//'
