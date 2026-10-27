/* Scale pads: in chromatic mode, the 16 pads play the notes of the chosen scale.
 *
 * Roland's pads get their pitch from four identical tables (semitones per pad), always
 * subtracting chrom_offset: semi = table[pad] - chrom_offset. A scale only hides the
 * pads whose note isn't in it. We rewrite all four tables so that every pad holds an
 * in-scale note; Roland's own scale check then passes for every pad, and the screen,
 * the sound and the lights follow. See docs/app1/topics/chromatic-mode.md.
 *
 * Layout: the bottom-left pad is the root, one octave below the sample's pitch; the
 * scale climbs left to right, then row by row upwards. chrom_offset (VALUE knob: 4 per
 * step, SHIFT: 1) moves the layout by scale steps instead of semitones.
 */
#include <stdint.h>

#include "inkey.h"

/* Roland's pad order: pads 0-3 are the top row, 12-15 the bottom row. */
static const int8_t chromatic_layout[16] = {
    4, 5, 6, 7, 0, 1, 2, 3, -4, -3, -2, -1, -8, -7, -6, -5,
};

/* Same order as SCALE (param 0xF1) 1..6; the 0 entries after n_notes are unused. */
static const uint8_t n_notes[6] = {7, 7, 7, 7, 5, 5};
static const uint8_t intervals[6][7] = {
    {0, 2, 4, 5, 7, 9, 11},     /* Maj */
    {0, 2, 3, 5, 7, 8, 10},     /* Min */
    {0, 2, 3, 5, 7, 9, 10},     /* Dorian */
    {0, 1, 3, 5, 7, 8, 10},     /* Phryd. */
    {0, 2, 4, 7, 9},            /* Penta M */
    {0, 3, 5, 7, 10},           /* Penta m */
};

void inkey_fill(int32_t out[16], int32_t scale, int32_t root, int32_t offset)
{
    for (int pad = 0; pad < 16; pad++) {
        if (scale < 1 || scale > 6) {           /* Chrom: Roland's own layout */
            out[pad] = chromatic_layout[pad];
            continue;
        }
        int n = n_notes[scale - 1];
        int degree = (3 - pad / 4) * 4 + pad % 4 - offset;
        int octave = degree / n, step = degree % n;
        if (step < 0) {                         /* floor division for negative degrees */
            step += n;
            octave--;
        }
        int semi = root - 12 + 12 * octave + intervals[scale - 1][step];
        out[pad] = semi + offset;               /* Roland subtracts the offset again */
    }
}

#ifndef HOST_TEST
#include "imports.h"

/* Roland's state and the four copies of the pad table (names as in app1/tools/symbols.txt). */
#define CHROM_OFFSET (*(volatile int32_t *)IMP(g_param_store_chrom_offset))
#define CHROM_SCALE  (*(volatile int32_t *)IMP(g_param_store_chrom_scale))
#define CHROM_ROOT   (*(volatile int32_t *)IMP(g_param_store_chrom_root))

void inkey_update(void)
{
    volatile int32_t *const tables[4] = {
        (volatile int32_t *)IMP(g_chrom_semis_trigger),
        (volatile int32_t *)IMP(g_chrom_semis_pitch),
        (volatile int32_t *)IMP(g_chrom_semis_page),
        (volatile int32_t *)IMP(g_chrom_semis_lights),
    };
    int32_t semis[16];

    inkey_fill(semis, CHROM_SCALE, CHROM_ROOT, CHROM_OFFSET);
    for (int t = 0; t < 4; t++)
        for (int pad = 0; pad < 16; pad++)
            tables[t][pad] = semis[pad];
}
#endif
