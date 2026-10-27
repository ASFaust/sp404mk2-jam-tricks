# Time stretch and pitch shift (goal #4)

## Summary

The SP-404MKII does **not** use an STFT or phase vocoder. All pitch/tempo processing is
one time-domain routine, `Resampler_Process` (ITCM `0x12DC0`), a **splice stretcher**:

- **Pitch** comes from reading the source at `rate` (Q14 fixed point, `2^(semis/12)`)
  with **linear interpolation**.
- **Tempo** comes from comparing that read position with an "ideal" position that moves
  at the time ratio. When they drift more than one splice period apart, the reader
  **jumps forward or back by one or two periods** and **crossfades linearly** from the
  old read point to the new one over one period. It's the classic "rotating tape head"
  method, as in old samplers and harmonizers. There are no grains, windows or overlap-add.
- Three variants, selected per pad by the hidden **VARI** setting: **Off**, **Backing** and
  **Ensemble**. VINYL is the same routine with ratio = rate, so it never splices.

Analysis cost is tiny: two linear-interpolated taps per channel per sample, plus (Ensemble
only) a 200-product correlation every 35 samples. There is plenty of room for a better
time-domain algorithm (see "Budget").

## How a pad gets its algorithm

On the pitch/speed panel (`PitchPanel_Draw`, `0x800F7518`), holding key `0x2A` shows two
rows instead of "BPM VALUE": **VINYL** (param `0x77`) and **VARI** (param `0xF6`,
values *Off / Backing / Ensemble*, plus an unused label `TBD2`). VARI shows "Off" while
VINYL is on.

| UI | param | stored in | voice field |
|---|---|---|---|
| VINYL | `0x77` | pad `+0x114` | voice word `0x3A` |
| VARI | `0xF6` | bits 1–2 of pad param `0x70` (pad `+0xF8`) | `Stretcher.mode` = `(v & 7) >> 1` |
| (`0xF5`) | `0xF5` | bit 0 of `0x70` | — |
| SPEED | `0x76` | | voice word `0x39` (500…15000 = ×0.05…×1.5) |
| PITCH, FINE | `0x73`, `0x74` | | `Stretcher.semis`, `rate_q14` |

`Param_Set` (`0x800DED18`) cases `0xF5`/`0xF6` read-modify-write param `0x70`. The pad setter
`SmplPrm_Set` (`0x80186BC0`) and the second setter at `0x800DA720` rebuild the voice when bits 1–2
change. `Voice_SetParam` case `0x70` re-runs `Stretch_SetXfadeTables`.

At voice start (`SmplCharge_Init`, `0x800231F8`, "CSmplCharge::Init"):
`Stretch_Init(stretcher, &g_stretch_state[voice], mode)`, then the period is set to
**600 / 400 / 800** samples for Off / Backing / Ensemble. In the DJ/tempo-loop path
(voice word `0x45` = 1) the mode comes from voice word `0x44` instead (0–1 → Off, 2 → Backing, 3 →
Ensemble).

There is also a global **"TS Algo"** (param `0x9C`, `g_ts_algo` `0x80245984`, labels
`FA` / `BK-9` / `HS 5B`, default 1). Only `Param_Get/Set/FormatValue` touch it; no DSP code
reads it. It's probably a leftover debug setting.

## Data flow

```
Pad_Trigger → Voice_Start → SmplCharge_Init (mode, Stretch_Init)
voice streaming (FUN_800E5C68 / FUN_800E9960), whenever an output slot is free:
  Voice_RenderSetup(voice)                         0x800E3F78
    rate_q14 = 2^(semis/12)·16384
    ratio    = VINYL ? rate : SPEED/10000 (or project BPM / sample BPM)
    fill source rings (Ring_FillStereo/Mono: int16 → int32 << 13)
       until 40 % (Off/Backing) or 60 % (Ensemble) of the 0x2800 ring is ahead
    Resampler_Stereo/Mono(stretcher, out, 0x800) → Resampler_Process
```

