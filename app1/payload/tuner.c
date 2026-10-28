/* Guitar tuner (goal #5): BANK A + BANK B on the top screen opens it (patch "tuner",
 * docs/app1/topics/tuner.md). The page (page.c) shows the pitch of the SP's mix (pitch.c), so
 * nothing else should play while tuning. With patch "tuner-probes" (diagnostic, not on the
 * website) VALUE press cycles through the diagnostic views: the input probe, then the sample
 * probe (sprobe.c).
 *
 * The combo works like Roland's BANK D + BANK E for DJ mode (TopScreen_HandleKey, cases
 * 0x28/0x29): the first bank key has already switched the bank by the time the second
 * arrives, so we remember the bank on each BANK A/B press and put it back, with the same
 * bank-key state DJ mode leaves (0x8059259A/9B, the F-J flags at 0x805925B8).
 */
#include <stdint.h>

#include "imports.h"
#include "page.h"
#include "pitch.h"
#include "ram.h"

void sprobe_draw(cpage_ctx *ctx, int view);    /* sprobe.c */

/* Set to 1 in the image by patch "tuner-probes" (make_image.py) */
const volatile uint8_t tuner_probes = 0;

#define REG(a) (*(volatile uint32_t *)(a))

#define TopScreen_KeyDown ((int (*)(void *, int))IMP(TopScreen_KeyDown))  /* top screen vtable slot 0x18 */
#define UI_GetCurrentPage ((int (*)(void))IMP(UI_GetCurrentPage))
#define Key_IsHeld ((int (*)(int))IMP(Key_IsHeld))
#define PadStore_Get ((int (*)(void *, int, int))IMP(PadStore_Get))
#define PadStore_Set ((void (*)(void *, int, int, int))IMP(PadStore_Set))
#define PAD_STORE ((void *)IMP(g_pad_store))    /* value 0 = current bank 0..9 */
#define BANK_KEY_LAST (*(volatile uint8_t *)IMP(g_bank_key_last))
#define BANK_KEY_INDEX (*(volatile uint8_t *)IMP(g_bank_key_index))
#define BANK_F_TO_J ((volatile uint8_t *)IMP(g_bank_f_to_j))  /* per bank key A..E: showing F..J */
#define PAGE_TOP 1
#define PickStore_Get ((int (*)(void *, int, int))IMP(PickStore_Get))
#define ExtSource_Toggle ((void (*)(void *, int))IMP(ExtSource_Toggle))  /* (unused, key down) */
#define PICK_STORE ((void *)IMP(g_pick_store))
#define PICK_EXT_SOURCE 0x25                    /* EXT SOURCE on (store +0x5C) */

#define SAVED_BANK REG(TUNER_RAM + 0x00)
#define CENTS (*(volatile float *)(TUNER_RAM + 0x04))   /* smoothed pitch, cents above C0 */
#define MAGIC_REG REG(TUNER_RAM + 0x08)
#define VIEW REG(TUNER_RAM + 0x0C)      /* 0 tuner, 1 input probe, 2-4 sample probe (VALUE press) */
#define N_VIEWS 5
#define PROBE_HOLD ((volatile uint8_t *)(TUNER_RAM + 0x10))    /* 16 bar heights, decaying */
#define HOLD REG(TUNER_RAM + 0x20)      /* ticks the last pitch stays shown; 0 = none */
#define RECENT_C ((volatile float *)(TUNER_RAM + 0x24))     /* last 3 estimates, newest first */
#define N_RECENT REG(TUNER_RAM + 0x30)
#define SHOWN REG(TUNER_RAM + 0x34)     /* the note shown (semitones above C0) */
#define DRAWN REG(TUNER_RAM + 0x38)     /* what the screen shows (draw_key), to skip redraws */
#define MAGIC 0x54554E45u               /* "TUNE" */
#define HOLD_TICKS 10                   /* 0.5 s */
#define SMOOTH 0.3f                     /* per tick, towards the median of the last 3 */
#define AGREE 30                        /* cents: two estimates that count as the same note */
#define NEW_NOTE 50                     /* cents: farther than this, jump instead of smoothing */
#define SWITCH_AT 60                    /* cents from the shown note before the name changes */
#define LOG2_A4 8.78135971f             /* log2(440) */

