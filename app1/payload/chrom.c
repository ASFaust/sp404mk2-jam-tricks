/* Chromatic voices: a polyphony limit for POLY mode, set with REMAIN held + VALUE wheel on
 * the chromatic page (patch "chrom-voices", docs/app1/topics/chromatic-mode.md). The same page
 * hooks also serve the in-key layout of the scale-pads patches; make_image.py sets the flag
 * of each patch chosen (chrom_layout, chrom_voices) in the image.
 *
 * Roland's chromatic mode (param 0xF3, bits 3-4 of pad param 0x70) is 0 MONO, 1 LEGATO,
 * 2 POLY. We keep the voice count for POLY in bits 6-8 of the same pad word, which nothing
 * else uses: 0 (every existing project) means no limit, as Roland's POLY, and 1..7 mean
 * 8..2 voices (9 - value). The wheel steps LEGATO, MONO, POLY 2 ... POLY 8, POLY inf;
 * REMAIN alone only shows the setting (Roland's cycle is off). When
 * a pad starts a note and the chromatic sample already plays that many notes, the oldest is
 * released like a second press would.
 *
 * Parameter writes are asynchronous: Param_Set posts a message that PARAM_TASK (priority 31)
 * applies later, so a read right after a write still sees the old word. We write the whole
 * word once per step and remember it (CHROM_RAM) so quick wheel turns build on our own copy.
 */
#include <stdint.h>

#include "imports.h"
#include "inkey.h"
#include "ram.h"

#define REG(a) (*(volatile uint32_t *)(a))

/* Feature flags, 0 here; make_image.py writes 1 into the image for the patches chosen. */
const volatile uint8_t chrom_layout = 0;        /* scale-pads: rewrite the pad tables (inkey.c) */
const volatile uint8_t chrom_voices = 0;        /* chrom-voices: REMAIN + wheel, the limit */

#define PARAM_STORE ((void *)IMP(g_param_store))
#define Param_Get ((int (*)(void *, int, int))IMP(Param_Get))
#define Param_Set ((void (*)(void *, int, int, int))IMP(Param_Set))
#define Key_IsHeld ((int (*)(int))IMP(Key_IsHeld))
#define Popup_Show ((void (*)(const char *, uint32_t))IMP(Popup_Show))      /* copies the text */
#define Page_PopupTime ((uint32_t (*)(void *, int))IMP(Page_PopupTime))
#define Voice_Release ((void (*)(uint32_t, int))IMP(Voice_Release))         /* (slot, trigger type) */
#define ChromPage_OnKey ((int (*)(void *, int))IMP(ChromPage_OnKey))
#define ChromPage_OnPadDown ((void (*)(void *, int, int))IMP(ChromPage_OnPadDown))
#define REDRAW_FLAGS (*(volatile uint16_t *)IMP(g_redraw_flags))
#define CHROM_OFFSET (*(volatile int32_t *)IMP(g_param_store_chrom_offset))
#define SEMIS_TRIGGER ((volatile int32_t *)IMP(g_chrom_semis_trigger))
#define SYS_TIME_MS REG(IMP(g_sys_time_ms))                                 /* low word */

#define PENDING_WORD REG(CHROM_RAM + 0)  /* pad word 0x70 as we last wrote it */
#define PENDING_TIME REG(CHROM_RAM + 4)  /* when (ms) */
#define PENDING_MAGIC REG(CHROM_RAM + 8) /* MAGIC once written (RAM is garbage at power-on) */
#define MAGIC 0x43485256u                /* "CHRV" */
#define PENDING_MS 1000                  /* trust our copy this long after a write */

static int pending(void)
{
    return PENDING_MAGIC == MAGIC && SYS_TIME_MS - PENDING_TIME < PENDING_MS;
}

#define VOICE_SLOTS IMP(g_voice_slots)  /* 32 x 0x58 */
#define SLOT_SIZE 0x58u
#define TRIG_CHROMATIC 0xC              /* slot +0x2C: trigger type */

#define KEY_REMAIN 0x24
#define KEY_WHEEL_DOWN 0x3F
#define KEY_WHEEL_UP 0x40

