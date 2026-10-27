# Looper mode (goal #2)

## Summary

**Paused (2026-10-26)** after finding Roland's own LOOPER (see Findings). The v0/v1/v2 plan
below assumed no looper existed; the design is open again. Where it stands:

- If we write a looper, it **replaces Roland's** (same RAM, same SKIP BACK entry), not a second
  one alongside it.
- Roland's looper and ours aim at different things. Roland's: the pads are an instrument you
  play *into* one overdub loop. Ours: the pads *are* the tracks (a multitrack looper). The wish
  is both, like a small Boss RC-505: play pads into the looper while it has several tracks.
- Ideas considered, not decided: banks A–E for playing, F–J as looper tracks (one bank = 16
  tracks would already be plenty). Downside of any split: recording e.g. beats from the pads
  must stay responsive. Whether undo and overdub are in changes the memory and the engine.
- **Prerequisite for everything: audio routing.** We must record from a different signal than
  the one the loop playback goes into, or each take records the earlier tracks again. Roland
  had the same problem; its looper probably works around it with its own channel in
  `g_mix_bus`. To find out:
  1. With the tuner's receive probe: play a pad, play Roland's looper, play the input. Which of
     the 8 receive words carry which? Is any word input-only or pads-only?
  2. Which `g_mix_bus` channel `Looper_MixBlock` writes, and whether that channel is left out of
     the word the looper records from.

## First plan (2026-10-26, before Roland's looper was found)

- **Enter / leave:** SHIFT + REC (unbound today). A custom page ([custom-pages.md](custom-pages.md),
  like the tuner) shows the state. EXIT leaves.
- **The master length L is a frame count** (48 kHz frames), never a BPM. Track lengths are
  exactly k × L with k ∈ {¼, ½, 1, 2, 4, 8}. The BPM is derived from L for display only.
  Deriving lengths from the BPM would round and drift.
- **Bars in the first loop:** pick the bar count (4/4) that puts the BPM in 80–160; the VALUE
  wheel halves/doubles it. For an existing pad, use its stored BPM first if it has one.
- **Snapping:** a take's length is rounded to the nearest allowed k × L. Too long: trimmed.
  Too short: silence at the end.
- **Phase locking:** a master position `pos` runs 0..L (counted in audio frames). Each track
  has length Lᵢ and phase φᵢ (where in the bar its take began). Its play position is always
  `(pos − φᵢ) mod Lᵢ`. Unmuting resumes there, never at the sample start.
- **Pad presses:** empty pad: record, press again: stop and loop. Playing pad: mute; muted
  pad: unmute. No overdub on an occupied pad.
- **Undo:** SHIFT + PATTERN SELECT (printed on the chassis) removes the last take.
- **SHIFT + pad:** a per-track menu later (fine phase shift, clear, …).
- **Screen:** playhead across the loop, bar/beat counter, BPM, one mark per pad state
  (empty / recording / playing / muted).

## Why the clock must be the audio, not SysTick

The CPU meter's time base (`meter_now`) is the 1 ms SysTick, driven by the CPU's crystal. The
audio runs on the codec's clock. Two crystals always differ a little (a few tens of ppm, so
milliseconds per minute): a looper that counts SysTick time drifts against the audio it plays.

So the looper's clock counts **audio frames**: `Audio_RxHandler` runs once per 64-frame
half-buffer (1.33 ms), and we already hook it (`hook_audio_rx`, tuner). Adding 64 to a counter
there gives a clock that can't drift from the audio.

**Jitter vs drift:** the UI reacts to pads with a few ms of jitter. That never adds up, because
every position is computed from the frame counter, not by summing delays. Once voices can start
partway into a sample, even the jitter goes away: a voice started 3 ms late starts 3 ms into
the sample (`(pos − φᵢ) mod Lᵢ`), so it's exactly in phase.

## v0: one track, end to end