/* --- input probe: which receive channel is the instrument input? ---
 * The audio task (ITCM 0x1CF78) points eDMA channel 4 at 0x20008000: two halves of 64
 * frames of 8 words; each word is a stereo pair, left in the high half (as the receive
 * handler FUN_0001CA80 reads it). The probe shows the peak of all 16 channels; the
 * recorder's own choice is word 1 when 0x82E011A8 is set, else the byte at 0x82E0E974. */
#define RX_BUF ((const volatile int32_t *)IMP(g_audio_rx_buf))
#define RX_WORDS (2 * 64 * 8)
#define REC_FLAG (*(volatile uint8_t *)IMP(g_rx_force_word1))
#define REC_SOURCE (*(volatile uint8_t *)IMP(g_rec_ctrl_source))
#define BAR_TOP 10
#define BAR_BOTTOM 54                   /* 16 steps of 6 dB, 2.75 px each */

/* --- the screen --- */

static const char *const NOTE_NAMES[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};

static char *put_int(char *p, int v)
{
    char tmp[12];
    int n = 0;
    if (v < 0) {
        *p++ = '-';
        v = -v;
    }
    do
        tmp[n++] = '0' + v % 10;
    while ((v /= 10) && n < 11);
    while (n)
        *p++ = tmp[--n];
    *p = 0;
    return p;
}

/* log2 for x > 0: exponent bits + 2 atanh((m - 1)/(m + 1)) for the mantissa m in [1, 2);
   error below 1e-6, i.e. far under a hundredth of a cent. */
static float log2_f(float x)
{
    union {
        float f;
        uint32_t u;
    } v = {x};
    int e = (int)((v.u >> 23) & 0xFF) - 127;
    v.u = (v.u & 0x7FFFFF) | 0x3F800000;
    float s = (v.f - 1) / (v.f + 1), s2 = s * s;
    float ln = 2 * s * (1 + s2 * (1.0f / 3 + s2 * (1.0f / 5 + s2 * (1.0f / 7 + s2 * (1.0f / 9)))));
    return e + ln * 1.44269504f;
}

static void tuner_open(void)
{
    if (MAGIC_REG != MAGIC) {
        MAGIC_REG = MAGIC;
        VIEW = 0;
    }
    if (VIEW >= N_VIEWS || !tuner_probes)
        VIEW = 0;
    HOLD = 0;
    N_RECENT = 0;
    DRAWN = 0xFFFFFFFEu;
    /* The instrument only reaches the mix (receive word 0) with EXT SOURCE on: switch it on
       as a press of the button would (TopScreen_HandleKey, key 0x12). */
    if (!PickStore_Get(PICK_STORE, PICK_EXT_SOURCE, 0))
        ExtSource_Toggle(0, 1);
    pitch_capture(1);
}

static void tuner_close(void)
{
    pitch_capture(0);
}

/* Layout: title row, the note big in the middle, the cents meter below it. */
#define METER_Y 52
#define METER_X0 4
#define METER_X1 123
#define METER_MID 64