The rendering runs in **blocks of 0x800 output samples, from the streaming code, not from
the 64-sample audio interrupt**. A new algorithm must keep up on average and may use block
look-ahead. Not confirmed yet: which task runs `FUN_800E5C68`, and with what priority.

## The algorithm (Resampler_Process)

State: `Stretcher` (in the voice at `+0x140`), with the two `StretchRing`s holding
0x2800 int32 samples each (213 ms at 48 kHz), and `StretchState` (OCRAM `0x2023F000 + voice·0x328`).
Per output sample:

```
ideal_pos += (ratio + modulation) · 16384           # where we should be, Q14
frac      += rate_q14;  Ring_Advance(frac >> 14)     # where we are
if mode > 0: Stretch_TransientGuard()                # may set holdoff
holdoff--

switch splice:
  0 (straight):  out = lerp(ring[read], ring[read+1], frac)
  1 (forward):   out = g_out·lerp(read) + g_in·lerp(read + period)
  2 (back):      out = g_out·lerp(read) + g_in·lerp(read − k·period)
                 g_out -= step; g_in += step  (step = 1/(period−1), Q28, linear)
                 at the end: Ring_Advance(±k·period), period re-chosen, maybe chain another splice

when splice == 0 and holdoff < 0:
  if read + 600 < ideal  → start forward splice (skip)
  if read − period > ideal → start back splice (repeat), k = 1, or 2 if > 2 periods ahead
  holdoff = 1024
every 35 samples (25 before the first result), Ensemble only: Stretch_CorrelateStep()
out = clip(acc >> 13)
```

Interpolation is linear with a 12-bit fraction (`(frac·2^14 + 0x8000) >> 16`). Both
channels share one splice decision (good stereo image). All analysis looks at the left
ring only.

### The three modes

| | Off (0) | Backing (1) | Ensemble (2) |
|---|---|---|---|
| splice period | 600 (12.5 ms) | 400 (8.3 ms) | from correlation, ≥ 600 (start 800) |
| transient guard | no | yes | yes |
| periodicity search | no | no | yes |

**`Stretch_TransientGuard`** (`0x13C58`) reads the absolute value of *one sample 2000 samples
ahead* of the reader, as a peak envelope (decay ×0.9999 per sample). About 1000 samples after the
envelope last rose (that is, around a loud event entering the window), it blocks splicing for
2000 samples and re-arms after `g_guard_hold` = 3000. The effect is no splices close to attacks.
It works sample by sample on raw peaks, so it's crude.

**`Stretch_CorrelateStep`** (`0x13DE0`, Ensemble) handles one lag per call: the
cross-correlation of 400 samples at `corr_anchor` (= read + 1200) with the same signal `lag`
later, using every second sample at about 9-bit precision. Lags are 400 + 5k for k = 0…99
(400–895 samples), so one sweep takes about 3,500 samples. The best lag becomes
`detected_period`; `Stretch_PeriodFromLag` (`0x13D9A`) doubles it until it's ≥ 600. Splices
then jump by a multiple of the main repetition period of the material (pitch-synchronous splicing).
There is **no search at the actual splice point**: the period is measured at one place and
used wherever the next splice happens.

`Stretch_FallbackPeriod` (`0x13D82`) halves the period when there isn't enough data ahead
for read + 2·period.

### Unused in the firmware

- **Equal-power crossfades.** `Stretch_SetXfadeTables` (`0x80025858`) installs sine/cosine
  tables (Q28) for 15 period lengths (200…1800, e.g. `g_xfade_in_600` `0x802D6CE0`). The process
  uses them only when `mode < 0`, which nothing sets. They're ready to use.
- `0x805968DD` switches the correlation to an energy-normalised version (the energy is computed
  for k = 0). It's never set in normal operation.

### Ratio modulation (open question)

`StretchState` `0x2B8…0x324` modulates the ratio periodically with a cycle derived from the sample
BPM. Shape comes from pad param `0x8A` (0 = off; `(n−1) % 4` picks one of 4 curves), depth from
`0x8B`, random depth ±7/14/21 % per cycle from `0x8C`, using `Rand_Next` (`0x4EC`).
`Stretch_ClampRatio` (`0x14338`) lowers the depth so that ratio ± depth stays within
0.1…1.9. Which UI sets `0x8A…0x8C` (pad store `+0xF61C + pad·0x80`) is still unknown.

