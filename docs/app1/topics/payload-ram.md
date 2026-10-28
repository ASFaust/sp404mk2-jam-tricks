# Where our code runs (and why not from flash)

## Summary

Until 2026-10-26 our payload ran in place from flash (`0x60308000`). That freezes the SP: the
firmware erases and reprograms its **system settings sector** (flash `0x3FF000`) at run time
with interrupts and task switches on, and while the flash is busy, instruction fetches from
it return garbage. Roland's code never notices, because all of APP1 runs from RAM (ITCM,
SDRAM). Our hooks run constantly (`hook_audio_rx` every 1.33 ms, `hook_idle`, the resampler
thunks), so any settings save could run garbage.

Since then the payload is still *stored* at flash `0x60308000` but *runs* from SDRAM
`0x83FE0000`. Roland's own start-up copies it there, through a copy of the scatter-load
table with one more entry.

**Rule: nothing of ours may execute from flash.**

## Symptoms it caused (device, 2026-10-25/26)

- Freezes "at random", e.g. on opening INPUT SETTING after playing a note.
- Then reproducibly: SEL. PROJECT → pick a project → hang. The display kept its picture and
  the pad lights kept blinking, but no input worked and the power switch did nothing (power
  off saves settings too); only pulling power helped.
- After a power-down the SP came up with project 01 instead of the last one: the settings
  write at power-down was cut short, and `Settings_Load` replaces every word outside its
  range (an erased word reads `0xFFFFFFFF`) with its default.

Why random at first: flash is cacheable (MPU region 2), so the instruction cache often holds
the few hooks that run. The tuner build added an always-on audio hook and more code, so
cache misses during an erase became nearly certain.

## Key functions

| Address | Name | What it does |
|---|---|---|
| `0x800469B0` | `Settings_Load` | boot: copies 0x758 bytes from flash `0x603FF000` to `g_sys_settings`, checks each word against `g_settings_ranges` {default, min, max}, logs "outrange", saves if anything was fixed |
| `0x800DCF08` | `Settings_Save` | if `state[1]`: `Flash_EraseSector(0x3FF000)`, then 8 × `Flash_ProgramPage` via `g_settings_page_buf`. 27 callers, among them `Param_Set` and `TopScreen_HandleKey` |
| `0x8001ACB0` | `Flash_EraseSector` | write enable, 4 KB erase, polls the status register's busy bit. No interrupt masking |
| `0x800D5FD8` | `Flash_ProgramPage` | write enable, 256-byte page program, polls busy |
| `0x800E2CC0` | `FLEXSPI_TransferBlocking` | NXP SDK: one IP command sequence on FlexSPI (`0x402A8000`) |
| `0x800D5F68` | `DCache_InvalidateByAddr` | DCIMVAC per 32-byte line |

## How the payload gets to RAM

- `__scatterload` (`0x60080054`) reads its table bounds from two words at `0x60080080`,
  relative to that address (Roland's: `0x5AC`, `0x66C` → table `0x6008062C`..`0x600806EC`,
  12 entries of {source, destination, size, handler}). There's no room after the table.
- `make_image.py` (`add_payload_copy`) writes a copy of the 12 entries plus
  `{0x60308000, 0x83FE0000, size, __scatterload_copy 0x600800E4}` to flash `0x60307F00` (in
  the padding between APP1's end and the payload) and changes the two words to point at it.
  The copy handler moves 16-byte blocks, so the payload is padded to a multiple of 16.
- `app1/payload/payload.ld` links at `0x83FE0000` (64 KB, asserted). All jumps from Roland's code
  into ours go through absolute address words (`ldr.w pc, [pc, #n]` or vtable words), so the
  64 MB distance doesn't matter.
- `app1/tools/build_elf.py` replays the patched table the same way, which is how the build was
  checked: the region at `0x83FE0000` equals the flash bytes.

## Why `0x83FE0000` is free

- The looper/skip-back buffer is `0x8383A424`..`0x83F7A424`. Its start and end are built with
  `movw/movt` in 7 places (Tallfree shrinks it by changing them). Above it, up to MPU region 9
  at `0x83FF0000`, nothing in the firmware builds or holds an address: no literal word and no
  `movt` with `0x83F8`..`0x83FE` (scanned 2026-10-26). Tallfree runs a whole engine there, which
  supports this.
- MPU region 8 (`0x80000000`, 64 MB, normal write-back cacheable, executable) covers it. That's
  the same as Roland's code at `0x80000000`, and it's copied by the same routine, so whatever
  cache handling works for Roland's code works for ours.
- MPU table from the boot code (`0x600803F8`..): 0 `0x80000000` 512 MB device; 1 `0x60000000`
  512 MB device; 2 `0x60000000` 4 MB flash, read-only, cacheable; 3 `0x00000000` 1 GB device;
  4 ITCM 128 KB; 5 DTCM 128 KB; 6 `0x20200000` 512 KB; 7 `0x20280000` 256 KB; 8 SDRAM 64 MB;
  9 `0x83FF0000` non-cacheable.

If we ever need more than ~460 KB, shrinking the skip-back buffer as Tallfree does is the next
step (`resources/repos/tallfree/image/make_image.py`).

## Open questions

- Not yet confirmed on the device (as of writing): select projects several times, change
  INPUT SETTING, power off and on, and check that the last project comes back.
- Payload state (`ram.h`) moved from region 9's tail (`0x83FFBF60`..) to the top 8 KB of our
  range (`0x83FEE000`..) on 2026-10-28 (web-patcher.md); not yet tested on the device.
  Writable globals in the RAM copy would also be possible with a zero-init entry. Not done.
