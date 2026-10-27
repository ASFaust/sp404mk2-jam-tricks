# APP1 firmware format (v5.52)

This page describes the main application image in the v5.52 system update.

## SP404MKII_APP1.bin: main application (unencrypted)

A raw image of the flash, loaded at **`0x60080000`**. Evidence: the reset vector in its vector table
is `0x60080001`, which is the first instruction of the file. It starts directly with code,
with no header. The trailing bytes are zero padding.

### Boot sequence

1. `Reset_Handler` (`0x60080000`) sets the interrupt priority grouping (AIRCR), `BASEPRI`
   and the main stack pointer (`0x202BE008`). Then it calls `SystemInit_early` (`0x60080120`,
   which sets up clocks/SDRAM/caches) and points `VTOR` to `0x00000000`.
2. `__scatterload` (`0x60080054`) walks the scatter-load table at `0x6008062C`.
   This is the standard ARM Compiler (armcc/armclang) C runtime start-up. Each
   16-byte entry is `(source, destination, size, handler)` and the handler is one of:
   - `0x600800E4` `__scatterload_copy`: plain copy
   - `0x60080088` `__decompress`: LZ77-style decompression (re-implemented in `app1/tools/build_elf.py`)
   - `0x60080100` `__scatterload_zeroinit`: fill with zeros (BSS)
3. Then `main` runs (not yet identified; the scatter-load runtime jumps to `__rt_entry`
   after the table, so start from the end of `__scatterload`).

### Scatter-load table

| Source (flash) | Dest (runtime) | Size | How | ELF section |
|---|---|---|---|---|
| `0x600806EC` | `0x00000000` | `0x400` | copy | `.itcm_vectors` |
| `0x60080AF0` | `0x00000400` | `0x1F800` | copy | `.itcm_00000400` |
| `0x600A0300` | `0x20000000` | `0x9180` | LZ | `.dtcm_data_20000000` |
| `0x600A0840` | `0x2023F000` | `0x7A250` | LZ | `.ocram_data_2023f000` |
| `0x600A1AC0` | `0x80000000` | `0x245740` | copy | `.sdram_code` |
| `0x602E7200` | `0x80245740` | `0xA20C0` | LZ | `.sdram_data_80245740` |
| `0x60306E00` | `0x8383A30C` | `0x118` | copy | `.sdram_8383a30c` |
| `0x60307200` | `0x83FF0000` | `0x1800` | LZ | `.sdram_data_83ff0000` |
| — | `0x20009180` | `0x358` | zero | `.dtcm_bss_20009180` |
| — | `0x202B9250` | `0x4DB8` | zero | `.ocram_bss_202b9250` |
| — | `0x802E7800` | `0x3252B0C` | zero | `.sdram_bss_802e7800` |
| — | `0x83FF1800` | `0xA758` | zero | `.sdram_bss_83ff1800` |

`app1/tools/build_elf.py` replays this table and writes `app1/build/app1.elf`. Every region sits at
its runtime address there, which is what Ghidra needs: code in SDRAM refers to other
code and data by SDRAM addresses, not flash addresses.