## Why it sounds the way it does

1. **Fixed, unaligned splices (Off, Backing).** Jumping exactly 600/400 samples ignores the
   signal's period, so the crossfade mixes two out-of-phase copies: comb filtering,
   "phasiness" and roughness on tonal material, and a doubled sound on held notes.
2. **Coarse alignment (Ensemble).** The lag grid is 5 samples, the search runs only on the left
   channel at 9 bits, it's measured away from the splice, and the result is doubled up to ≥ 600. A
   5-sample error cancels at about 4.8 kHz, so highs smear while lows stay clean.
3. **Linear crossfade only.** It's right for well-correlated segments; for uncorrelated ones it
   dips by about 3 dB in the middle (audible "pumping" on noise or reverb tails).
4. **Crude transient handling.** A single-sample peak with 2000-sample look-ahead can miss
   onsets or react to bass waveform peaks. Attacks can still land inside a crossfade (doubled or
   blurred transients), or a repeat splice can play them twice.
5. **Pitch via linear interpolation.** No low-pass on up-shifts (aliasing, worst at +12…+24
   where rate reaches 2…4) and high-frequency loss on down-shifts.
6. **Formants move with pitch** (resampling). That's inherent to this design.

## Improvement avenues (time domain, no STFT)

Ordered by value for effort. All fit the existing interface (`Resampler_Mono/Stereo(stretcher,
out…, 0x800, &pos)`), so a replacement can go in at the SDRAM thunks `0x800D2E38`/`0x800D2E2E`
that `Voice_RenderSetup` calls.

1. **WSOLA alignment at every splice.** When a splice is due, search an offset δ ∈ [−Δ, +Δ]
   around the nominal jump that maximises the normalised cross-correlation between the outgoing
   and incoming segments (L ≈ 256–512 samples, on the L+R sum). Coarse to fine: on 4× decimated
   data, step 1, then refine ±4 at full rate. Roughly 30–40 k multiply-adds per splice, with
   splices ≥ 1000 samples apart, gives **≈ 40 MAC per output sample per voice**. Fixes points 1 and 2.
2. **Crossfade shape from the correlation.** Linear when the match is good, equal-power when it's
   poor. The tables already exist. Fixes point 3 almost for free.
3. **Transient-aware splice scheduling.** A proper onset detector (short-frame energy flux,
   e.g. 64-sample frames, high-passed, using the look-ahead in the ring). Splices go in stationary
   stretches, never across an onset, with a guard of 20–50 ms after it. When the reader has to
   catch up, do it *just before* an attack so the attack plays once and on time. Fixes point 4.
4. **Adaptive splice length.** Long (2–4 periods, 20–40 ms) on tonal material, short
   (5–10 ms) on percussive material, chosen from the correlation peak and the onset density.
5. **Better interpolation for pitch.** 4-point Hermite (≈ 10 multiply-adds), or polyphase
   windowed sinc (8–16 taps, cutoff lowered when rate > 1). ≈ 16–32 MAC per channel. Fixes
   point 5.
6. *(Later, optional)* **TD-PSOLA** for monophonic sources (vocals) to keep formants when
   pitching. Needs pitch marks; a separate project.

**Phase Gradient Heap Integration (PGHI)** rebuilds STFT phase from the gradient of the
magnitude. It only exists in the STFT domain, so it's out with the decision not to implement an
STFT.

## Budget

- CPU: i.MX RT1062, Cortex-M7 at 600 MHz → **12,500 cycles per output sample for everything**
  at 48 kHz. Roland's spec is 32 voices. If 32 stereo voices stretched at once with 100 cycles
  per voice-sample, that's 3.2 k cycles (≈ 25 % of the CPU). Nothing has been measured yet; the
  current cost is estimated from the code at roughly 40–80 cycles per channel-sample.
  Tallfree warns that the cycle counter is unreliable here; measure with a timer or GPIO instead.