static void tuner_draw(cpage_ctx *ctx)
{
    float total = CENTS;
    int semis = (int)SHOWN;
    float off = total - semis * 100;        /* -60..+60 (hysteresis) */
    int cents = (int)(off + (off < 0 ? -0.5f : 0.5f));
    if (off > 50)
        off = 50;
    if (off < -50)
        off = -50;
    char buf[16], *p;

    gfx_font(ctx, FONT_S);
    gfx_text(ctx, 0, 1, "TUNER");             /* row 0 is the cpu-meter's */
    gfx_text_right(ctx, GFX_W - 1, 1, "A4=440");
    if (!HOLD || total < 0) {
        gfx_font(ctx, FONT_L3);
        gfx_text_centred(ctx, METER_MID, 14, "-");
        gfx_font(ctx, FONT_S);
        gfx_line(ctx, METER_X0, METER_Y, METER_X1, METER_Y);
        return;
    }

    /* the note, with its octave small after it */
    gfx_font(ctx, FONT_L3);
    gfx_text_centred(ctx, METER_MID, 14, NOTE_NAMES[semis % 12]);
    gfx_font(ctx, FONT_S);
    put_int(buf, semis / 12);
    gfx_text(ctx, METER_MID + 16, 28, buf);
    if (cents >= -2 && cents <= 2)      /* in tune */
        gfx_frame(ctx, METER_MID - 22, 10, METER_MID + 22, 38);

    /* cents, signed, on the left */
    p = buf;
    if (cents > 0)
        *p++ = '+';
    p = put_int(p, cents);
    *p++ = 'c';
    *p = 0;
    gfx_text(ctx, 0, 28, buf);

    /* the meter: a scale from -50 to +50 cents, ticks every 10, and the needle */
    gfx_line(ctx, METER_X0, METER_Y, METER_X1, METER_Y);
    for (int c = -50; c <= 50; c += 10) {
        int x = METER_MID + c * (METER_X1 - METER_MID) / 50;
        int h = c == 0 ? 4 : 2;
        gfx_line(ctx, x, METER_Y - h, x, METER_Y + h);
    }
    int nx = METER_MID + (int)(off * (METER_X1 - METER_MID) / 50);
    gfx_fill(ctx, nx - 1, METER_Y - 9, nx + 1, METER_Y - 3);
    gfx_line(ctx, nx - 3, METER_Y - 3, nx, METER_Y);
    gfx_line(ctx, nx + 3, METER_Y - 3, nx, METER_Y);
}

static int bits(uint32_t v)            /* 0 for silence .. 16 for full scale: 6 dB a step */
{
    return v ? 32 - __builtin_clz(v) : 0;
}

static void probe_tick(int ms)
{
    (void)ms;
    uint32_t peak[16];
    for (int k = 0; k < 16; k++)        /* no "= {0}": gcc would call memset */
        peak[k] = 0;
    for (int i = 0; i < RX_WORDS; i++) {
        int32_t w = RX_BUF[i];
        int32_t l = (int16_t)(w >> 16), r = (int16_t)w;
        int k = (i & 7) * 2;
        uint32_t al = l < 0 ? -l : l, ar = r < 0 ? -r : r;
        if (al > peak[k])
            peak[k] = al;
        if (ar > peak[k + 1])
            peak[k + 1] = ar;
    }
    for (int k = 0; k < 16; k++) {
        int h = bits(peak[k]) * (BAR_BOTTOM - BAR_TOP) / 16;
        int held = PROBE_HOLD[k];
        PROBE_HOLD[k] = (uint8_t)(h >= held ? h : held - 1);   /* falls 20 px a second */
    }
    cpage_redraw();
}

/* 16 bars, a pair (L R) per receive word, the word number under each pair. */
static void probe_draw(cpage_ctx *ctx)
{
    char buf[24], *p;
    gfx_font(ctx, FONT_S);
    p = buf;
    for (const char *s = "RX  rec: word "; *s; s++)
        *p++ = *s;
    put_int(p, REC_FLAG ? 1 : REC_SOURCE);
    gfx_text(ctx, 0, 1, buf);
    for (int k = 0; k < 16; k++) {
        int x = (k / 2) * 16 + (k & 1) * 7 + 1;
        int h = PROBE_HOLD[k];
        gfx_frame(ctx, x, BAR_TOP, x + 5, BAR_BOTTOM);
        if (h > 0)
            gfx_fill(ctx, x, BAR_BOTTOM - h, x + 5, BAR_BOTTOM);
    }
    for (int w = 0; w < 8; w++) {
        put_int(buf, w);
        gfx_text_centred(ctx, w * 16 + 7, BAR_BOTTOM + 2, buf);
    }
}

/* The median of the last 3 estimates; with only 2, their mean if they agree; else 0. */
static int estimate(float *m)
{
    float a = RECENT_C[0], b = RECENT_C[1], c = RECENT_C[2];
    if (N_RECENT == 2 && a - b < AGREE && b - a < AGREE) {
        *m = (a + b) / 2;
        return 1;
    }
    if (N_RECENT < 3)
        return 0;
    *m = a > b ? (b > c ? b : (a > c ? c : a)) : (a > c ? a : (b > c ? c : b));
    return 1;
}

