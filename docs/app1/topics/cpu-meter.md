# CPU meter (patch `cpu-meter`)

Why: with VARI Plus on several chromatic voices, hits got lost and notes stopped early, without
stutter (device test 2026-10-25). The task priorities explain how CPU load does that: voice
rendering runs at priority 6 (SMPL_TASK, AUDIO_TASK), sample loading at 14
(SMPL_CHARGE_TASK). When rendering takes the CPU, loading starves; a voice whose source read
fails is freed (`Voice_RenderSetup` returns −1 → `VoiceSlot_Render`), and a new voice can't
start. The meter is there to see this happen and to measure optimizations.

## What it shows

- **Top pixel row:** CPU load, 0 to 100 % across the screen width, refreshed every 100 ms.
  The bar is averaged (each redraw moves it a quarter of the way to the new 100 ms value, a
  time constant of about 0.4 s); one lit pixel beyond it marks the highest 100 ms value, held
  for 1.5 s, then sinking 2 % per redraw. The raw 100 ms values flickered too much to read.
  A dotted row means the meter's RAM didn't keep values (meter not working).
- **Bottom pixel row:** one mark per busy voice slot, 4 pixels apart: 3 lit pixels for a
  VARI Plus voice, 1 for any other. Ensemble takes two slots.

## How it works

| Piece | Where | What |
|---|---|---|
| idle time | `hook_idle` (`app1/payload/hooks.S`); ITCM `0xE2D0` (`Kernel_IdleWait`) → `ldr.w pc, [pc]` + address | same as Roland's idle (BASEPRI 0xF0, `dsb`, `wfi`, BASEPRI 0x60), timestamps before and after, adds to `IDLE_ACC` |
| time base | `meter_now` (`app1/payload/meter.c`) | `g_sys_time_ms` × (SysTick LOAD + 1) + elapsed part of the current period; handles a tick that's pending while masked |
| drawing | `hook_panel_event` ← panel task vtable word `0x8021D018` | Roland's `PanelTask_OnEvent`, then on event 0 (its regular tick) at most every 100 ms: load = 1 − idle/elapsed, two rows through the screen surface's SetInk/FillRect |

Facts it relies on:
- T-Kernel's dispatcher (`PendSV_Handler`, ITCM `0xDD3C`) loops while no task is ready and calls
  `Kernel_IdleWait` each time (`g_idle_spin` at `0x80245794` is 0 and never written).
- SysTick is the 1 ms kernel tick (`SysTick_Handler` → `Kernel_TimerTick`, 64-bit
  `g_sys_time_ms` at `0x202BCF20`); `Delay_us` scales its LOAD as one millisecond.
- **The cycle counter is not used for time:** Roland reads it as a timestamp
  (`SeqClock_Tick`, never write it), and it probably stops during WFI (which would explain
  Tallfree's "out by ninefold").
- The payload can't have writable globals; the meter's words live in `METER_RAM` (`ram.h`).
  Until 2026-10-28 that was `0x83FFFFC0`, in the free tail of MPU region 9 (normal
  **non-cacheable**, `0x83FF0000..0x84000000`, set up at flash `0x600804EA`; the linker fills
  it up to `0x83FFBF58` in v5.52, more in each release). Now it is in the payload's own range,
  cacheable: the idle hook (handler mode) and the panel task run on the same core, so they see
  the same cached values.
- First attempt kept them in the DWT comparator registers (`DWT_COMP0..3`): on the device they
  didn't keep values (dotted row), so the debug block isn't usable for storage.
- The screen surface is `**0x80591B48` (`g_icon_draw_ctx`); its bitmap (`Bitmap1`, surface + 4)
  gives width and height. Ink 0xFFFFFF/0 as Roland uses for pictures (follows display inversion).
- Voices: `g_voice_slots` (32 × 0x58 at `0x202AE1A0`), state word `*slot[1]` (0 free, 1
  playing), voice object `slot[0]`, VARI mode at voice + 0x140 + 0x1C.

## Task priorities (`Task_ctor` calls)

| Task | Priority |
|---|---|
| MIDI_SYNC_TASK | 2 |
| SMPL_END_FROM_INTERRUPT_TASK | 3 |
| SMPL_TASK, AUDIO_TASK, SMPL_PLAY_START, SMPL_FROM_INTERRUPT_TASK | 6 |
| SMPL_REC_TASK | 10 |
| LOOPER_TASK | 12 |
| SMPL_CHARGE_TASK | 14 |
| SMPL_CHARGE_POLLING_TASK | 15 |
| FILEIF_TASK, LOOPER_BACKGROUND_TASK | 20 |
| PANEL_TASK, CONSOLE_TASK | 29 |
| PARAM_TASK | 31 |
| Sys_task | 32 |

Raw `tk_cre_tsk` tasks (4-character names in the record): USBCdcTs, LowBGK, PlayTask, PrefTask,
UpdateMB, IPC, SDSC…, MIDI RX, Factory.

Tested on the device 2026-10-25: works; ink 0xFFFFFF is lit. It showed the CPU running out
with several chromatic Plus voices, while voices still ended normally (no stuck voices).

## Open

- Does event 0 tick regularly on every screen, and is `**0x80591B48` the visible screen on
  every page?