- Memory per voice: 2 × 40 KB rings (in SDRAM, inside the 0x28820-byte voice object), with about
  4,000–6,000 samples of look-ahead (fill level 40 % / 60 %). A WSOLA search of ±500 samples and
  an onset look-ahead of 1,000–2,000 samples both fit.
- `StretchState` has 0x328 bytes in OCRAM; new state can go after the used fields or in our
  own appended RAM.
- Code: ITCM is writable; new DSP code could be copied to free ITCM, or run from SDRAM (cached).

## A fourth VARI option

The new stretcher goes in as an additional VARI value. The firmware already has room for one:

- `Param_FormatValue` case `0xF6` already has a fourth label, `TBD2`, for value 3.
- `Stretcher.mode` is `(v & 7) >> 1`, so it holds 0–3.
- What's needed: raise the `0xF6` range in `Param_GetRange` (`0..2`, shared with other ids in that
  case) to 3, give value 3 a name, and send mode 3 to our code at the `Resampler_Mono/Stereo`
  thunks (`0x800D2E38` / `0x800D2E2E`).
- Today a mode-3 voice would behave like Backing with an 800-sample period: guard yes, correlation no.
- Compatibility: a project saved with VARI = 3 would play as that on stock firmware.

## Prototype: `tsplice` (2026-10-25)

Code in `app1/proto/stretch/` (README there): `fwmodel.py` reproduces the firmware's three modes,
`tsplice.py` is the new one, `render.py`/`metrics.py` compare them with rubberband (an offline
reference, far too heavy for the SP) on 17 clips from the user's library plus synthetic signals.

**Design.** The same reader/ideal-position/splice architecture as the firmware, so it fits the
ring and the streaming code. What's new:

- **WSOLA jump search.** Normalised cross-correlation on L+R. The coarse pass runs on an 8× box-filtered,
  decimated copy (2,048-sample window, about 1.5 cycles of 30 Hz bass); the fine pass runs at full
  rate (512 samples, ±8 lags). The jump must land within 0.5–1.9× the drift-correcting jump, so
  every splice at least halves the drift without ping-pong.
- **Wait for a good match.** It demands a correlation of 0.95 up to twice the threshold, then
  relaxes linearly to 0 at the hard limit (2,400 samples, 50 ms). Before re-searching it waits
  until the drift has moved by 512 samples, and drift pointing against the natural direction
  (an overshoot) is left to heal by itself. Two silent segments count as a perfect match.
- **Correlation-aware crossfade.** Linear shape, gains divided by
  `sqrt(a² + b² + 2·c·a·b)`, so the level stays constant for any correlation; 480 samples.
- **Onset map.** 64-sample frames of first-difference energy; an attack is a frame more than
  2× the loudest of the previous 24 frames (32 ms), with at least 50 ms between attacks.
  Comparing with the recent maximum (not the mean) is what keeps beating chords from
  counting as attacks.