/* Everything tuner_draw shows, packed: the note, the needle's pixel, the cents. */
static uint32_t draw_key(void)
{
    if (!HOLD || CENTS < 0)
        return 0xFFFFFFFFu;
    float off = CENTS - (int)SHOWN * 100.0f;
    int cents = (int)(off + (off < 0 ? -0.5f : 0.5f));
    if (off > 50)
        off = 50;
    if (off < -50)
        off = -50;
    int nx = METER_MID + (int)(off * (METER_X1 - METER_MID) / 50);
    return (SHOWN & 0xFFFF) << 16 | (uint32_t)(nx & 0xFF) << 8 | (uint32_t)((cents + 128) & 0xFF);
}

static void tuner_tick(int ms)
{
    if (VIEW == 1) {
        probe_tick(ms);
        return;
    }
    if (VIEW >= 2) {                    /* sample probe: live values */
        cpage_redraw();
        return;
    }
    float hz;
    int r = pitch_detect(&hz);
    if (r == PITCH_OK) {
        cpage_user_activity();          /* playing counts as activity: no screen saver */
        float c = 5700 + 1200 * (log2_f(hz) - LOG2_A4), m;
        RECENT_C[2] = RECENT_C[1];
        RECENT_C[1] = RECENT_C[0];
        RECENT_C[0] = c;
        if (N_RECENT < 3)
            N_RECENT++;
        if (estimate(&m)) {
            float prev = CENTS, diff = m - prev;
            int jump = !HOLD || diff > NEW_NOTE || diff < -NEW_NOTE;
            CENTS = jump ? m : prev + SMOOTH * diff;
            float from = CENTS - (int)SHOWN * 100.0f;
            if (jump || from > SWITCH_AT || from < -SWITCH_AT)
                SHOWN = (uint32_t)(int)((CENTS + 50) / 100);
            HOLD = HOLD_TICKS;
        }
    } else if (r == PITCH_NONE && HOLD) {
        if (!--HOLD)
            N_RECENT = 0;
    }                                   /* PITCH_UNSURE (attack, noise): keep what's shown */
    uint32_t key = draw_key();
    if (key != DRAWN) {
        DRAWN = key;
        cpage_redraw();
    }
}

static void any_draw(cpage_ctx *ctx)
{
    if (VIEW == 1)
        probe_draw(ctx);
    else if (VIEW >= 2)
        sprobe_draw(ctx, (int)VIEW - 2);
    else
        tuner_draw(ctx);
}

static int tuner_key_down(int key)
{
    if (key == KEY_VALUE_PRESS && tuner_probes) {
        VIEW = (VIEW + 1) % N_VIEWS;
        DRAWN = 0xFFFFFFFEu;
        for (int k = 0; k < 16; k++)
            PROBE_HOLD[k] = 0;
        cpage_redraw();
        return 1;
    }
    return 0;
}

static const struct cpage tuner_page = {
    .open = tuner_open,
    .close = tuner_close,
    .draw = any_draw,
    .tick = tuner_tick,
    .key_down = tuner_key_down,
};

/* --- BANK A + BANK B on the top screen --- */

static void restore_bank(int bank)      /* as TopScreen_HandleKey before DJ mode */
{
    if (bank < 0 || bank > 9)
        return;
    PadStore_Set(PAD_STORE, 0, bank, 0);
    BANK_KEY_LAST = (uint8_t)(KEY_BANK_A + bank % 5);
    for (int i = 0; i < 5; i++)
        BANK_F_TO_J[i] = 0;
    BANK_KEY_INDEX = 0;
    if (bank > 4)
        BANK_F_TO_J[bank % 5] = 1;
}

/* Top screen vtable slot 0x18 (key down), word 0x8022CE74. */
int hook_top_key_down(void *page, int key)
{
    if ((key == KEY_BANK_A || key == KEY_BANK_B) && UI_GetCurrentPage() == PAGE_TOP) {
        int other = key == KEY_BANK_A ? KEY_BANK_B : KEY_BANK_A;
        if (Key_IsHeld(other)) {
            restore_bank((int)SAVED_BANK);
            cpage_open(&tuner_page);
            return 1;
        }
        SAVED_BANK = (uint32_t)PadStore_Get(PAD_STORE, 0, 0);
    }
    return TopScreen_KeyDown(page, key);
}
