# Chromatic mode and scales (goal #1)

## Summary

In chromatic mode the 16 pads play one sample at 16 pitches. A pad's pitch in semitones is

    semi = table[pad % 16] - g_param_store.chrom_offset

with a **fixed** table, one row of 4 pads per 4 semitones:

```
pads  0- 3:  +4 +5 +6 +7      (top row)
pads  4- 7:   0 +1 +2 +3
pads  8-11:  -4 -3 -2 -1
pads 12-15:  -8 -7 -6 -5      (bottom row)
```

The VALUE knob changes `chrom_offset` (4 per step, 1 with SHIFT), which moves the whole grid.
A scale (SCALE = Maj, Min, …) doesn't change the table. It only **filters**: a pad whose
`(semi - root) mod 12` isn't in the scale is ignored and drawn dark. So the pad grid on screen
always shows the same pattern of numbers, and only about half the pads play.

The table exists **four times**, each read by different code. They hold identical values:

| Copy | Read by |
|---|---|
| `g_chrom_semis_trigger` `0x801AA0A0` | `Pad_Trigger` (`0x800D63C8`): the pitch that is actually played |
| `g_chrom_semis_pitch` `0x801AA0E0` | `0x800F7CE0`, `0x800F800E`: store the pitch in PickStore `0x30` |
| `g_chrom_semis_page` `0x801AA120` | chromatic page: `ChromPage_OnPadDown/Up`, `ChromPadCell_Draw` (the numbers in the grid) |
| `g_chrom_semis_lights` `0x80028F64` | unattached code at `0x80028270` with its own scale check, probably the pad lights |

## Parameters

| Param id | Name (`Param_GetName`) | Values (`Param_FormatValue`) |
|---|---|---|
| `0xBF` | Chromatic Mode | Off / On |
| `0xC0` | Chromatic Scale | number (`%d`) |
| `0xC1` | – | the pad whose sample chromatic mode plays |
| `0xF1` | SCALE | 0 Chrom, 1 Maj, 2 Min, 3 Dorian, 4 Phryd., 5 Penta M, 6 Penta m |
| `0xF2` | NOTE | root, names from the table at `0x800C6AAC` |
| `0x9B` | – | `Param_GetRange` gives the playable semitone range; pads outside show "-" |

The scale names were never found by string search because the compiler stores short strings as
immediate constants: `*out = 0x6a614d` writes the bytes `4D 61 6A 00`, i.e. `"Maj"`. So for short
labels, search the decompiled code, not the strings.

`g_param_store` keeps cached copies the hot code reads directly: `+0x2C` `chrom_offset`,
`+0x118` `chrom_scale`, `+0x11C` `chrom_root` (struct `ParamStore` in `app1/tools/types.txt`).

## The scale check

`ChromPage_OnPadDown` (`0x80111B38`):

```c
semi = g_chrom_semis_page[pad % 16] - g_param_store.chrom_offset;
scale = g_param_store.chrom_scale;
if (scale - 1 > 5 /* Chrom */ ||
    g_scale_flags[scale - 1][(semi - g_param_store.chrom_root + 24) % 12])
    ... range check against param 0x9B, then play ...
```

`g_scale_flags` (`0x801AB3B0`) points to six 12-byte tables of 0/1. A second set of pointers,
`g_scale_flags_lights` (`0x801AB4BC`), and copies in RAM at `0x82E01074` (13 bytes apart, used by
`0x80011230`, which looks for the nearest in-scale note) exist too.

## Key handling on the chromatic page

`ChromPage_OnKey` (`0x801120D8`): VALUE press (`0x31`) cycles SCALE 0 → 6 → 0. Keys
`0x3F`/`0x40` (VALUE knob down/up, presumably): plain = `Param_Step(0xC0, ∓4)`, with SHIFT =
∓1, with key `0x21` held and a scale chosen = root `0xF2` ∓1. After a change it sets 16 redraw
flags (one per pad cell, `0x60` apart in the page object).

## Idea for the in-key layout

Since everything reads the four tables, **rewriting the tables at run time** gives an in-key
layout everywhere at once: the screen numbers, the played pitch, the lights and the scale check.
If every table entry is an in-scale note, the existing scale check passes for all 16 pads by
itself, so no instructions in the check need patching.