- **Attack rules:**
  - no splices within 1,200 samples after an attack
  - splices may happen just before one, to catch up
  - a fade must end before the next attack (otherwise it's heard twice)
  - forward jumps land on an attack (96-sample fade) instead of skipping it, unless that overshoots
  - backward jumps never go to before the last attack
- **Interpolation.** Blackman-windowed sinc, half-width 6 (12 taps), cutoff 0.92 (÷ pitch rate
  when pitching up); a plain copy at pitch 1.0.

**Results** (synthetic; `render.py --synth`):

| | firmware (best mode) | tsplice | rubberband |
|---|---|---|---|
| steady tone, purity (dB) | −30…−37 (Ensemble), but 0 at +12 | −30…−42 | −15…−39 |
| 4-note chord, purity (dB) | −15…−29 (Ensemble) | −28…−32 | −15…−24 |
| click train at half speed: extra peaks per click | 1–3 | 0 | 2 |
| click timing error (ms) | 4–50 | 1.4–11.5 | 3–31 |
| aliasing, 14 kHz tone at +12 | full level (linear interpolation) | −31 dB | — |

Real clips (mean over 17), unmatched extra onsets per real onset (doubled or invented
attacks, bumps; lower is better):

| setting | Off | Backing | Ensemble | **tsplice** | rubberband |
|---|---|---|---|---|---|
| 50 % speed | 3.73 | 4.76 | 4.70 | **0.96** | 0.87 |
| 80 % | 2.71 | 3.57 | 1.54 | **0.36** | 0.51 |
| 125 % | 1.96 | 2.13 | 1.00 | **0.10** | 0.17 |
| 150 % | 1.18 | 1.36 | 0.99 | **0.09** | 0.14 |
| +7 st | 2.59 | 2.22 | 2.66 | **0.46** | 0.22 |
| +12 st | 1.47 | 1.68 | 1.48 | **0.67** | 0.40 |
| −5 st | 2.37 | 2.45 | 1.45 | **0.95** | 0.33 |
| −12 st | 2.76 | 2.46 | 1.95 | **1.69** | 1.49 |
| +5 st, 80 % | 2.76 | 3.07 | 2.58 | **0.57** | 0.36 |

Onset timing error is 10–20 ms for everyone. These are numbers, not listening results; the
listening page is `app1/proto/stretch/work/listen.html` after `python render.py`.

**Cost** (counted in the model, multiply-adds per output sample, stereo voice):

- search: 15 (time only), 36 (+7), 67 (+12)
- interpolation: 0 at pitch 1.0; 12 taps × pitch × 2 channels, ×1.3–1.6 during fades
  (≈ 50 at +7, ≈ 75 at +12)
- total: ≈ 20 (time only) to ≈ 145 (+12), against the firmware's ≈ 10–20

At an assumed 1.5–2 cycles per multiply-add that is up to about 300 cycles per sample per voice at
+12 (2.4 % of the CPU per voice). Fine for a few voices, too much for 32 at once. Ways down for
the C version: a polyphase table instead of computing sin/cos, int16 SIMD (`SMLAD`, 2 multiply-adds
per cycle), running energy sums in the search, and a 3-stage search.

**Weak spots so far:** pitching down (−5, −12) adds more extra onsets than rubberband;
onset recall is a little lower than the firmware's.

### Listening round 1 (user, 2026-10-25) and changes

The user listened to all renders. tsplice is clearly better than the stock modes. Piano (a chord)
is unrecognisable with the stock modes and good with tsplice. ep_chord at 50 % is better than
rubberband, which adds a disharmonious frequency. "mix" is a khaen (mouth-organ) recording:
chords with fast amplitude modulation, a hard benchmark. Pitching down sounds better than pitching up.

| Heard | Cases | Cause found | Change |
|---|---|---|---|
| Warbled, louder first pluck; doubled release | guitar up5slow, slow80 | 7–8 catch-up splices at correlation 0.0–0.3 *inside* the rolled strum; the strum's plucks aren't separate attacks | **attack zones** (onset until the 10 ms energy is 8 dB below its peak, 25–150 ms): no splices inside; catch-ups only right before an attack (fade-length window), never inside a zone |
| Attack stronger than original, amplitude warble | ep_chord up7 / fast125, guitar | the gain normalisation used the low-passed coarse correlation, not the faded audio's | gains from the full-band correlation over exactly the faded span |
| Can't decide which frequency to emphasise | guitar/piano up12, down5, down12 | jump chosen on the low-passed copy; at +12, 36–40 splices/s | the best 3 coarse candidates are re-ranked full band over the fade length; fewer splices (variants tsB/tsC) |
| Snare repeated, grainy, "digitally stretched" | break_wet slow50/80, guitar slow50, vox slow | reverb/noise matches only at c ≈ 0.3; 20–30 splices/s with 10 ms fades | backward jumps land after the attack zone; **fade length grows with poor matches** (10 → 40 ms at c = 0) |
| Missed attack at sample start | all (ep_chord had no attack at all) | the detector needed 32 ms of history | detection from the first frame |

The objective metrics (onset extra, a level/spectral flux metric) did **not** track these
reports (the flux metric even rated rubberband worse on break_wet), so tuning now goes by
listening, with the splice log to find causes. Some grain on noise-like material is inherent
to splicing: every splice of two uncorrelated noise segments changes the texture.

Round 2 page: `app1/proto/stretch/work/listen2.html`: the complaint clips with ts1 (round 1), ts
(the fixes above), tsB/tsC (drift threshold 960/1440 instead of 480: fewer, longer splices;
attacks stay on time through the catch-ups) and rubberband.

### Listening round 2 and decision (2026-10-25)

- tsB/tsC (fewer splices) win nowhere: the warble only changes speed. The threshold stays at 480.
- ts is sometimes better than ts1, never worse. **ts (current `tsplice.py`) is the version to port.**
- Fixed afterwards: retry storms before attacks (a failed catch-up search was repeated every
  sample, 300 searches/s on drums; now that attack just plays with its drift). Search cost is now
  ts1's +7…30 % (15–71 multiply-adds per sample); longer fades add about 15 % interpolation
  work when pitched.
- **Accepted limitations** of single-band splicing:
  - the guitar's ringing chord tail warbles between two dominant partials, pitched up or down
    (no single jump aligns all partials of a chord)
  - vox_loop pitched up sounds grainy, like fast amplitude modulation (many splices of
    changing-pitch material, plus formants moving up)
  - Possible later, at higher cost: splitting into 2–3 bands, each spliced on its own (helps
    chords); pitch-synchronous splicing (PSOLA) for voices (also keeps formants). Not planned now.

## C implementation: patch `vari4-stretch` (2026-10-25)

`app1/payload/tsx.c` (+ `tsx.h`) is the stretcher on the SP. It ports
`tsplice.py` rule for rule, but streams: it sees only Roland's source rings.

**How it is hooked in** (`app1/tools/make_image.py`, patch `vari4-stretch`, experimental):

| Address | Change |
|---|---|
| `0x800D2E2E` / `0x800D2E38` | the `Resampler_Stereo/Mono` thunks (`movw/movt ip; bx ip`, `Voice_RenderSetup`'s only path to the resampler) jump to `tsx_resampler_stereo/mono`; these run mode 3 themselves and send every other mode to Roland's original |
| `0x800E4392` | `and r2, #6` → `#4`: the 60 % ring fill that Ensemble gets now also applies to mode 3 |
| `0x800D37F0` | `Param_GetRange` switch entry for `0xF6` → `0x800D5784` (alignment padding) holding `ldr.w pc, [pc, #0x1B4]`, whose address word (padding at `0x800D593C`) points at `range_vari4` (0..3) |
| `0x800C63A6` | the label of value 3: Roland's unused `TBD2` → `VARI4_LABEL` ("Plus"; 4 characters at most) |
| `0x800F8222`…`0x800F82C3` | the SHIFT + wheel cycle on the pitch/speed panel (`FUN_800F8190`), which ignores `Param_GetRange` and hard-codes VINYL → Off → Backing → Ensemble: its "→ Off" block becomes a generic "VARI = r9" block, and a new dispatcher (assembled by `make_image.py`) goes VINYL → Off → Backing → Ensemble → **Plus** → VINYL, and backwards |

**Firmware contract**, as found:

- `Resampler_Mono/Stereo(stretcher, out…, 0x800, &pos_out)` returns the samples produced;
  fewer means the ring ran dry (at the end of a sample). `*pos_out` = the block tag of the
  current read block, or −1 when it ran dry.
- The ring: Roland's writer fills 0x800-sample chunks before each call until (write −
  read) ≥ 40 % (60 % for modes 2 and 3). It only refuses to overwrite between the *reported*
  read index and itself, and it doesn't run during the call. So **all 10,240 slots hold valid
  data during a call**: history + look-ahead = 10,240. tsx reports a read index just behind its
  lowest tap and computes history from `write − 10240`. Look-ahead is about 6,144 at each
  block start.
- State: our `TsxState` is laid over the voice's 808-byte `StretchState` (OCRAM), leaving
  the fields Roland still touches (`+0x48` splice flag: we set it while fading, so
  `Voice_FadeOutTail` still fades a voice that ends mid-splice; `+0x4C`: `Stretch_Init` sets
  −1 at every voice start, our reset marker; `+0x54`; `+0x2C8`). If the ring's read index
  isn't where we left it, the state is reset as well.
- Scratch (16 KB): the render set's two int32 conversion buffers (`0x20251500` or
  `0x20249500`), which `Voice_RenderSetup` uses only before calling the resampler.
