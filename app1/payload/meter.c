/* CPU meter: the top pixel row of the screen shows the CPU load (averaged over about 0.4 s,
 * plus a peak-hold pixel), the bottom row one dot per playing voice (3 pixels for a VARI
 * Plus voice, 1 for any other). Patch "cpu-meter"; details in docs/app1/topics/cpu-meter.md.
 *
 * Load = 1 - idle time / elapsed time. hook_idle (hooks.S) replaces the kernel's idle
 * function (ITCM 0xE2D0) and adds each WFI sleep to IDLE_ACC. Time comes from SysTick (one
 * period = 1 ms of g_sys_time_ms), not from the cycle counter, which Roland uses as a
 * timestamp and which may stop while the core sleeps. hook_panel_event runs on the panel
 * (UI) task's tick and redraws at most every 100 ms.
 *
 * The payload has no writable globals, so the meter's words live in METER_RAM (ram.h).
 * See METER_RAM in hooks.S too.
 */
#include <stdint.h>

#include "imports.h"
#include "ram.h"

#define REG(a) (*(volatile uint32_t *)(a))

#define SYST_LOAD  REG(0xE000E014u)
#define SYST_VAL   REG(0xE000E018u)
#define ICSR       REG(0xE000ED04u)
#define ICSR_PENDSTSET (1u << 26)
#define SYS_TIME_MS REG(IMP(g_sys_time_ms))     /* low word */

#define IDLE_ACC   REG(METER_RAM + 0)           /* idle SysTick counts, added by hook_idle */
#define LAST_TS    REG(METER_RAM + 4)           /* time of the last redraw */
#define LAST_IDLE  REG(METER_RAM + 8)           /* IDLE_ACC at the last redraw */
#define MAGIC_REG  REG(METER_RAM + 12)          /* MAGIC once set up */
#define SMOOTH     REG(METER_RAM + 16)          /* averaged load, per mille x 16 */
#define PEAK       REG(METER_RAM + 20)          /* peak marker, per mille */
#define PEAK_HOLD  REG(METER_RAM + 24)          /* redraws left before the peak starts to sink */
#define MAGIC      0x4D455452u                  /* "METR" */

#define PANEL_ON_EVENT ((void (*)(void *, int))IMP(PanelTask_OnEvent))    /* panel task vtable slot 0x20 */
#define DRAW_CTX   REG(IMP(g_icon_draw_ctx))    /* word 0 = the screen's surface */
#define VOICE_SLOTS IMP(g_voice_slots)          /* 32 x 0x58 bytes */
#define VARI_PLUS  3

#define INK_ON     0xFFFFFFu                    /* as Roland draws pictures (respects inversion) */
#define INK_OFF    0x000000u

/* SysTick counts since boot (wraps; only differences are used). Called from the idle hook
   in handler mode and from the panel task. */
uint32_t meter_now(void)
{
    uint32_t period = SYST_LOAD + 1;
    uint32_t ms = SYS_TIME_MS, val = SYST_VAL;
    if (SYS_TIME_MS != ms) {                    /* the tick came in between */
        ms = SYS_TIME_MS;
        val = SYST_VAL;
    } else if ((ICSR & ICSR_PENDSTSET) && SYST_VAL > val) {
        ms++;                                   /* wrapped, tick not yet counted (masked) */
        val = SYST_VAL;
    }
    return ms * period + (period - 1 - val);
}

typedef void (*set_ink_fn)(void *, uint32_t);
typedef uint32_t (*get_ink_fn)(void *);
typedef void (*fill_fn)(void *, int32_t *);

/* One row: x 0..split-1 in INK_ON, the rest in INK_OFF, through the surface's own methods
   (slot 0x0C SetInk, 0x10 GetInk, 0xC0 FillRect), which also mark the row for the LCD. */
