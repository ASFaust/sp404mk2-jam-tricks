/* Our code, appended to APP1 in free flash (make_image.py pads the image up to PAYLOAD_FLASH)
   and copied to SDRAM at boot by Roland's own start-up: make_image.py gives __scatterload a
   copy of its table with one more entry. It must not run from flash: the firmware erases and
   reprograms its settings sector (flash 0x3FF000) at run time with interrupts on, and code
   fetched from flash meanwhile is garbage (docs/app1/topics/payload-ram.md).

   0x83FE0000..0x83FF0000 is in the gap between the looper/skip-back buffer (ends 0x83F7A424)
   and MPU region 9 that nothing in the firmware refers to. Normal cacheable memory, MPU
   region 8. The copy is plain: writable globals would be possible but aren't set up (no
   zero-init), so state lives in ram.h's blocks in the top 8 KB, 0x83FEE000..0x83FF0000.

   The import table (imports.h) comes first, so it starts at 0x83FE0000. */
SECTIONS
{
  . = 0x83FE0000;
  payload_ram_start = .;
  .payload : AT(0x60308000) {
    KEEP(*(.imports)) *(.text*) *(.rodata .rodata.[!s]*)
    payload_strings = .;        /* string literals last: make_image.py's address check stops here */
    *(.rodata*) . = ALIGN(16);
  }
  payload_ram_end = .;
  .writable : { *(.data*) *(.bss*) *(COMMON) }
  ASSERT(SIZEOF(.writable) == 0, "payload must not have writable globals")
  ASSERT(payload_ram_end <= 0x83FEE000, "payload code runs into ram.h's state (PAYLOAD_RAM_START)")
  /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.ARM.exidx*) }
}
