#ifndef TSX_H
#define TSX_H

/* VARI mode 3: transient-aware WSOLA splice stretcher (docs/app1/topics/time-stretch.md).
   C port of app1/proto/stretch/tsplice.py, streaming over Roland's source rings. */

#include <stddef.h>
#include <stdint.h>

#define TSX_RING 0x2800                 /* samples per source ring */
#define TSX_MODE 3                      /* Stretcher.mode handled here */
#define TSX_SCRATCH_FLOATS 4096         /* scratch the caller provides (16 KB) */
#define TSX_MARKER 0x31585354           /* "TSX1" in TsxState.marker: state initialised */

/* Roland's StretchRing (types.txt): header + 0x2800 samples of int16 << 13 */
typedef struct {
    int32_t block_pos[5];               /* 0x00 source position of each 0x800 block */
    uint32_t write_idx;                 /* 0x14 */
    uint32_t read_block;                /* 0x18 read_idx >> 7 */
    uint8_t _r1[0x38 - 0x1C];
    uint32_t read_idx;                  /* 0x38 */
    uint8_t _r2[0x48 - 0x3C];
    int32_t moved;                      /* 0x48 total read advance */
    uint8_t _r3[0x60 - 0x4C];
    int32_t data[TSX_RING];             /* 0x60 */
} TsxRing;

/* Roland's Stretcher, inside the voice at +0x140 (only the fields used here) */
typedef struct {
    uint8_t _r0[0x1C];
    int32_t mode;                       /* 0x1C VARI: 0 Off, 1 Backing, 2 Ensemble, 3 us */
    uint32_t rate_q14;                  /* 0x20 pitch: source samples per output sample */
    uint8_t _r1[0x40 - 0x24];
    TsxRing ring_l;                     /* 0x40 */
    TsxRing ring_r;                     /* 0xA0A0 */
    uint32_t state;                     /* 0x14100 StretchState* (32-bit target address) */
    float ratio;                        /* 0x14104 time ratio */
} TsxStretcher;

/* Our state, overlaid on the voice's 0x328-byte StretchState (OCRAM 0x2023F000 + i*0x328).
   Holes keep the fields Roland's code still touches for this voice: +0x48 (it reads it as
   "splicing" to fade out a voice that ends mid-splice), +0x4C (Stretch_Init sets -1 at
   every voice start: our reset marker), +0x54 (period, written at start) and +0x2C8
   (modulation depth, written by Stretch_ClampRatio). */
#define TSX_ONSETS 8
typedef struct {
    double pos;                         /* 0x000 main tap, absolute source position */
    double pos2;                        /* 0x008 second tap while fading */
    double ideal;                       /* 0x010 where the main tap should be */
    float t, dt, corr;                  /* 0x018 fade progress, step, correlation */
    float rej_drift;                    /* 0x024 drift at the last rejected search */
    int32_t holdoff;                    /* 0x028 */
    int32_t base;                       /* 0x02C ring index of absolute position 0 */
    int32_t abs_rd;                     /* 0x030 absolute position of ring read_idx */
    int32_t pre_skip;                   /* 0x034 onset (serial) whose catch-up failed */
    int32_t frames;                     /* 0x038 64-sample frames analysed */
    int32_t last_on_frame;              /* 0x03C frame of the last onset */
    float prev_m;                       /* 0x040 last mono sample of the previous frame */
    uint8_t fading;                     /* 0x044 */
    uint8_t _p45[3];
    uint8_t splice;                     /* 0x048 Roland: nonzero while fading */
    uint8_t _p49[3];
    int32_t marker;                     /* 0x04C Roland's corr_k: -1 = voice just started */
    int32_t on_count;                   /* 0x050 onsets found (serial) */
    int32_t _period;                    /* 0x054 Roland's, unused */
    float ehist[32];                    /* 0x058 frame energies, by frame & 31 */
    int32_t on_pos[TSX_ONSETS];         /* 0x0D8 onset position, by serial & 7 */
    int32_t on_zone[TSX_ONSETS];        /* 0x0F8 end of its attack zone (INT32_MAX: open) */
    int32_t on_j[TSX_ONSETS];           /* 0x118 zone search: last frame examined */
    float on_peak[TSX_ONSETS];          /* 0x138 zone search: peak smoothed energy (<0: unset) */
    int32_t oi;                         /* 0x158 serial of the first onset >= pos */
    uint8_t _p15c[0x2C8 - 0x15C];
    float _mod_depth;                   /* 0x2C8 Roland's, unused */
    uint8_t _p2cc[0x328 - 0x2CC];
} TsxState;

/* Produce up to n output samples (n <= 0x800) from the voice's rings. out_r may be NULL
   (nch 1). Returns the number produced (fewer when the ring has run dry); *pos_out gets
   the source position tag of the current block, or -1 when it ran dry. scratch: at least
   TSX_SCRATCH_FLOATS floats this call may overwrite. */
int tsx_run(TsxStretcher *s, TsxState *st, int16_t *out_l, int16_t *out_r, int n,
            int32_t *pos_out, int nch, float *scratch);

/* Entry points replacing Roland's Resampler_Stereo / Resampler_Mono thunks: mode 3 runs
   tsx_run, every other mode goes to Roland's original. */
int tsx_resampler_stereo(TsxStretcher *s, int16_t *out_l, int16_t *out_r, int n,
                         int32_t *pos_out);
int tsx_resampler_mono(TsxStretcher *s, int16_t *out, int n, int32_t *pos_out);

/* vari.c: a voice switched from mode 3 to Roland's gets its stretch state re-initialised. */
void vari_leave_tsx(TsxStretcher *s);

#endif
