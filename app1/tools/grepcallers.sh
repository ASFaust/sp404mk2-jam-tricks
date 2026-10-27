#!/bin/sh
# Decompile every caller of a function and grep the C (read-only, project unchanged).
#   app1/tools/grepcallers.sh 800DDA38 '0xbf|0xc0'   (targets may be comma-separated)
# Fails if the Ghidra GUI has the project open (Ghidra locks it); close it first.
root=$(cd "$(dirname "$0")/.." && pwd)
ghidra.analyzeHeadless "$root/ghidra" sp404mk2 -process app1.elf -noanalysis -readOnly \
    -scriptPath "$root/tools/ghidra_scripts" -postScript GrepCallers.java "$@" 2>&1 |
    awk '/@@@/{p=1} /INFO  (REPORT|ANALYZING|Headless)/{p=0} p' |
    sed 's/^INFO  GrepCallers.java> //; s/ (GhidraScript) *$//'