- The payload has no writable globals (it runs from SDRAM since 2026-10-26, see
  [payload-ram.md](payload-ram.md), but nothing zero-initialises them), no libgcc (64-bit → float by hand),
  stack ≤ about 370 bytes.

**Differences from the Python model, on purpose:** onsets are found incrementally as frames
arrive. An attack zone whose end isn't known yet counts as still open. The coarse correlation
windows are centred on the splice points (Python changed to match; it halves the look-ahead a
forward jump needs). Mono for the search is int16.

**Tests:**
- `app1/proto/stretch/tsx_c.py` runs the C on the PC inside an emulation of `Voice_RenderSetup`'s
  refill. On all 17 clips × 9 settings it matches Python: extra onsets within ±0.06 except
  −12 (C 1.52 vs Python 1.88), and timing within ±1 ms. Pass-through (pitch 1, ratio 1) is
  bit-exact.
- `app1/proto/stretch/qemu/` runs the ARM build in QEMU (`mps2-an500`, Cortex-M7) with
  `-icount`, on 3 s of the "mix" clip.

| Instructions per output sample, stereo voice | first C | optimized (sinc) | linear |
|---|---|---|---|
| time only (0.8 / 1.25) | 553 / 519 | 271 / 277 | 260 / 266 |
| pitch −5 | 1,165 | 563 | 287 |
| pitch +7 | 1,872 | 842 | 410 |
| pitch +12 | 2,910 | 1,281 | 562 |

