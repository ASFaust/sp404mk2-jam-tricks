/* Host test of the scale-pad layout (no device needed):
 *   gcc -DHOST_TEST -Wall -o app1/build/test_inkey app1/payload/test_inkey.c app1/payload/inkey.c && app1/build/test_inkey
 * Checks every scale, root and offset: notes ascend through the pads in order, all are in
 * the scale, and Roland's own scale check (ChromPage_OnPadDown) would accept every pad.
 */
#include <stdio.h>

#include "inkey.h"

static const char *names[7] = {"Chrom", "Maj", "Min", "Dorian", "Phryd.", "Penta M", "Penta m"};
static const int masks[6] = {0xAB5, 0x5AD, 0x6AD, 0x5AB, 0x295, 0x4A9};   /* bit n = semitone n */

int main(void)
{
    int failures = 0;

    for (int scale = 1; scale <= 6; scale++)
        for (int root = 0; root < 12; root++)
            for (int offset = -8; offset <= 4; offset++) {
                int32_t t[16];
                inkey_fill(t, scale, root, offset);
                int prev = -1000;
                for (int i = 0; i < 16; i++) {
                    int pad = (3 - i / 4) * 4 + i % 4;      /* bottom-left first */
                    int semi = t[pad] - offset;             /* what Roland computes */
                    int pc = ((semi - root + 24) % 12);     /* Roland's check, exactly */
                    if (!(masks[scale - 1] >> pc & 1) || semi <= prev || semi - root + 24 < 0) {
                        printf("FAIL %s root %d offset %d pad %d semi %d\n",
                               names[scale], root, offset, pad, semi);
                        failures++;
                    }
                    prev = semi;
                }
            }

    /* Show two layouts the way they sit on the pads. */
    for (int scale = 1; scale <= 6; scale += 4) {
        int32_t t[16];
        inkey_fill(t, scale, 0, 0);
        printf("%s, root C, offset 0 (semitones from the sample's pitch):\n", names[scale]);
        for (int row = 0; row < 4; row++)
            printf("  %4d %4d %4d %4d\n", t[row * 4], t[row * 4 + 1], t[row * 4 + 2], t[row * 4 + 3]);
    }
    int32_t t[16];
    inkey_fill(t, 0, 5, 3);
    for (int pad = 0; pad < 16; pad++)
        if (t[pad] != (int[]){4, 5, 6, 7, 0, 1, 2, 3, -4, -3, -2, -1, -8, -7, -6, -5}[pad])
            failures++;

    printf(failures ? "%d FAILURES\n" : "all checks passed\n", failures);
    return failures != 0;
}