Goal: the basic primitives on the device. SHIFT+REC → looper page; press an empty pad →
recording; press again → loops at exactly the recorded length; press again → mute/unmute
(restart from the start is OK in v0). Screen: playhead + length in seconds + derived BPM.

Research (each answer goes into this file):
1. **Key codes** of REC and PATTERN SELECT (a key probe on the custom page, like the tuner's
   receive probe), and what SHIFT+REC does today (should be nothing).
2. **Sampling path:** from "REC pressed" to "pad playable". Start at `SMPL_REC_TASK`
   (`SmplTasks_Create`), the sampler fed by `Audio_RxHandler`, and `g_rec_ctrl`. Which call
   starts and stops a recording; where the audio goes (RAM, then SD?); **how long until the pad
   plays** after stop. If it's long (SD write), v0 keeps the take in RAM and writes it to the
   pad later (on exit or in the background).
3. **Recording source:** the recorder takes one receive word (`g_rx_selected`; word 0 is the
   full mix). Does normal input sampling use a word *without* the pads? If the take includes
   the loops already playing, every new track doubles the old ones. Measure with the receive
   probe: play a pad, check which words carry it.
4. **Pad loop playback:** how the pad LOOP / GATE settings reach a voice, and the call that
   triggers a pad (to start/stop it from our code). Candidate: `g_voice_slots`, voice objects,
   `Voice_RenderSetup`.
5. **Loop seam:** does the voice loop at exactly the sample end (no gap, no rounding)? Measure
   by recording a click loop and listening / resampling a few minutes of it.
6. Look at `LOOPER_TASK` (12) and `LOOPER_BACKGROUND_TASK` (20) and the string `Looper Sample`:
   if Roland's looper effect already records into a RAM ring and plays it back, it may be the
   better recording path.

Build: payload `looper.c` (state machine + page), key combo in `hook_top_key_down`, frame
counter in `hook_audio_rx`. Patch `looper` (experimental) in `app1/tools/make_image.py`.

Done when: a guitar loop recorded on the device plays for 5 minutes without audible drift or
a click at the seam, and the CPU meter shows no extra load.

## v1: several tracks in sync

Goal: L from the first take (recorded or an existing pad); further pads join in time.

- First take sets L; bar count from the BPM range, VALUE wheel ×2 / ÷2.
- Existing pad pressed: snap its length to k × L (trim / pad with silence), start it on the
  next boundary of its length (next bar for k ≤ 1, next multiple of its length otherwise).
- New take: recording starts at the press, its length is snapped to the nearest k × L when
  stopped. In v1, playback starts on the next boundary (phase φ = 0); the offset between the
  press and the grid is ignored.
- Mute = volume 0 with a short fade; the voice keeps running, so unmuting keeps the phase
  for free (costs voices: measure with the CPU meter; 32 voice slots).
- Undo: SHIFT + PATTERN SELECT removes the last take.
- Screen: bar/beat counter, one mark per pad.
- Optional: set the project BPM to the loop BPM so BPM SYNC pads (and VARI Plus) follow.