Optimizations:
- the search on int16 copies with `SMLALD` (2 multiply-adds per instruction); every window
  has an aligned copy, so no unaligned loads
- a polyphase kernel table built per call (64 or 32 phases, interpolated); later replaced
  by linear interpolation (below)
- ring reads without modulo (one modulo per tap)
- planning sleeps while no decision can change (it used to run its bookkeeping on 72 % of samples)

Instructions aren't cycles (the M7 dual-issues, but waits for SDRAM and flash), so the real
cost must be measured on the device ([cpu-meter.md](cpu-meter.md)).

**Device test 1 (2026-10-25):** sounds great (a piano with scale pads "like a really good
piano"), but with chromatic + Plus, 4–5 notes used up the CPU: hits got lost and notes ended
early, without stutter. The cause is starved sample loading (SMPL_CHARGE_TASK, priority 14,
below rendering at 6), shown by the new CPU meter. Voices end normally (nothing gets stuck).

**Interpolation → linear (2026-10-25).** When pitched, the windowed sinc (up to 28 taps at
+12) was about 2/3 of the cost. `app1/proto/stretch/interp.py` rendered the same splicing with
cheaper filters: sinc half-width 4, fixed 12/8/6 taps, 4-point cubic, linear. Measured: the
long sinc suppresses aliasing by 45 dB against 0 for linear, and interpolation accuracy
(6 kHz tone) is −88 vs −32 dB. But the user heard **no difference** between them: "linear
sounds fine"; the shimmer that remains is equally there in all of them, so it comes from the
splicing (phase steps at crossfades), not from aliasing. Roland's Backing (also linear)
still sounds bad, which shows the quality is in the splicing. Plus now interpolates linearly:
the kernel table (`tsx_kernel.c`, 6 KB) and its per-call setup are gone, and the cost halved.

What's left (profile at +12, per sample): search ~280, output ~140, crossfade correlation
~60, analysis ~30. The search runs at every splice; pitched voices splice every 500–1,000
samples.

**Open:**
- device test 2: voices at the new cost
- cheaper search (coarser decimation, fewer candidates), a cheaper output loop (fixed-point
  position), and if needed a Plus voice limit or fallback to Backing under load
- polyphony accounting: Pad_Trigger counts an Ensemble voice double; mode 3 counts single

## Changing VARI while a pad plays (2026-10-26)

Device: scrolling VARI (SHIFT + wheel) restarted the sample from the start, but only while
the pad was still sounding; VINYL toggles didn't. Stock behaviour, not Plus:

- `PadStore_Set` (`0x800DA720`), pad param `0x70`: when bits 1–2 change it calls
  `Pad_MarkRestart(pad)` and `Pad_RestartVoices(ctx, pad)` (`0x800D7668`). That collects the
  pad's voices still in a slot, stops them and calls `Pad_Trigger` again, position 0 (except
  sequencer trigger types 20/22/23). With no voice left, nothing happens. Any other change of
  the word goes to `Voice_PushParam(ctx, pad, 0x70, word)`.