#define MODE_MONO 0
#define MODE_LEGATO 1
#define MODE_POLY 2
#define MODE_SHIFT 3
#define MODE_MASK (3u << MODE_SHIFT)
#define VOICES_SHIFT 6
#define VOICES_MASK (7u << VOICES_SHIFT)
#define MAX_VOICES 8
#define POS_INF (MAX_VOICES + 1)        /* wheel position after POLY 8: no limit */

/* Pad param 0x70 as Param_Set reads it: the store's vtable slot 8. */
static uint32_t pad_word(int index)
{
    uint32_t (*get)(void *, int, int) = (*(uint32_t (***)(void *, int, int))PARAM_STORE)[2];
    return get(PARAM_STORE, 0x70, index);
}

/* POLY voices from the pad word; 0 = no limit */
static int poly_voices(uint32_t word)
{
    int k = (int)((word & VOICES_MASK) >> VOICES_SHIFT);
    return k == 0 ? 0 : MAX_VOICES + 1 - k;
}

/* ---- REMAIN + wheel: LEGATO, MONO, POLY 2 .. POLY 8, POLY inf ---- */

/* The current pad's word: our last write while the parameter task may not have applied it. */
static uint32_t current_word(void)
{
    if (pending())
        return PENDING_WORD;
    return pad_word(-1);                        /* the current pad, as Roland's REMAIN uses */
}

/* position on the wheel: 0 LEGATO, 1 MONO, 2..8 POLY with that many voices, 9 POLY inf */
static int wheel_pos(uint32_t word)
{
    int mode = (int)((word & MODE_MASK) >> MODE_SHIFT);
    if (mode == MODE_LEGATO)
        return 0;
    if (mode == MODE_POLY) {
        int v = poly_voices(word);
        return v == 0 ? POS_INF : v;
    }
    return 1;
}

static void show(void *page, int pos)
{
    char text[16];
    const char *s;
    if (pos == 0) {
        s = "LEGATO mode";
    } else if (pos == 1) {
        s = "MONO mode";
    } else if (pos == POS_INF) {
        s = "POLY inf";
    } else {
        const char *t = "POLY 0 voices";
        int i = 0;
        for (; t[i]; i++)
            text[i] = t[i];
        text[i] = 0;
        text[5] = (char)('0' + pos);
        s = text;
    }
    Popup_Show(s, Page_PopupTime(page, 0));
}

static void step_mode(void *page, int dir)
{
    uint32_t word = current_word();
    int pos = wheel_pos(word) + dir;
    if (pos < 0)
        pos = 0;
    if (pos > POS_INF)
        pos = POS_INF;
    uint32_t mode = pos == 0 ? MODE_LEGATO : pos == 1 ? MODE_MONO : MODE_POLY;
    word &= ~MODE_MASK;
    word |= mode << MODE_SHIFT;
    if (mode == MODE_POLY)
        word = (word & ~VOICES_MASK)
            | ((uint32_t)(pos == POS_INF ? 0 : MAX_VOICES + 1 - pos) << VOICES_SHIFT);
    Param_Set(PARAM_STORE, 0x70, (int)word, -1);    /* one message with mode and count */
    PENDING_WORD = word;
    PENDING_TIME = SYS_TIME_MS;
    PENDING_MAGIC = MAGIC;
    show(page, pos);
    REDRAW_FLAGS = 0xFFFF;
}

static int voices_on_key(void *page, int key)
{
    if ((key == KEY_WHEEL_DOWN || key == KEY_WHEEL_UP) && Key_IsHeld(KEY_REMAIN)) {
        step_mode(page, key == KEY_WHEEL_UP ? 1 : -1);
        return 1;
    }
    if (key == KEY_REMAIN) {                    /* instead of Roland's MONO/LEGATO/POLY cycle */
        show(page, wheel_pos(current_word()));
        return 1;
    }
    return 0;
}

/* ---- the limit ---- */

/* Before a chromatic pad starts its note: in POLY, release the oldest notes of the chromatic
   sample until one more fits. Notes already releasing don't count; a note that's sounding
   already isn't limited (Roland handles pressing it again). */
