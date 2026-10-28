# Guitar tuner (goal #5)

## Summary

BANK A + BANK B pressed together on the top screen opens a tuner page that shows the nearest
note and the cents. **Works on the device** (user, 2026-10-27: "works great"), and it's on the
website patcher ([web-patcher.md](web-patcher.md)). Patch `tuner` in
`app1/tools/make_image.py`, code in `app1/payload/tuner.c` (page) and `app1/payload/pitch.c`
(capture and detection), drawn with the custom-page module ([custom-pages.md](custom-pages.md)).

## How Roland does BANK D + BANK E (DJ mode)

Found by decompiling every caller of `Key_IsHeld` and grepping for `Key_IsHeld(0x25..0x29)`.
In `TopScreen_HandleKey` (`0x800C9788`), case `0x28` (BANK D, key down) and `0x29` (BANK E):

```c
if (key down && !(SHIFT held && ...)) {
    if (Key_IsHeld(other one) && UI_GetCurrentPage() == 1) {       // only on the top screen
        PadStore_Set(&g_pad_store, 0, g_dj_saved_bank, 0);      // undo the first key's bank change
        g_bank_key_last = g_dj_saved_bank % 5 + 0x25;
        g_bank_f_to_j[...] = 0; g_bank_key_index = 0;
        if (g_dj_saved_bank > 4) g_bank_f_to_j[key] = 1;      // F..J
        UI_RequestPage(0x1F);                                   // page 31 = DJ mode
    }
}
g_dj_saved_bank = PadStore_Get(&g_pad_store, 0, 0);             // every plain D/E press
```

The first key of the pair has already switched the bank by the time the second arrives. That's
why the bank is saved on every press and put back on the combo.

`Key_LightLevel` (`0x8009AAB8`) also tests `Key_IsHeld(0x25)` and `(0x26)`, but only for the
button lights (lit while held). BANK A + B has no meaning anywhere else.

## Our combo

The top screen's vtable (`vt_TopScreen`, `0x8022CE5C`) overrides the raw key-down slot
`0x18` (`TopScreen_KeyDown`, `0x801839B8`), from which most keys reach `TopScreen_HandleKey`.
Patch `tuner` points that word (`0x8022CE74`) at `hook_top_key_down`, which does what DJ mode
does: on BANK A/B with the other one held, on page 1, it restores the saved bank and calls
`cpage_open(&tuner_page)`. On a plain BANK A/B press it saves the bank. Everything else goes
on to `TopScreen_KeyDown`.

## The screen (stage 1)

```
TUNER                A4=440
            A
-12c          4
    ---------+---------
   |    |    ^    |    |
```

Title row (font S), the note name (font L3, centred) with its octave, a frame around it when
within ±2 cents, the cents value on the left, and a meter from −50 to +50 cents with ticks
every 10 and a needle.

## Audio input (2026-10-26)

What the code says:
- The **audio task** (`Audio_Task`, ITCM `0x1CF78`) programs eDMA channel 3 (transmit: four
  SAI lines, buffers from `0x20000000`) and channel 4 (receive: `0x20008000`, two halves of 64
  frames × 8 words). On each transmit event it runs `Audio_Mixer`; on each receive event,
  `Audio_RxHandler` (`0x1CA80`).
- Each receive word is a stereo pair (left in the high half). Word 0 is the SP's own mix
  coming back (Tallfree's receive probe). So mixing and the effects, input FX included,
  appear to run outside the M7 (a DSP), which sends channels back.
- `Audio_RxHandler` copies **one** word, the recorder's source, into `g_rx_selected`
  (`0x202AD0A0`), keeps peak meters, and feeds the sampler. Which word: 1 if `0x82E011A8` is
  set, else the byte at `0x82E0E974` (`g_rec_ctrl + 0x258`).
- Candidates for muting: parameters named `InputVolume` / `INPUT LEVEL`, `Gain_Input`,
  `InputFxOnOff`, `Input Bus`.

The device has to tell us which word is the instrument input and whether it's before or after
the input FX and the input level. **Probe** (in the tuner patch): VALUE press on the tuner
page switches to 16 bars, the peak of every receive channel (pairs L R, word numbers under
them, 6 dB per step), and the word the recorder uses now. To do:
1. Guitar into the input, play: which bars move?
2. Input FX (e.g. Gt Amp Sim) on/off: does that bar change (post-FX) or not (raw)?
3. INPUT LEVEL down to 0: does it still move? If yes, that word is before the input level,
   and turning the input level down while tuning mutes the input for everything else while
   we still hear it. Otherwise: mute the main out instead.

**Result (2026-10-26, measured with the probe):** the guitar shows up on receive **word 0**,
**wet** (after the input FX), i.e. in the finished mix. There is no raw input channel among the
eight. Decision: tune from word 0 and don't mute anything for now.

## Pitch detection (stage 2, `app1/payload/pitch.c`)

- **Capture:** the patch replaces the first 14 bytes of `Audio_RxHandler` (ITCM `0x1CA80`)
  with a jump to `hook_audio_rx`. While the tuner is open, that adds L + R of word 0,
  averages pairs of frames (24 kHz), writes them into a 2048-sample ring (`PITCH_RAM`), then
  runs the replaced instructions (`audio_rx_orig`, `hooks.S`) and continues in Roland's code.
- **Detection** (revised 2026-10-26 after the first test: CPU-heavy and twitchy), on the UI
  tick (every 50 ms):
  - buffers from Roland's heap while the tuner is open (`PITCH_RAM` was non-cacheable until 2026-10-28; the
    first version's inner loops ran there and were most of the cost);
  - YIN at 12 kHz (pairs averaged), 512-sample window, stopping at the first dip under 0.15
    once it has passed its minimum (else the deepest dip if under 0.2, else "unsure"); then
    the plain difference function at 24 kHz at ±2 samples around twice that period,
    interpolated. 10k–80k multiply-adds per analysis instead of 614k;
  - an attack (newest 21 ms 6 dB louder than 50 ms before) skips 100 ms of analysis;
  - offline check: ≤ 0.16 cents on the open strings, 1.2 at 1.3 kHz.
- **Display:** the median of the last 3 estimates (with 2: their mean if within 30 cents),
  smoothed 30 % per tick, a jump for changes over 50 cents; the note name only changes once
  the pitch is 60 cents from it; held 0.5 s after the sound stops, then "-". The screen is
  redrawn only when the note, needle pixel or cents change.

## EXT SOURCE on (2026-10-26)

The instrument only reaches the mix (receive word 0) with EXT SOURCE on. `tuner_open` switches
it on when it's off: `PickStore_Get(&g_pick_store, 0x25, 0)` is the state (store `+0x5C`,
Tallfree), and `ExtSource_Toggle(_, 1)` (`0x80134A98`, what `TopScreen_HandleKey` calls for key
`0x12`) does what a button press does: `PickStore_Set(0x25, …)`, redraw flags (the button
light) and a message, presumably to the DSP. It's left on when the tuner closes. Not yet
tested on the device.

## Diagnostic views

VALUE press cycles: tuner → input probe → sample probe (3 views, `app1/payload/sprobe.c`, see
[sample-loop.md](sample-loop.md)) → tuner.

## Next steps

1. ~~Test stage 1 on the device~~ Done 2026-10-26: combo, EXIT and bank, drawing all fine.
2. ~~Audio input~~ word 0, captured as above.
3. ~~Test pitch detection on the device~~ Done: works (user, 2026-10-27).
4. Later: muting the input while tuning, the pads as a meter, A4 calibration.
