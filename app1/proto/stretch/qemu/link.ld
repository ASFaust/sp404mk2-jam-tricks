MEMORY { CODE (rx) : ORIGIN = 0x00000000, LENGTH = 4M
         RAM (rw)  : ORIGIN = 0x20000000, LENGTH = 4M }
SECTIONS {
  .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > CODE
  .data : { *(.data*) } > RAM AT > CODE
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