static void limit_voices(int pad)
{
    if (!Param_Get(PARAM_STORE, 0xBF, -1))      /* chromatic mode off */
        return;
    int cpad = Param_Get(PARAM_STORE, 0xC1, -1);
    if (cpad < 0)
        return;
    uint32_t word = pending() ? PENDING_WORD : pad_word(cpad);
    if (((word & MODE_MASK) >> MODE_SHIFT) != MODE_POLY)
        return;
    int limit = poly_voices(word);
    if (limit == 0)                             /* POLY inf */
        return;
    int32_t semi = SEMIS_TRIGGER[pad % 16] - CHROM_OFFSET;
    uint32_t skip = 0;                          /* slots Voice_Release didn't stop */

    for (int round = 0; round < 32; round++) {
        int count = 0, oldest = -1;
        uint32_t oldest_seq = 0;
        for (int i = 0; i < 32; i++) {
            uint32_t slot = VOICE_SLOTS + (uint32_t)i * SLOT_SIZE;
            uint32_t voice = REG(slot);
            if (REG(REG(slot + 4)) != 1 || !voice || REG(slot + 0x2C) != TRIG_CHROMATIC
                    || REG(voice + 0x14274) != (uint32_t)cpad || REG(voice + 0x1439C) != 0
                    || (skip & (1u << i)))
                continue;
            if ((int32_t)REG(slot + 0x4C) == semi)
                return;                         /* this note is sounding already */
            uint32_t seq = REG(slot + 0x14);    /* start sequence number */
            if (oldest < 0 || (int32_t)(seq - oldest_seq) < 0) {
                oldest = i;
                oldest_seq = seq;
            }
            count++;
        }
        if (count < limit || oldest < 0)
            return;
        uint32_t slot = VOICE_SLOTS + (uint32_t)oldest * SLOT_SIZE;
        Voice_Release(slot, TRIG_CHROMATIC);
        uint32_t voice = REG(slot);
        if (!voice || REG(voice + 0x1439C) == 0)    /* not marked as stopping: don't retry */
            skip |= 1u << oldest;
    }
}

/* Chromatic page vtable slot 0xD8 (word 0x802214CC), patches scale-pads* and chrom-voices. */
int hook_chrom_on_key(void *page, int key)
{
    int r = 0;
    if (chrom_voices)
        r = voices_on_key(page, key);
    if (!r)
        r = ChromPage_OnKey(page, key);
    if (chrom_layout)
        inkey_update();                         /* the key may have changed scale, root or offset */
    return r;
}

/* Chromatic page vtable slot 0xFC (word 0x802214F0). */
void hook_chrom_pad_down(void *page, int pad, int arg)
{
    if (chrom_layout)
        inkey_update();
    if (chrom_voices)
        limit_voices(pad);
    ChromPage_OnPadDown(page, pad, arg);
}

/* ---- pitch base: the pad's PITCH/FINE under every chromatic note ----
   Pad_Trigger sets a chromatic voice's pitch with Voice_SetParam(slot, 0x73, note) and
   (slot, 0x74, 0), after Voice_Start and when MONO/LEGATO retune a playing voice (its six
   calls are at 0x800D73CC..0x800D7456). The chrom-voices patch sends Voice_SetParam
   here first (hooks.S voice_set_param_orig runs Roland's); for those callers, PITCH gets the
   pad's PITCH added and FINE the pad's FINE, so the pitch/speed panel sets the base. */

#define PAD_STORE ((void *)IMP(g_pad_store))
#define PadStore_Get ((int (*)(void *, int, int))IMP(PadStore_Get))
#define PAD_TRIGGER_LR_LO IMP(Pad_Trigger_pitch_calls_lo)  /* return addresses of Pad_Trigger's pitch calls */
#define PAD_TRIGGER_LR_HI IMP(Pad_Trigger_pitch_calls_hi)

void voice_set_param_orig(uint32_t *slot, int id, int value);

void hook_voice_set_param(uint32_t *slot, int id, int value)
{
    uint32_t lr = (uint32_t)__builtin_return_address(0);
    if ((id == 0x73 || id == 0x74) && chrom_voices
            && lr >= PAD_TRIGGER_LR_LO && lr <= PAD_TRIGGER_LR_HI && slot[0]) {
        int pad = (int)REG(slot[0] + 0x14274);  /* the pad this voice plays */
        if (pad >= 0)
            value += PadStore_Get(PAD_STORE, id, pad);
    }
    voice_set_param_orig(slot, id, value);
}
