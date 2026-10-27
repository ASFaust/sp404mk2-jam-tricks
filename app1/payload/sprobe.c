/* Sample probe (diagnostic, docs/app1/topics/sample-loop.md): what the current pad's sample looks
 * like in memory, to plan the loop crossfade. Views 2..4 of the tuner page (VALUE press
 * cycles them, tuner.c):
 *   2  summary: START/LOOP/END as the pad stores them and as the sample record has them, the
 *      RAM head's length, and live, a playing voice's source state
 *   3, 4  the raw sample record (26 words), in two halves
 * The current pad is bank * 16 + pad (PadStore ids 0 and 1). Hit the pad to see a voice.
 */
#include <stdint.h>

#include "imports.h"
#include "page.h"

#define PadStore_Get ((int (*)(void *, int, int))IMP(PadStore_Get))
#define SampleInfo_ForPad ((const uint32_t *(*)(int))IMP(SampleInfo_ForPad))
#define PAD_STORE ((void *)IMP(g_pad_store))
#define PAD_REC(pad) (IMP(g_pad_store) + (uint32_t)(pad) * 0xAC)
#define PAD_START 0xD4                          /* pad record words (as PadStore_Set clamps) */
#define PAD_END 0xD8
#define PAD_LOOP 0xFC

#define VOICE_SLOTS IMP(g_voice_slots)
#define N_SLOTS 32
#define SLOT_SIZE 0x58
#define VOICE_PAD 0x14274
#define VOICE_SRC_STATE 0x14260                 /* byte: 0 head, 1-5 segment, 7 ended */
#define VOICE_SRC_NEXT 0x14261                  /* byte: next segment */
#define VOICE_SRC_OFF (0x50ED * 4)              /* byte offset in the current region */
#define VOICE_SRC_LEFT (0x50F0 * 4)             /* frames left in the region (low word) */
#define VOICE_MODE (0x37 * 4)                   /* bit 0 reverse, bit 1 ping pong */

#define U32(a) (*(volatile uint32_t *)(a))
#define U8(a) (*(volatile uint8_t *)(a))

static char *put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    *p = 0;
    return p;
}

static char *put_dec(char *p, uint32_t v)
{
    char tmp[12];
    int n = 0;
    do
        tmp[n++] = '0' + v % 10;
    while ((v /= 10) && n < 11);
    while (n)
        *p++ = tmp[--n];
    *p = 0;
    return p;
}

static char *put_hex(char *p, uint32_t v, int digits)
{
    for (int i = digits - 1; i >= 0; i--)
        *p++ = "0123456789ABCDEF"[(v >> (4 * i)) & 0xF];
    *p = 0;
    return p;
}

static int current_pad(void)
{
    int bank = PadStore_Get(PAD_STORE, 0, 0), pad = PadStore_Get(PAD_STORE, 1, 0);
    if (bank < 0 || bank > 9 || pad < 0 || pad > 15)
        return -1;
    return bank * 16 + pad;
}

/* The first playing voice of the pad, or 0. */
static uint32_t pad_voice(int pad)
{
    for (int i = 0; i < N_SLOTS; i++) {
        uint32_t slot = VOICE_SLOTS + i * SLOT_SIZE;
        uint32_t voice = U32(slot);
        if (voice && U32(U32(slot + 4)) == 1 && (int)U32(voice + VOICE_PAD) == pad)
            return voice;
    }
    return 0;
}

#define ROW(i) (1 + 9 * (i))

static void row_pair(cpage_ctx *ctx, int y, const char *label, uint32_t a, uint32_t b)
{
    char buf[32], *p = put_str(buf, label);
    p = put_dec(p, a);
    *p++ = ' ';
    put_dec(p, b);
    gfx_text(ctx, 0, y, buf);
}

static void draw_summary(cpage_ctx *ctx, int pad, const uint32_t *rec)
{
    char buf[32], *p;
    uint32_t prec = PAD_REC(pad);

    p = put_str(buf, "SMP ");
    *p++ = "ABCDEFGHIJ"[pad / 16];
    *p++ = '-';
    p = put_dec(p, pad % 16 + 1);
    p = put_str(p, " #");
    put_dec(p, (uint32_t)pad);
    gfx_text(ctx, 0, ROW(0), buf);
    gfx_text_right(ctx, GFX_W - 1, ROW(0), "pad/rec");
    if (!rec) {
        gfx_text(ctx, 0, ROW(2), "no sample record");
        return;
    }
    row_pair(ctx, ROW(1), "ST ", U32(prec + PAD_START), rec[0x08 / 4]);
    row_pair(ctx, ROW(2), "LP ", U32(prec + PAD_LOOP), rec[0x20 / 4]);
    row_pair(ctx, ROW(3), "EN ", U32(prec + PAD_END), rec[0x10 / 4]);

    /* the RAM head, in bytes, and in seconds if it's 16-bit stereo at 48 kHz */
    uint32_t head = rec[0x54 / 4];
    p = put_str(buf, "HD ");
    p = put_dec(p, head);
    p = put_str(p, " ");
    uint32_t cs = head / 1920;                  /* hundredths of a second: 192000 bytes/s */
    p = put_dec(p, cs / 100);
    *p++ = '.';
    *p++ = '0' + cs / 10 % 10;
    *p++ = '0' + cs % 10;
    put_str(p, "s");
    gfx_text(ctx, 0, ROW(4), buf);

    uint32_t voice = pad_voice(pad);
    if (!voice) {
        gfx_text(ctx, 0, ROW(5), "V -");
        return;
    }
    p = put_str(buf, "V st");
    p = put_dec(p, U8(voice + VOICE_SRC_STATE));
    p = put_str(p, " nx");
    p = put_dec(p, U8(voice + VOICE_SRC_NEXT));
    p = put_str(p, " m");
    put_dec(p, U32(voice + VOICE_MODE));
    gfx_text(ctx, 0, ROW(5), buf);
    row_pair(ctx, ROW(6), "  ", U32(voice + VOICE_SRC_OFF), U32(voice + VOICE_SRC_LEFT));
}

static void draw_raw(cpage_ctx *ctx, const uint32_t *rec, int half)
{
    char buf[32], *p;
    if (!rec) {
        gfx_text(ctx, 0, ROW(0), "no sample record");
        return;
    }
    for (int r = 0; r < 7; r++) {
        int w = half * 14 + r * 2;              /* words 0..13, 14..25 */
        if (w >= 26)
            break;
        p = put_hex(buf, (uint32_t)w * 4, 2);
        for (int k = 0; k < 2 && w + k < 26; k++) {
            *p++ = ' ';
            p = put_hex(p, rec[w + k], 8);
        }
        gfx_text(ctx, 0, ROW(r), buf);
    }
}

/* view: 0 summary, 1 and 2 the raw record */
void sprobe_draw(cpage_ctx *ctx, int view)
{
    gfx_font(ctx, FONT_S);
    int pad = current_pad();
    if (pad < 0) {
        gfx_text(ctx, 0, ROW(0), "SMP: no pad");
        return;
    }
    const uint32_t *rec = SampleInfo_ForPad(pad);
    if (view == 0)
        draw_summary(ctx, pad, rec);
    else
        draw_raw(ctx, rec, view - 1);
}