Research: triggering a pad at a given frame from the audio side (from `hook_audio_rx`, or
through the pattern sequencer's clock, `SeqClock_Tick`), per-voice volume for muting, trimming
/ extending a sample's end point in RAM.

## v2: phase-locked tracks

Goal: takes keep the offset of the press (φᵢ ≠ 0); voices start partway into a sample.

- **Start a voice at an offset.** Find where the voice holds its read position (voice object,
  `Voice_RenderSetup`, start point / sample position fields) and set it right after the voice
  starts, or start it from a moved start point. This is also the base for granular synthesis
  later, so it gets its own notes (`voice-engine.md`).
- New takes keep φᵢ = (press frame mod Lᵢ). Unmuting and late triggers start at
  `(pos − φᵢ) mod Lᵢ`, so muted tracks no longer need to keep running (saves voices/CPU).
- SHIFT + pad menu: fine phase shift (φᵢ ± a few ms), clear the track.
- Candidates, not decided: retroactive takes from an input ring buffer (catches a late
  downbeat), count-in, threshold start, time signature setting, overdub.

## Findings (2026-10-26)

### Key codes

From `TopScreen_HandleKey` (`0x800C9788`) and Tallfree's simulator scenarios:

| Code | Button | Top screen |
|---|---|---|
| `0x14` | PATTERN SELECT | pattern page (Tallfree: key 0x14 opens the pattern screen) |
| `0x19` | SKIP BACK (probably) | page 6 (skip-back), or page 39 (looper) while Roland's looper is on |
| `0x1A` | DEL | |
| `0x1B` | REC | page 13 (sampling standby, "Select PAD for RECORDING") |
| `0x23` | COPY | |
| `0x24` | REMAIN | |
| `0x31` | VALUE press | with SHIFT: dialog "Enable Looper? (SBS will reset)" / "Enable Skipback? (Looper will reset)" |

**SHIFT + REC** on the top screen does the same as REC (opens page 13). SHIFT only matters there
while a pattern is armed for recording (`PatternStore_Get(0x7C) == 1`). Our hook can take
SHIFT+REC when no pattern is armed.

### Roland already has a LOOPER (v4.04)

The changelog for v4.04 says "Added LOOPER for overlapping samples". The firmware confirms it:
- **On/off:** SHIFT + VALUE press on the top screen asks "Enable Looper? (SBS will reset)".
  The looper and skip-back share the same RAM, so only one of them runs at a time. The flag is
  byte `+0x39` of `g_looper` (`0x82E0B71C`), read by `Looper_IsOn` (`0x800D5E70`).
- **Page:** while it's on, SKIP BACK opens page 39 (`LooperPage_Handle`, `0x800602A0`). Its key
  handler `LooperPage_OnKey` (`0x800819C0`): REC = record/play, key `0x1C` = overdub
  ("Overdub not Available", "Please reset RATE"), SHIFT+PATTERN SELECT = Undo/Redo, DEL / SHIFT+DEL,
  COPY = save (probably to a pad; "Looper Sample"), key `0x18` = "BPM from PAD" (SHIFT: "BPM from
  BANK"). Pads play normally on that page.
- **Commands** go through `Looper_PostCommand` (`0x800C23C8`) into a 128-entry queue
  (`0x80B5DFA4`) for `LOOPER_TASK` (priority 12). The audio code posts its own (0x64, 0x65, 0x69)
  when recording starts on threshold or the buffer is full.
- **Recording** (`Looper_RecordBlock`, ITCM `0x1D634`) runs instead of the normal recorder in
  `Audio_RxHandler` while the looper is on, on the same receive word (`g_rx_selected`). State byte
  `g_looper_state` (`g_looper + 0x101`): 1 armed (a 500-frame pre-roll ring waits for the
  threshold), 2 recording, 4 full, 5/6 playing, 7 overdub armed. L and R go into separate planes.
- **Playback** (`Looper_MixBlock`, ITCM `0x1E248`) is called from `Audio_Mixer` right after the
  32 voice slots are mixed into the bus buffer `g_mix_bus` (`0x202AD3A0`, 64 frames, 14 channels),
  and adds the loop into it. **The looper isn't a voice.** It's a separate stream mixed in at
  the same point.

What this means for our design:
- Takes don't have to become pads to loop. A mixer stream like Roland's plays RAM directly, with
  full control of the read position (phase locking, partway starts) and no SD write in the way.
- `Audio_Mixer` is a good hook point for our own tracks, and `Audio_RxHandler` (hooked already)
  for recording them. Both run every 64 frames, which is also the frame clock.
- Roland's save path (COPY) shows how to turn RAM audio into a pad sample.

## Open questions

- Time from "recording stopped" to "pad playable" (normal sampling).
- Which receive word is the input without the pads, and whether Roland's looper playback is in
  the word it records (overdub must avoid recording itself twice).
- Roland's looper on the device: length set by BPM/bars or free? Does it sync to the BPM?
- The bus layout of `g_mix_bus` (which of the 14 channels is what).