static void draw_row(void *surf, int y, int width, const uint8_t *on)
{
    void **vt = *(void ***)surf;
    set_ink_fn set_ink = (set_ink_fn)vt[0x0C / 4];
    fill_fn fill = (fill_fn)vt[0xC0 / 4];
    uint32_t old = ((get_ink_fn)vt[0x10 / 4])(surf);
    int x = 0;
    while (x < width) {
        int x1 = x;
        while (x1 + 1 < width && on[x1 + 1] == on[x])
            x1++;
        int32_t rect[4] = {x, y, x1, y};
        set_ink(surf, on[x] ? INK_ON : INK_OFF);
        fill(surf, rect);
        x = x1 + 1;
    }
    set_ink(surf, old);
}

#define MAX_W 256

#define PEAK_HOLD_TICKS 15                      /* 1.5 s at one redraw per 100 ms */
#define PEAK_SINK  20                           /* per mille per redraw after the hold */

/* load and peak in per mille; ok = 0 draws the dotted "no RAM" row */
static void draw(int load_permille, int peak_permille, int ok)
{
    if (!DRAW_CTX)
        return;
    void *surf = *(void **)DRAW_CTX;
    if (!surf)
        return;
    const uint8_t *bm = *(const uint8_t **)((uint8_t *)surf + 4);
    int w = *(const int16_t *)(bm + 8), h = *(const int16_t *)(bm + 10);
    if (w < 16 || w > MAX_W || h < 8 || h > 1024)
        return;

    uint8_t on[MAX_W];
    int n = load_permille * w / 1000, p = peak_permille * w / 1000;
    for (int x = 0; x < w; x++)
        on[x] = ok ? x < n : (x & 1);           /* dotted: the meter's RAM didn't keep values */
    if (ok && p > n && p < w)
        on[p] = 1;
    draw_row(surf, 0, w, on);

    for (int x = 0; x < w; x++)
        on[x] = 0;
    int x = 0;
    for (int i = 0; i < 32 && x + 3 < w; i++) {
        uint32_t slot = VOICE_SLOTS + i * 0x58u;
        uint32_t state = *(volatile uint32_t *)REG(slot + 4);
        if (state == 0)                         /* free (VoiceSlot_IsBusy, really IsFree) */
            continue;
        uint32_t voice = REG(slot);
        int plus = voice && REG(voice + 0x140 + 0x1C) == VARI_PLUS;
        on[x] = 1;
        if (plus)
            on[x + 1] = on[x + 2] = 1;
        x += 4;
    }
    draw_row(surf, h - 1, w, on);
}

static void meter_update(void)
{
    uint32_t now = meter_now(), idle = IDLE_ACC;
    if (MAGIC_REG != MAGIC) {
        LAST_TS = now;
        LAST_IDLE = idle;
        SMOOTH = PEAK = PEAK_HOLD = 0;
        MAGIC_REG = MAGIC;
        if (MAGIC_REG != MAGIC)
            draw(0, 0, 0);
        return;
    }
    uint32_t elapsed = now - LAST_TS;
    if (elapsed < 100u * (SYST_LOAD + 1))
        return;
    uint32_t idled = idle - LAST_IDLE;
    LAST_TS = now;
    LAST_IDLE = idle;
    uint32_t per_mille = elapsed / 1000;
    int load = 1000 - (int)(idled / (per_mille ? per_mille : 1));
    load = load < 0 ? 0 : load > 1000 ? 1000 : load;

    /* bar: average with a time constant of about 0.4 s (a quarter of the way per redraw);
       marker: the highest single 100 ms value, held 1.5 s, then sinking */
    int smooth = (int)SMOOTH;
    smooth += (load * 16 - smooth) / 4;
    SMOOTH = (uint32_t)smooth;
    int peak = (int)PEAK;
    if (load >= peak) {
        peak = load;
        PEAK_HOLD = PEAK_HOLD_TICKS;
    } else if (PEAK_HOLD > 0) {
        PEAK_HOLD = PEAK_HOLD - 1;
    } else {
        peak = peak - PEAK_SINK > smooth / 16 ? peak - PEAK_SINK : smooth / 16;
    }
    PEAK = (uint32_t)peak;
    draw(smooth / 16, peak, 1);
}

/* Panel task vtable slot 0x20 (word 0x8021D018): Roland's handler, then on event 0 (its
   regular tick) the meter. */
void hook_panel_event(void *task, int event)
{
    PANEL_ON_EVENT(task, event);
    if (event == 0)
        meter_update();
}
