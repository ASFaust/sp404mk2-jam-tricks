#ifndef INKEY_H
#define INKEY_H

#include <stdint.h>

/* The 16 table values Roland's code should see for this scale, root and offset
   (scale: 0 Chrom, 1 Maj, 2 Min, 3 Dorian, 4 Phryd., 5 Penta M, 6 Penta m). */
void inkey_fill(int32_t out[16], int32_t scale, int32_t root, int32_t offset);

/* Rewrites Roland's four pad tables from the current scale, root and offset. */
void inkey_update(void);

#endif