- New C function `inkey_update()`: when SCALE ≠ Chrom, writes
  `table[pad] = chrom_offset + note(pad)` into all four copies, where `note(pad)` walks the
  scale's degrees from the bottom-left pad upwards. For Chrom it writes the original values back.
- Call it whenever scale, root or offset may have changed. The natural hook is the chromatic
  page's key handler: redirect its vtable word to a wrapper that calls the original
  `ChromPage_OnKey` and then `inkey_update()`. Do the same on page build.
- The tables are in SDRAM (data, not instructions), so writing them needs no cache maintenance.
  The code itself goes in free flash after APP1 (see [patching.md](../patching.md)), built with `arm-none-eabi-gcc`.

Still to decide with the user: which pad is the lowest note, what the knob does in in-key
mode (shift by degrees or by rows), and how far below the sample's pitch the grid starts.

## Open questions

- Where does the function containing `0x80028270` start, and is it really the pad lights?
- Which physical keys are `0x21`, `0x3F`, `0x40`?
- ~~What range does param `0x9B` give?~~ −12…+12 (`Param_GetRange` case `0x9B` at
  `0x800D38C8`); `0xC0` (offset) is −8…+4, `0xF1` 0…6, `0xF2` 0…11. So only 25 semitones
  play: an in-key layout fits 15 notes of a 7-note scale and 11 of a pentatonic. The
  experimental patch `pitch-range-24` widens it to ±24; does the audio engine handle that?
  (Test: Chrom scale, knob at its lowest offset, the top row then reaches +13…+15.)
- Which vtable holds `ChromPage_OnKey`, and which page id is the chromatic page? (Tallfree
  saw its title drawn at `0x801120A1`.)
- `chrom_offset` vs param `0xC0`: the knob steps `0xC0`; is `+0x2C` its cached copy?

## Widening the range to ±24: is the audio path safe?

Checked 2026-10-25, because the musician may push pads to ±24 semitones:

- **The pitch reaches the voice as a pad parameter.** `Pad_Trigger` calls
  `Voice_SetParam(voice, 0x73 /* PITCH */, semi)` and `(voice, 0x74 /* FINE */, 0)`.
  Pad parameters: `0x73` PITCH (−12…12), `0x74` FINE (−100…100 cents), `0x75` PLAY (0…3),
  `0x76` SPEED (500…15000), `0x77` VINYL (0/1), `0x78` PAN.
- **No clamp on the way:** `Voice_SetParam` and the render setup (`0x800E3F78`) both compute
  `semis = PITCH + FINE/100` (float, voice `+0x140`) and `rate = 2^(semis/12)` as fixed point
  with 14 fraction bits (`+0x160`). +24 gives `0x10000` (4.0): an ordinary value.
- **The resampler can't overrun.** `Resampler_Process` (ITCM `0x12DC0`) advances a Q14
  position per output sample and pulls source samples through `Ring_Advance` (`0x13BEC`) from a
  ring of `0x2800` samples. Every index wraps, and `Ring_Advance` refuses a step that would
  overtake the writer (the block then ends early). Blocks are `0x800` output samples, so 4×
  needs 8,192 source samples per block, which fits in 10,240. Worst case at extreme
  rates (with SPEED > 100 % on top): dropouts or graininess, not memory corruption.
- **Already exercised:** MIDI-triggered chromatic notes pass `(note & 0x1F) − 13` = −13…+18
  to the same path.
- `0x14338` (`Stretch_ClampRatio`) limits the ratio-modulation depth so that ratio ± depth stays within 0.1…1.9; it doesn't touch pitch ([time-stretch.md](time-stretch.md)).

**The real side effect is in `Param_GetRange`:** `0x73` (pad PITCH) and `0x9B` (chromatic range)
share the case code at `0x800D38C8`. The two-byte test patch `pitch-range-24` therefore also
lets every pad's PITCH go to ±24, and a project saved like that may misbehave on stock firmware.
It's for listening tests only.

