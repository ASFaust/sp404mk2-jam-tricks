# Sound Generator (SHIFT + REC SETTING)

## Summary

The Sound Generator (added in v4.04) is **entirely APP1 code**: a single software oscillator
on the i.MX, computed sample by sample inside `Audio_Mixer` and added to the mix bus like the
looper. It can be read and patched
like any other APP1 feature: new waveforms, a different noise, an envelope, etc.

The state is one global object, `g_sound_gen` (`0x80249800`, struct `SoundGen` in
`app1/tools/types.txt`).

## Key functions

| Address | Name (as in Ghidra) | What it does |
|---|---|---|
| `0x0001C2D0` | `Audio_Mixer` | per 64-frame block: 32 voices, `Looper_MixBlock`, then `SoundGen_MixBlock` |
| `0x0001CB84` | `SoundGen_MixBlock` | ITCM; if `gate`: per frame, step `level` toward `level_target`, get a sample, pan it (`pan` −50…50, ±51 hard L/R) into `g_mix_bus` at `bus` |
| `0x80007700` | `SoundGen_NextSample` | the oscillator: switch on `wave`, scale by `level / 0x319C`, advance the phases, 200-sample fade in/out (`fade`, `releasing`); a pending wave change (`+0x71`) is taken at the end of a fade |
| `0x80131C48` | `SoundGen_FormatValue` | value text for params `0x7A`–`0x82` (On/Off, wave names, `"%s%d:%.1fHz"`, pan Left/Right, …) |
| `0x80132428` | `SoundGen_NoteOnOff` | pad down/up → note on/off |
| `0x801326F8` | `SoundGen_Start` | sets up the oscillator before the gate opens |
| `0x8015C228` | `SoundGenPage_Build` | background `bg_3row_m2`, `"SHIFT:OTHERS"`, three knob items (params `0x7B`/`0x7E`, `0x7C`/`0x7F`, `0x7D`/`0x80`) |
| `0x8015BF90` / `0x8015C4B0` | `SoundGenPage_OnPadDown` / `_OnPadUp` | chromatic pads: note = table `0x801A9E60`[pad] + 12·octave − offset; key `0x13` toggles the gate |
| `0x0001EFB0`, `0x0001E8D8` | `sinf`, `cosf` | C library |

## Details

Waves (`SoundGen.wave`, param `0x7B`):

| # | Name | How (from `SoundGen_NextSample`) |
|---|---|---|
| 0 | Sine | `sinf(phase_rad)` |
| 1, 2 | Sine1, Sine2 | sine, low bits masked off (`& 0xFFFFFF00` / `& 0xFFFFF000`): bit-crushed |
| 3 | Cos | `cosf(phase_rad)` |
| 4, 5 | Cos1, Cos2 | cosine, bit-crushed the same way |
| 6 | Saw | linear in `phase` |
| 7 | Saw+ | saw with a fold (`phase + 0.25`, mirrored) |
| 8 | Saw2 | saw variant (falls through into the Saw code) |
| 9 | Tri | triangle |
| 10 | Tri2 | triangle mixed with a sine on the second phase, ratio from `width` |
| 11 | Pulse | ±level, switching at `width/100` of the cycle |
| 12 | Pulse+ | one-sided pulse (0 or +level) |
| 13 | Noise1 | `Rand_Next()` white noise, gated by `width` |
| 14 | Noise2 | white noise through a three-pole filter (states `pink0..2`): pink-ish |

Types 0–5 use the radian phase at `+0x30`; the rest use the 0…1 phase at `+0x18`.
Frequency: `fine/10 · 2^((note − 33)/12)` (shown as note name + octave + Hz).

**Why the text search failed.** None of "Sine", "Saw+", "Noise1", "Freq", "SHIFT: OTHERS" exist
as strings: the compiler turned the short `strcpy`s into `movw/movt` + `str` of the bytes
(e.g. `0x2B776153` = `"Saw+"`). See [ui-pages.md](ui-pages.md#where-ui-text-is) for how to
find such text.

## Open questions

- Exact formulas of Saw2, Tri2 and Pulse+, and the constants at `0x800079D0`–`0x800079F8`.
- Param `0x7E` (`time`, shown as "sec." / "period") and `0x81`/`0x82`: what they do.
- `SoundGen_NoteOnOff` and `SoundGen_Start` in detail; what fields `+0x02`/`+0x03` mean.
- How the generator's output gets resampled onto a pad (if it does).
