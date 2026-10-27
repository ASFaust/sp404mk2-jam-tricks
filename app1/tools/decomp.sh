#!/bin/sh
# Decompile the functions containing the given hex addresses (read-only, project unchanged).
#   app1/tools/decomp.sh 80147f30 [more addresses...]
# Fails if the Ghidra GUI has the project open (Ghidra locks it); close it first.
root=$(cd "$(dirname "$0")/.." && pwd)
ghidra.analyzeHeadless "$root/ghidra" sp404mk2 -process app1.elf -noanalysis -readOnly \
    -scriptPath "$root/tools/ghidra_scripts" -postScript Decompile.java "$@" 2>&1 |
    awk '/@@@/{p=1} /INFO  (REPORT|ANALYZING|Headless)/{p=0} p' |
    sed 's/^INFO  Decompile.java> //; s/ (GhidraScript) *$//'