**Proper fix (with the injected code):** point the `tbh` entry for `0x9B` (halfword at
`0x800D3604 + 2*0x9B`) at `0x800ED544`, a 4-byte padding gap, holding `ldr.w pc, [pc, #0xC]`.
That loads the stub address from the padding gap at `0x800ED554`. The stub, in our appended code,
does `min = −24; max = 24; pop {r4, r5, r6, pc}` (registers as in the original case: `r5` = &min,
`r4` = &max). Nothing in reach has 12 free bytes for the stub itself; the 8-byte zero run at
`0x800ED530` is really the end of the string `"%4ld"`.

## Implementation: "Scale pads" (2026-10-25)

Code in `app1/payload/`, built and appended by `app1/tools/make_image.py` (patches `scale-pads` and
`scale-pads-wide`, one choice in group "chromatic"):

- `app1/payload/inkey.c`: `inkey_fill()` computes the 16 table values for a scale, root and offset;
  `inkey_update()` writes them into all four tables. For SCALE = Chrom it writes Roland's
  original layout back. Layout: bottom-left pad = root an octave below the sample's pitch
  (`root − 12`), scale steps left to right, then row by row upward. `chrom_offset` now counts
  scale steps (knob: a row, SHIFT: one step), because the table holds `semi + offset`.
- `app1/payload/hooks.S`: the entry points. Chromatic page vtable `0x802213F4`:
  slot `0xD8` (word `0x802214CC`) → `hook_chrom_on_key` (runs `ChromPage_OnKey`, then updates);
  slot `0xFC` (word `0x802214F0`) → `hook_chrom_pad_down` (updates, then tail-calls);
  slot `0x118` (word `0x8022150C`) → `hook_chrom_build` (updates before the page is built).
  `range_chrom_24` is the `0x9B` case of `Param_GetRange` (±24), reached via the switch entry at
  `0x800D373A` → `0x800ED544` (`ldr.w pc, [pc, #0xC]`) → word at `0x800ED554`.
