/* The import table (imports.h): first in the payload, so it starts at 0x83FE0000. */
#include "imports.h"

        .syntax unified
        .section .imports, "a"
        .align  2

#define IMPORT_WORD(name, addr) .global imp_##name ; imp_##name: .word addr ;
#define IMPORT_COUNT(name, addr) + 1

        .global roland_imports
roland_imports:
        .word   IMPORTS_MAGIC
        .word   0 ROLAND_IMPORTS(IMPORT_COUNT)
        ROLAND_IMPORTS(IMPORT_WORD)
