# Sample looping (LOOP / ping pong)

## Summary

**Parked (2026-10-26).** Two reasons. First, the crossfade needs real audio memory, which only
comes from shrinking the skip-back/looper buffer. Second, buffering audio for playback is
general enough to be built once, as an interface other features can use ("If we revisit"
below). The design, the measurements and the code paths found are recorded here.

First look, 2026-10-26, prompted by the user: drones are hard because loops click, even in
ping pong. A loop in Roland's code is a **hard jump**. When the read reaches the end of the
loop, `Voice_ReadSource` (`0x800E7890`) restarts it at the loop start (or turns round for
ping pong) inside the same block, with no crossfade. There is a 32-sample fade
(`Fade_Out32`/`Fade_InLinear`), but only on the path where flag `+0x59` of the sample record
is set, not on a normal loop. So any difference in level or slope between the end and the
start is heard once per loop.

The data side constrains any blend: a pad's sample is in RAM only from its start for a
limited "head" (record `+0x50` pointer, `+0x54` bytes). Past the head, each voice streams into
five buffers of its own (0xB800 bytes = 11,776 stereo frames ≈ 245 ms each), filled by
SMPL_CHARGE_TASK. Roland's jump reads the loop start right away, so the loop start is resident
whenever the jump happens. Audio from *before* the loop point or *after* the end may not be.

## Key functions

| Address | Name | What it does |
|---|---|---|
| `0x800E7890` | `Voice_ReadSource` | the next source samples for a voice: head / segments, forward / reverse, loop and ping-pong jumps, end |
| `0x800D8998` | `SampleInfo_ForPad` | (pad) → the pad's record in `g_sample_info` |
| `0x1A41A` | `SrcRead_Fwd16` | (ITCM) voice vtable `+8` (one of four variants): copies n big-endian 16-bit frames from a RAM address, with gain |
| `0x16F50` / `0x17196` | `Env_ApplyFwd` / `Env_ApplyRev` | (ITCM) the pad's volume envelope (gain 0…10000 by source position), after every read |
| `0x173CA` | `Fade_InLinear` | (ITCM) linear fade-in over `len` samples (32, or 0x140 = 320 after a segment change) |
| `0x8001D660` | `Fade_Out32` | the 32 samples *before* the pointer, multiplied by `g_fade32` |
| `0x17B04` | `Fade_Tail32` | (ITCM) the same table, continuing a fade counter (voice `+0x58`) |

## Details

**Voice object** (SmplCharge, 0x28820 bytes from `0x805F8834`; offsets in words as Ghidra
shows `param_1[...]`):

| Word | What |
|---|---|
| `[0]` | vtable: `+0` bytes→frames, `+4` frames→bytes, `+8` read forward, `+0xC` read backward, `+0x10` silence. Four variants, picked in `Voice_InitFromSample` (ITCM ones at `0x80227484`/`0x80229D90`, SDRAM ones at `0x80228DD0`/`0x8022A940`) |
| `[9]` | > 0: loop on |
| `[0x34]` | 1: loop from the LOOP point (C2), else from START |
| `[0x37]` | play mode: bit 0 reverse, bit 1 ping pong (a REVERSE press mid-play swaps 0↔1, 2↔3) |
| byte `+0x14260` (`[0x5098]`) | source state: 0 head, 1–5 a streamed segment, 7 ended, 0xFF before the first read |
| byte `+0x14261` | the next segment |
| `[0x5099]` | → the pad's sample record |
| `[0x509D]` | pad |
| `[0x50A0 + k·0xE]` | segment k: buffer (`0x826CC7A0 + voice·0x39800 + k·0xB800`), fill state etc. |
| `[0x50ED]` | byte offset inside the current region |
| `[0x50F0..F1]` | 64-bit count of frames left before the region's end |

**Sample record** (`g_sample_info`, 161 × 0x68 bytes at `0x80B08CB8`; found by `+4` = pad):
`+0` used, `+4` pad, `+8` START, `+0x10` end of the RAM head (START + `+0x54`; measured), `+0x18` (?), `+0x20` LOOP (used when `[0x34]` = 1),
`+0x50` head data, `+0x54` head length in bytes, `+0x58` ping pong going backwards, `+0x59` the
"fade at the seam" flag (meaning not yet known), `+0x60` (trigger type 6).

**Loop in the forward path:** read; if the region's frame count is used up, then: next
segment if one is queued; else, with loop on, set position = START, or LOOP − START with
`[0x34]` = 1, and read the rest of the block from there, all in the same call. Ping pong
(`[0x37]` > 1, `+0x58` = 0) sets `+0x58` = 1 and continues with the backward read from the end.

**Where a seam can come from**, besides the jump itself:
- level/phase mismatch between end and loop start (all loops);
- ping pong: the waveform is continuous at the turn, but its slope flips (a corner). It's audible
  when the material has high frequencies at that point; the end frame may also be read twice;
- the envelope is computed from the source position (`Env_Apply*`), so a pad with an
  envelope could step at the jump (to be checked);