- `app1/payload/payload.ld`: stored at flash `0x60308000` (after APP1's end at `0x60307A00`), runs
  from SDRAM `0x83FE0000` (copied at boot; [payload-ram.md](payload-ram.md)). No writable
  globals allowed.
- `app1/payload/test_inkey.c`: host test of the layout for all scales × roots × offsets, including
  Roland's own scale check (`gcc -DHOST_TEST ... && app1/build/test_inkey`).
- `scale-pads-wide` adds the two-byte change to the shared PITCH/`0x9B` case, so every pad's
  PITCH reaches ±24 (incompatible project files, the UI warns).

Tested on the device 2026-10-25: works as intended. Things that were checked: the pad grid numbers after changing SCALE, NOTE
(knob with key `0x21` held) and the offset; pad lights; that SCALE = Chrom restores the old grid.

## Voice limit and pitch base (2026-10-25)

Why: with VARI Plus several chromatic voices use up the CPU ([cpu-meter.md](cpu-meter.md)),
and the pitch/speed panel's PITCH was ignored in chromatic mode, so a sample couldn't be
tuned (e.g. a few cents off C). Patch `chrom-voices` (`app1/payload/chrom.c`), independent of
`scale-pads`: both point the chromatic page's key and pad-down methods at the same hooks in
`chrom.c`, and each switches its part on with a flag byte in the payload (`chrom_voices`,
`chrom_layout`) that `make_image.py` sets. `make_image.py` accepts an identical change from a
second patch ("already applied").

**Chromatic mode** (param `0xF3`, bits 3–4 of pad param `0x70`): 0 MONO, 1 LEGATO, 2 POLY.
REMAIN (key `0x24`) on the chromatic page steps `g_chrom_mode_next` (0→1→2→0) and shows
`g_chrom_mode_labels[v]` with `Popup_Show`. `Pad_Trigger` reads it for the chromatic pad
(`Param_Get(0xF3, Param_Get(0xC1))`); LEGATO is `(word & 0x18) == 8`.

**Voice count:** bits 6–8 of pad param `0x70`: 0 (as in every existing project) = no limit, like
Roland's POLY ("POLY inf"); 1..7 = 8..2 voices (`9 − value`). Nothing else reads those bits: all readers of `0x70` mask theirs (`0xF5` bit 0,
`0xF6` bits 1–2, `0xF3` bits 3–4, `0x100` bit 5). **But every runtime write is validated**:
`SmplPrm_Set` (which applies the posted message) runs `FUN_8001A570`, whose case `0x70` sets the
whole word to 0 when it's above 0x3F (`0x8001A8D4`: `cmp r2, #0x3f; it hi; movhi r2, #0`). Second
device test: POLY 8 (bits 0) worked, POLY 2–7 became MONO with VARI Off. The patch changes the
check to "above 0x1FF" (`lsrs r0, r2, #9; it ne; movne r2, #0`). Project loading copies pad
words unchecked (`FUN_8018AE68`, byte-swapped), so the count is saved with the pad. On stock
firmware such a project plays normally (readers mask their bits), but editing VARI or the
mode of a pad set to POLY 2–8 would trip Roland's check and reset the pad's word to 0 (the patch's warning).
Alternative not taken: our free RAM (`app1/payload/ram.h`) isn't saved and is empty at power-on.

**REMAIN held + VALUE wheel** (keys `0x3F`/`0x40`): LEGATO ↔ MONO ↔ POLY 2 … POLY 8 ↔ POLY inf, with a pop-up
("POLY 4 voices"). A REMAIN press alone only shows the setting; Roland's cycle is off.

**Parameter writes are asynchronous.** For ids below 0x80, `Param_Set` doesn't write: it posts
a message (`Param_Post`, `FUN_800D5920` → `FUN_80018400`, type 0xA5) that PARAM_TASK (priority
31, the lowest) applies later. A read right after a write still returns the old value. First
device test: the wheel set the mode with `Param_Set(0xF3)` and then the count with a second
write built from a fresh read, which still had the old mode; the second message undid the
first (stuck at LEGATO/MONO/POLY 2, mode not applied). Now each step writes the whole word
once and keeps a copy (`CHROM_RAM`, `app1/payload/ram.h`) that later steps and the limit use for a
second, while the store may still be behind.

**The limit:** before Roland's `ChromPage_OnPadDown`, if the chromatic pad is in POLY with a count and
the sample already plays N notes, the oldest is released with `Voice_Release(slot, 0xC)` (as
a second press would), repeatedly until one fits. A slot counts when its state word is 1
(2 is an Ensemble note's second slot), its trigger type (`+0x2C`) is 0xC, its voice plays the
chromatic pad (`voice + 0x14274`) and isn't stopping already (`voice + 0x1439C`). Oldest =
smallest start number (`slot + 0x14`, from `g_voice_seq`). Pressing a note that already
sounds (`slot + 0x4C` = the note) doesn't steal: Roland handles that itself.

**Pitch base:** the pitch/speed panel's PITCH and FINE are pad params `0x73`/`0x74` (`0x6F` is
ORIGINAL BPM). `Pad_Trigger` sets a chromatic note's pitch in **two** places:
1. the parameter block for `Voice_Start` (`Pad_ReadParams` copies the pad's 0xAC-byte record;
   params from `0x6A` at +0x10, so `+0x34` PITCH, `+0x38` FINE, `+0x40` SPEED): PITCH = note,
   FINE = 0 at `0x800D71BA` and `0x800D72CE`. Patched to PITCH = pad PITCH + note, FINE kept,
   in the same 10 bytes.
2. directly on the voice afterwards, `Voice_SetParam(slot, 0x73, note)` and `(slot, 0x74, 0)`:
   after `Voice_Start`, and when MONO/LEGATO retune a playing voice (six calls,
   `0x800D73CC`–`0x800D7456`). This overrode (1): the first device test still played the
   unpitched note at grid 0 with PITCH +11. Fixed with an entry hook on `Voice_SetParam`
   (`0x800A2C98`, its first four instructions → `ldr.w pc, [pc]` + `hook_voice_set_param`;
   `voice_set_param_orig` in `hooks.S` runs those four and continues at `0x800A2CA0`, an
   `it eq` that needs the `cmp` flags, which `ldr pc` keeps). Only for return addresses in that
   range and ids `0x73`/`0x74` it adds the pad's value (`PadStore_Get(g_pad_store, id, pad)`,
   pad from voice + 0x14274). Other callers, like `FUN_800D7A80` (pushes parameter edits to
   playing voices), pass unchanged.
SPEED was never replaced, so it already worked as a base.
The pad grid still shows the plain chromatic numbers, and the ±24 range check applies to the
note alone.

Open: MIDI-triggered chromatic notes aren't limited (only pad presses).