- Yet `Voice_SetParam(slot, 0x70, word)` already switches a voice in place: `Stretcher.mode`,
  the period (600/400/800) and the crossfade tables. The restart is presumably for Ensemble,
  whose voices hold a second slot (`Voice_Start`'s `param_7`).
- (`SmplPrm_Set` case `0x70` refuses a VARI change while the pad plays; that's another path,
  not the wheel's.)

**Fix (part of `vari4-stretch`):** `0x800DBDC4` calls `hook_vari_changed(pad, word)`
(`app1/payload/vari.c`) instead of the restart. It calls `Voice_SetParam(slot, 0x70, word)` for each
playing voice of the pad (and its chromatic notes), except where Ensemble is involved: a
voice that plays Ensemble or has a second slot, or a change *to* Ensemble, keeps its current
mode until the next hit. So Off ↔ Backing ↔ Plus switch live; into or out of Ensemble you hear
the change on the next hit. Nothing restarts.

Coming back from Plus, Roland's resampler would find our `TsxState` in its state, so
`tsx_resampler_*` call `vari_leave_tsx` for every non-Plus block: if the marker (`+0x4C`) is
still `TSX_MARKER`, it runs `Stretch_Init` and sets the period as a voice start would. Going
to Plus needs nothing: tsx re-initialises whenever the marker isn't its own.

Not yet tested on the device.

## Key functions

| Address | Name | What it does |
|---|---|---|
| `0x12DC0` | `Resampler_Process` | the splice stretcher (above) |
| `0x1409A` / `0x140C4` | `Resampler_Mono` / `Resampler_Stereo` | wrappers; return samples produced |
| `0x13BEC` | `Ring_Advance` | moves the read index; 0 if it would overtake the writer |
| `0x13C58` | `Stretch_TransientGuard` | blocks splices around loud events (modes 1, 2) |
| `0x13DE0` | `Stretch_CorrelateStep` | Ensemble periodicity search, one lag per call |
| `0x13D9A` | `Stretch_PeriodFromLag` | lag → period ≥ 600 |
| `0x13D82` | `Stretch_FallbackPeriod` | shorter period when little data is ahead |
| `0x14338` | `Stretch_ClampRatio` | limits modulation depth |
| `0x14108` / `0x14238` | `Ring_FillStereo` / `Ring_FillMono` | int16 → int32 << 13 |
| `0x800259E0` | `Stretch_Init` | reset; mode, period 600 |
| `0x80025858` | `Stretch_SetXfadeTables` | equal-power tables (unused path) |
| `0x800E3F78` | `Voice_RenderSetup` | rate, ratio, ring fill, calls the resampler |
| `0x800231F8` | `SmplCharge_Init` | voice start; picks the mode |
| `0x800F7518` | `PitchPanel_Draw` | VINYL / VARI rows |

Structs `Stretcher`, `StretchRing`, `StretchState` are in `app1/tools/types.txt`.

## Open questions

- Which task/priority runs the streaming code (`FUN_800E5C68`), and how much headroom it has.
- How often voices actually render (per 0x800 block, or less when the ring is full).
- The UI for pad params `0x8A…0x8C` (ratio modulation), and the meaning of the 4 curves.
- `StretchState` `+0x7C`/`+0x80` (`0x4000`, `0x5000` at init) and `+0x14`.
- Ensemble's 60 % ring fill: is it there to give the correlation room, or for longer periods?