- with VARI not VINYL and a tempo change, the stretcher (Roland's or Plus) splices across the
  seam. Plus could even take the click as an attack. Test with VINYL on to separate the causes.

## Options for a smoother loop

1. **Crossfade inside the loop** (as in most samplers): for the last X frames before the end,
   a second reader plays from the loop start and is faded in while the main one fades out.
   At the end, the main reader continues at loop start + X. It only needs audio inside the
   loop, which is resident when it's needed. The loop gets X shorter. Equal-power curve for
   drones (uncorrelated material), with X around 50–500 ms, maybe tied to the loop length.
   Cost: one extra read and mix during the fade.
2. **Ping pong with a mirrored fade**: around the turn, mix the forward and the backward read
   (they meet at the end). The sum is symmetric, so there's no corner. The same at the start.
3. **Short declick** (5–30 ms fade out, fade in): cheap, Roland has the tables, but the level
   dips once per loop, which pumps on a drone. Only good as a fallback.
4. **Match the seam**: move the jump target near the loop start to where the waveform best
   matches the end (the correlation search Plus already has), then a short crossfade. Best
   for short tonal loops; may change the loop length by a few ms.

Selection: per pad, one free bit of a pad parameter word (as chrom-voices does with bits 6–8
of `0x70`), toggled by a key combo with a pop-up like Roland's "GATE ON". The user suggests
SHIFT + REVERSE **on the START/END page** (C1:START C2:LOOP C3:END, ENC:ZOOM), where the loop
is edited and the combo is free (user, 2026-10-26). On the top screen it means something else
(Tallfree: the pad mute mode). Next step: find that page's key handler (page id, vtable).

## Chosen design (2026-10-26)

"Smooth loop", per pad: two read positions inside the one voice, half a loop apart, each
running through the whole loop with a sine/cosine gain (zero at the loop ends and, in ping pong,
at the turns). There are always exactly two, mixed before the stretcher, so the CPU cost is one extra copy and
multiply-add per frame, not a second voice. The first pass plays from START at full gain, and
the second read starts when the first reaches the middle of the loop. The crossfade aims for 50 % of the loop but is capped
at X seconds (user's decision), so the memory a long loop needs has a known limit. X follows
from the measurements.

**Measurement first:** the sample probe (`app1/payload/sprobe.c`, views 2–4 of the tuner page:
BANK A + B, then VALUE press 2, 3, 4 times). View 2: the current pad (bank·16 + pad), START /
LOOP / END from the pad record (`+0xD4/+0xFC/+0xD8`) next to the sample record's (`+8/+0x20/
+0x10`), the head length (`+0x54`, in bytes and in seconds as 16-bit stereo), and for a
playing voice of the pad its source state (`st`: 0 head, 1–5 segment; `nx`; `m` play mode)
plus offset and frames left. Views 3, 4: the raw record in hex. To find out: the head length
for a short and a long sample; whether `st` leaves 0 (streaming) while a loop plays; and
which record words change when C1/C2/C3 move.

**Device results (2026-10-26):**
- Positions are byte offsets in the sample file: audio from 512 (after a 512-byte header),
  16-bit stereo at 48 kHz (192,000 bytes/s).
- The RAM head is at most **81,920 bytes (0.42 s) from START**, less for shorter samples. Record
  `+0x10` is START + head, i.e. the end of the RAM part, not END (examples: 512 + 81920 = 82432;
  548864 + 81920 = 630784; a short sample 512 + 76420 = 76932 = its END).
- A loop point far past the head (LOOP 952064 with START 548864) still loops without a gap,
  so the loop start must be streamed into the voice's segments ahead of the jump. The voice
  sees one continuous stream (…, end, loop start, …).
- Consequence for the design: there's no random access to the loop. The crossfade keeps
  the last X frames before the end (or the turn, in ping pong) in a buffer of its own and mixes
  them with the stream after the jump. That buffer is X × 192 KB/s per voice, which is what the
  cap limits. Reading X ahead once per loop must not outrun the streaming (about 1.2 s queued).
- The probe's voice line stayed "-": pads don't play on our pages (XPage's pad-down slot
  `0xFC`, `0x800736C0`, is empty, and the top screen takes pads in its key-down `0x18`, which
  our page doesn't forward). Start a looping pad on the top screen, then open the probe.

## If we revisit

1. **Memory first, as a general service.** Shrink the skip-back/looper buffer
   (`0x8383A424`–`0x83F7A424`, 7.25 MB, the end set in 7 places; [patching.md](../patching.md),
   "Memory for new features"), as Tallfree does: it cut it to 2.5 MB (13.65 s per channel). Put
   a small **custom audio storage** interface on the freed space: allocate and free named
   buffers, write and read frames, a size known at build time. Users: this crossfade buffer, a
   multitrack looper (goal #2), resampled-note caches for chromatic mode, anything else that
   records and plays back.
2. **Then the smooth loop:** the per-voice crossfade buffer from that storage, X = min(50 % of
   the loop, cap), with the cap set by the storage budget and by how far streaming can be read
   ahead (test it). Hook: around `Voice_ReadSource`, where the loop jump and the ping-pong turn
   happen. Toggle: SHIFT + REVERSE on the START/END page, stored in a free bit of a pad word.
3. Leftover measurement: the probe's live voice line (start a looping pad on the top screen,
   then open the probe): `st` while looping, and the raw record of an edited sample (where the
   real END lives).

The sample probe stays in the tuner's VALUE cycle as a diagnostic.

## Open questions

- How large is the head (`+0x54`)? Does a loop point past it work, and how (the
  `+0x14261` < 0 path)?
- What sets `+0x59` (the existing 32-sample fade)?
- Which of the four read variants is which (mono/stereo, head/segment)?
- Where exactly the ping-pong turn reads the end frame twice.
