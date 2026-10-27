/* VARI mode 3: transient-aware WSOLA splice stretcher. A rule-for-rule port of
   app1/proto/stretch/tsplice.py (_run, onset_map, _best_jump, _ncc), streaming: attacks are
   found as the source rings fill, and look-ahead/history come from the ring indices.
   Design and listening results: docs/app1/topics/time-stretch.md. */

#include "tsx.h"

#if defined(__ARM_FEATURE_DSP)
#include <arm_acle.h>
#endif

/* tsplice.DEFAULTS */
#define THR 480                 /* drift (source samples) that triggers a splice */
#define HARD 2400               /* drift that forces one */
#define C_MIN 0.95f             /* correlation required at THR, relaxing to 0 at HARD */
#define C_PRE 0.0f              /* ... for a catch-up just before an attack */
#define RETRY 512               /* after a rejected search, wait until the drift moved this much */
#define XFADE 480               /* fade length at a perfect match (output samples) */
#define XFADE_MAX 1920          /* ... at correlation 0 */
#define XFADE_ONSET 96          /* fade when landing on an attack */
#define SEARCH 800              /* +- search around the correcting jump */
#define CORR_LEN 2048           /* coarse correlation window */
#define FINE_LEN 512            /* fine correlation window */
#define DECIM 8                 /* coarse search on an 8x decimated copy */
#define GUARD_PRE 64            /* land this far before an attack */
#define GUARD_POST 1200         /* attack zone: at least this long */
#define ONSET_RATIO 2.0f
#define ONSET_FLOOR 1e-6f
#define ZONE_DROP 0.15848932f   /* zone ends 8 dB below its peak */
#define ZONE_MAX 7200
#define LOOKAHEAD 6144          /* at most this much look-ahead is used */
#define HISTORY 4096            /* ... and history */

#define FRAME 64
#define ONSET_REF 24
#define REFRACTORY 2400
#define OPEN INT32_MAX
#define FAR (1 << 29)

#ifdef TSX_HOST
/* PC test bench only: a log of splice decisions (app1/proto/stretch/tsx_c.py reads it) */
double tsx_log[4096][6];
int tsx_log_n, tsx_count[8];
#define LOG(n, j, c, d, why, abs_pos) do { if (tsx_log_n < 4096) { double *e_ = tsx_log[tsx_log_n++]; \
    e_[0] = (n); e_[1] = (j); e_[2] = (c); e_[3] = (d); e_[4] = (why); e_[5] = (abs_pos); } } while (0)
#define COUNT(i) (tsx_count[i]++)
#else
#define LOG(n, j, c, d, why, abs_pos) do { } while (0)
#define COUNT(i) do { } while (0)
#endif

#ifdef TSX_PROFILE
/* QEMU bench only (app1/proto/stretch/qemu): SysTick ticks spent per part */
extern uint32_t tsx_prof[8];
#define PROF_T (*(volatile uint32_t *)0xE000E018)
#define PROF_BEGIN uint32_t prof_t0_ = PROF_T
#define PROF_END(i) (tsx_prof[i] += (prof_t0_ - PROF_T) & 0xFFFFFF)
#else
#define PROF_BEGIN do { } while (0)
#define PROF_END(i) do { } while (0)
#endif

#define RING_SCALE (1.0f / 8192.0f)             /* ring sample -> int16 scale */
#define MONO_SCALE (1.0f / 268435456.0f)        /* ring sample -> +-1 */

static inline float fsqrt(float x)
{
#if defined(__ARM_FP)
    float r;
    __asm__("vsqrt.f32 %0, %1" : "=t"(r) : "t"(x));
    return r;
#else
    return __builtin_sqrtf(x);
#endif
}

static inline int32_t imin(int32_t a, int32_t b) { return a < b ? a : b; }
static inline int32_t imax(int32_t a, int32_t b) { return a > b ? a : b; }

/* Python's // (floor division) for d > 0 */
static inline int32_t floordiv(int32_t a, int32_t d) { return a >= 0 ? a / d : -((-a + d - 1) / d); }

static inline uint32_t ridx(const TsxState *st, int32_t a)
{
    return (uint32_t)(a + st->base) % TSX_RING;
}

/* Mono (+-1) at absolute position a; 0 before the start. */
static inline float mono_at(const TsxStretcher *s, const TsxState *st, int nch, int32_t a)
{
    if (a < 0)
        return 0.0f;
    uint32_t i = ridx(st, a);
    if (nch == 2)
        return (float)(s->ring_l.data[i] + s->ring_r.data[i]) * (0.5f * MONO_SCALE);
    return (float)s->ring_l.data[i] * MONO_SCALE;
}

/* Mono as int16 at ring index i: (L + R) / 2. */
static inline int32_t mono16(const TsxStretcher *s, int nch, uint32_t i)
{
    if (nch == 2)
        return (s->ring_l.data[i] + s->ring_r.data[i]) >> 14;
    return s->ring_l.data[i] >> 13;
}

/* n int16 mono samples from absolute position a (0 before the start). */
static void copy_mono(const TsxStretcher *s, const TsxState *st, int nch, int32_t a, int32_t n,
                      int16_t *dst)
{
    int32_t k = 0;
    for (; k < n && a + k < 0; k++)
        dst[k] = 0;
    if (k == n)
        return;
    uint32_t i = ridx(st, a + k);
    for (; k < n; k++) {
        dst[k] = (int16_t)mono16(s, nch, i);
        if (++i == TSX_RING)
            i = 0;
    }
}

/* n decimated samples from q0 on: dst[q] = mean of mono (8(q0+q)-7 .. 8(q0+q)), the box
   filter of tsplice (mono_d). */
static void copy_dec(const TsxStretcher *s, const TsxState *st, int nch, int32_t q0, int32_t n,
                     int16_t *dst)
{
    int32_t a = q0 * DECIM - (DECIM - 1);
    uint32_t i = ridx(st, a > 0 ? a : 0);
    for (int32_t q = 0; q < n; q++) {
        int32_t sum = 0;
        for (int d = 0; d < DECIM; d++, a++) {
            if (a < 0)
                continue;
            sum += mono16(s, nch, i);
            if (++i == TSX_RING)
                i = 0;
        }
        dst[q] = (int16_t)(sum >> 3);
    }
}

typedef uint32_t __attribute__((may_alias, aligned(4))) pair16;   /* two int16 */

/* Sum of a[i] * b[i]: a, b 4-byte aligned, n even. Two multiply-adds per SMLALD. */
static int64_t dot16(const int16_t *a, const int16_t *b, int32_t n)
{
#if defined(__ARM_FEATURE_DSP)
    const pair16 *pa = (const pair16 *)a, *pb = (const pair16 *)b;
    int64_t acc0 = 0, acc1 = 0;
    int32_t m = n / 2, i = 0;
    for (; i + 1 < m; i += 2) {
        acc0 = __smlald((int32_t)pa[i], (int32_t)pb[i], acc0);
        acc1 = __smlald((int32_t)pa[i + 1], (int32_t)pb[i + 1], acc1);
    }
    if (i < m)
        acc0 = __smlald((int32_t)pa[i], (int32_t)pb[i], acc0);
    return acc0 + acc1;
#else
    int64_t acc = 0;
    for (int32_t i = 0; i < n; i++)
        acc += (int32_t)a[i] * b[i];
    return acc;
#endif
}

/* ---- attack detection, as the rings fill (tsplice.onset_map) ---- */

static float smoothed(const TsxState *st, int32_t j)
{
    int32_t lo = imax(j - 3, 0);
    float sum = 0.0f;
    for (int32_t i = lo; i <= j + 4; i++)
        sum += st->ehist[i & 31];
    return sum / (float)(j + 4 - lo + 1);
}

/* Advance the zone search of onset serial o, now that frames up to `newest` exist. */
static void advance_zone(TsxState *st, int32_t o, int32_t newest)
{
    int q = o & (TSX_ONSETS - 1);
    int32_t k0 = st->on_pos[q] / FRAME;
    if (st->on_peak[q] < 0.0f) {
        if (k0 + 4 > newest)
            return;
        st->on_peak[q] = smoothed(st, k0);
        st->on_j[q] = k0;
    }
    while (st->on_j[q] + 1 + 4 <= newest) {
        if ((st->on_j[q] - k0) * FRAME >= ZONE_MAX)
            break;
        int32_t j = ++st->on_j[q];
        float es = smoothed(st, j);
        if (es > st->on_peak[q])
            st->on_peak[q] = es;
        if ((j - k0) * FRAME >= GUARD_POST && es < st->on_peak[q] * ZONE_DROP) {
            st->on_zone[q] = j * FRAME;
            return;
        }
    }
    if ((st->on_j[q] - k0) * FRAME >= ZONE_MAX)
        st->on_zone[q] = st->on_j[q] * FRAME;
}

static void analyse(const TsxStretcher *s, TsxState *st, int nch, int32_t abs_write)
{
    while ((st->frames + 1) * FRAME <= abs_write) {
        int32_t k = st->frames, b = k * FRAME;
        float prev = st->prev_m, sum = 0.0f;
        uint32_t ri = ridx(st, b);
        for (int32_t i = b; i < b + FRAME; i++) {
            float m = nch == 2
                ? (float)(s->ring_l.data[ri] + s->ring_r.data[ri]) * (0.5f * MONO_SCALE)
                : (float)s->ring_l.data[ri] * MONO_SCALE;
            if (++ri == TSX_RING)
                ri = 0;
            if (i >= 1) {
                float d = m - prev;
                sum += d * d;
            }
            prev = m;
        }
        st->prev_m = prev;
        float e = sum / FRAME;
        st->ehist[k & 31] = e;

        float ref = 0.0f;                       /* loudest of the last ONSET_REF frames */
        for (int32_t i = imax(k - ONSET_REF, 0); i < k; i++)
            if (st->ehist[i & 31] > ref)
                ref = st->ehist[i & 31];
        float floor_ref = ref > ONSET_FLOOR * 0.1f ? ref : ONSET_FLOOR * 0.1f;
        if (e > ONSET_FLOOR && e > ONSET_RATIO * floor_ref
                && (k - st->last_on_frame) * FRAME > REFRACTORY) {
            int q = st->on_count & (TSX_ONSETS - 1);
            st->on_pos[q] = b;
            st->on_zone[q] = OPEN;
            st->on_j[q] = k;
            st->on_peak[q] = -1.0f;
            st->on_count++;
            st->last_on_frame = k;
        }
        st->frames = k + 1;
        for (int32_t o = imax(st->on_count - TSX_ONSETS, 0); o < st->on_count; o++)
            if (st->on_zone[o & (TSX_ONSETS - 1)] == OPEN)
                advance_zone(st, o, k);
    }
}

/* ---- interpolation (tsplice._sinc_tap, INTERP_LINEAR) ----
   Linear: a listening test (app1/proto/stretch/interp.py) found no audible difference from a
   26-tap windowed sinc, at a fraction of the cost. */

static inline float ring_at(const int32_t *d, const TsxState *st, int32_t a)
{
    return a < 0 ? 0.0f : (float)d[ridx(st, a)];    /* before the sample's start: silence */
}

/* Both channels at position pos, in int16 scale; whole: pitch 1.0 (positions are whole
   samples, a plain copy). */
static void tap(const TsxStretcher *s, const TsxState *st, int nch, double pos, int whole,
                float *vl, float *vr)
{
    int32_t i0 = (int32_t)pos;
    if (whole) {
        uint32_t i = ridx(st, i0);
        *vl = (float)s->ring_l.data[i] * RING_SCALE;
        *vr = nch == 2 ? (float)s->ring_r.data[i] * RING_SCALE : 0.0f;
        return;
    }
    float f = (float)(pos - (double)i0);
    float a, b;
    if (i0 < 0) {                               /* at the sample's start */
        a = ring_at(s->ring_l.data, st, i0);
        b = ring_at(s->ring_l.data, st, i0 + 1);
        *vl = (a + f * (b - a)) * RING_SCALE;
        a = ring_at(s->ring_r.data, st, i0);
        b = ring_at(s->ring_r.data, st, i0 + 1);
        *vr = nch == 2 ? (a + f * (b - a)) * RING_SCALE : 0.0f;
        return;
    }
    uint32_t i = ridx(st, i0), j = i + 1 == TSX_RING ? 0 : i + 1;   /* one modulo per tap */
    a = (float)s->ring_l.data[i];
    b = (float)s->ring_l.data[j];
    *vl = (a + f * (b - a)) * RING_SCALE;
    if (nch == 2) {
        a = (float)s->ring_r.data[i];
        b = (float)s->ring_r.data[j];
        *vr = (a + f * (b - a)) * RING_SCALE;
    } else {
        *vr = 0.0f;
    }
}

/* ---- correlation (tsplice._ncc / _best_jump) ---- */

static float ncc_finish(float sab, float saa, float sbb, int32_t m)
{
    if (saa < 1e-9f * (float)m && sbb < 1e-9f * (float)m)
        return 1.0f;                    /* both silent: any splice is fine */
    if (saa <= 1e-12f || sbb <= 1e-12f)
        return 0.0f;
    return sab / fsqrt(saa * sbb);
}

#define I16_SCALE (1.0f / 1073741824.0f)        /* int16 products -> +-1 products */

/* int64 -> float without libgcc (the payload isn't linked with it) */
static inline float i64f(int64_t v)
{
    return (float)(int32_t)(v >> 32) * 4294967296.0f + (float)(uint32_t)v;
}

/* Full-rate correlation of the mono signal at a and b over len samples. */
static float ncc_mono(const TsxStretcher *s, const TsxState *st, int nch, int32_t a, int32_t b,
                      int32_t len)
{
    int64_t sab = 0, saa = 0, sbb = 0;
    int32_t k = 0;
    for (; k < len && (a + k < 0 || b + k < 0); k++) {     /* rare: at the very start */
        float va = mono_at(s, st, nch, a + k), vb = mono_at(s, st, nch, b + k);
        int32_t ia = (int32_t)(va * 32768.0f), ib = (int32_t)(vb * 32768.0f);
        sab += ia * ib;
        saa += ia * ia;
        sbb += ib * ib;
    }
    if (k < len) {
        uint32_t i = ridx(st, a + k), j = ridx(st, b + k);
        for (; k < len; k++) {
            int32_t va = mono16(s, nch, i), vb = mono16(s, nch, j);
            sab += va * vb;
            saa += va * va;
            sbb += vb * vb;
            if (++i == TSX_RING)
                i = 0;
            if (++j == TSX_RING)
                j = 0;
        }
    }
    return ncc_finish(i64f(sab) * I16_SCALE, i64f(saa) * I16_SCALE, i64f(sbb) * I16_SCALE, len);
}

/* buf[0..n) shifted by one into buf2 (so every window has an aligned copy) */
static void shift_copy(const int16_t *src, int16_t *dst, int32_t n)
{
    for (int32_t i = 0; i + 1 < n; i++)
        dst[i] = src[i + 1];
}

/* Window of length n starting at offset o of a region held as `even` (= region) and `odd`
   (= region shifted by one): an aligned pointer either way. */
static inline const int16_t *window(const int16_t *even, const int16_t *odd, int32_t o)
{
    return (o & 1) ? odd + (o - 1) : even + o;
}

#define CAND_MAX 464                    /* decimated candidate region (<= 202 lags + 256) */
#define FINE_REGION (FINE_LEN + 2 * DECIM + 2)

/* Coarse search on the decimated signal keeping the best 3 candidates, each refined at
   full rate. Returns the jump; *cf = its full-band correlation, *cc = its coarse one.
   buf: int16 scratch for 2 * FINE_REGION + FINE_LEN samples. */
static int32_t best_jump(const TsxStretcher *s, const TsxState *st, int nch, int32_t pos,
                         int32_t j_lo, int32_t j_hi, float *cf, float *cc, int16_t *buf)
{
    const int32_t ld = CORR_LEN / DECIM;
    int32_t pd = pos / DECIM - ld / 2;  /* coarse windows are centred on the splice points */
    int32_t jd_lo = floordiv(j_lo + DECIM - 1, DECIM), jd_hi = floordiv(j_hi, DECIM);
    float cv[3] = {-2.0f, -2.0f, -2.0f};
    int32_t cj[3] = {0, 0, 0};

    if (jd_lo <= jd_hi) {
        int32_t nc = jd_hi - jd_lo + ld;
        if (nc > CAND_MAX) {            /* can't happen with SEARCH 800; stay in bounds */
            nc = CAND_MAX;
            jd_hi = jd_lo + nc - ld;
        }
        int16_t *dref = buf, *dce = buf + ld, *dco = dce + CAND_MAX;
        copy_dec(s, st, nch, pd, ld, dref);
        copy_dec(s, st, nch, pd + jd_lo, nc, dce);
        shift_copy(dce, dco, nc);
        float saa = i64f(dot16(dref, dref, ld)) * I16_SCALE;
        int64_t sbb = dot16(dce, dce, ld);
        for (int32_t jd = jd_lo; jd <= jd_hi; jd++) {
            int32_t o = jd - jd_lo;
            if (o > 0)                  /* slide the energy of the candidate window */
                sbb += (int32_t)dce[o + ld - 1] * dce[o + ld - 1] - (int32_t)dce[o - 1] * dce[o - 1];
            float sab = i64f(dot16(dref, window(dce, dco, o), ld)) * I16_SCALE;
            float c = ncc_finish(sab, saa, i64f(sbb) * I16_SCALE, ld);
            int32_t j = jd * DECIM;
            int near = -1;
            for (int q = 0; q < 3; q++)
                if (cv[q] > -2.0f && (cj[q] - j <= 2 * DECIM && j - cj[q] <= 2 * DECIM))
                    near = q;
            if (near >= 0) {
                if (c > cv[near]) {
                    cv[near] = c;
                    cj[near] = j;
                }
                continue;
            }
            int w = 0;
            for (int q = 1; q < 3; q++)
                if (cv[q] < cv[w])
                    w = q;
            if (c > cv[w]) {
                cv[w] = c;
                cj[w] = j;
            }
        }
    }

    /* fine: full rate around each candidate */
    int16_t *ref = buf, *ce = buf + FINE_LEN, *co = ce + FINE_REGION;
    copy_mono(s, st, nch, pos, FINE_LEN, ref);
    float saa = i64f(dot16(ref, ref, FINE_LEN)) * I16_SCALE;
    float best = -2.0f, fc = -2.0f;
    int32_t fb = j_lo;
    for (int q = 0; q < 3; q++) {
        if (cv[q] <= -2.0f)
            continue;
        int32_t lo = imax(j_lo, cj[q] - DECIM), hi = imin(j_hi, cj[q] + DECIM);
        if (hi < lo)
            continue;
        int32_t nr = hi - lo + FINE_LEN;
        copy_mono(s, st, nch, pos + lo, nr, ce);
        shift_copy(ce, co, nr);
        int64_t sbb = dot16(ce, ce, FINE_LEN);
        for (int32_t j = lo; j <= hi; j++) {
            int32_t o = j - lo;
            if (o > 0)
                sbb += (int32_t)ce[o + FINE_LEN - 1] * ce[o + FINE_LEN - 1]
                     - (int32_t)ce[o - 1] * ce[o - 1];
            float sab = i64f(dot16(ref, window(ce, co, o), FINE_LEN)) * I16_SCALE;
            float c = ncc_finish(sab, saa, i64f(sbb) * I16_SCALE, FINE_LEN);
            if (c > best) {
                best = c;
                fb = j;
                fc = cv[q];
            }
        }
    }
    *cf = best;
    *cc = fc;
    return fb;
}

/* ---- the stretcher (tsplice._run) ---- */

static void init(const TsxStretcher *s, TsxState *st)
{
    st->pos = st->pos2 = st->ideal = 0.0;
    st->t = st->dt = st->corr = 0.0f;
    st->rej_drift = 1e30f;
    st->holdoff = 0;
    st->base = (int32_t)s->ring_l.read_idx;
    st->abs_rd = 0;
    st->pre_skip = -1;
    st->frames = 0;
    st->last_on_frame = -100000;
    st->prev_m = 0.0f;
    st->fading = 0;
    st->splice = 0;
    st->on_count = 0;
    st->oi = 0;
    for (int i = 0; i < 32; i++)
        st->ehist[i] = 0.0f;
    st->marker = TSX_MARKER;
}

static inline int16_t to_i16(float v)
{
    v += v >= 0.0f ? 0.5f : -0.5f;
    if (v > 32767.0f)
        return 32767;
    if (v < -32767.0f)
        return -32767;
    return (int16_t)(int32_t)v;
}

int tsx_run(TsxStretcher *s, TsxState *st, int16_t *out_l, int16_t *out_r, int n,
            int32_t *pos_out, int nch, float *scratch)
{
    TsxRing *rl = &s->ring_l, *rr = &s->ring_r;
    /* a new voice (Stretch_Init set the marker to -1), or rings reset behind our back */
    if (st->marker != TSX_MARKER || rl->read_idx != ridx(st, st->abs_rd))
        init(s, st);
    int32_t abs_write = st->abs_rd
        + (int32_t)((rl->write_idx + TSX_RING - rl->read_idx) % TSX_RING);
    {
        PROF_BEGIN;
        analyse(s, st, nch, abs_write);
        PROF_END(0);
    }

    const double p = (double)s->rate_q14 * (1.0 / 16384.0);
    const double r = (double)s->ratio;
    const float pf = (float)p;
    const int whole = p == 1.0;
    const int half = whole ? 0 : 1;             /* samples the interpolation reads past pos */
    int16_t *search_buf = (int16_t *)scratch;
    const int32_t margin = half + 2;
    /* The writer refuses only to overwrite what lies between the reported read index and
       itself, and it doesn't run during this call: the ring holds the last TSX_RING
       samples written, all valid now. */
    const int32_t lo_valid = imax(abs_write - TSX_RING + 1, 0);
    int stopped = 0, k;

    for (k = 0; k < n; k++) {
        if ((int32_t)st->pos + margin >= abs_write
                || (st->fading && (int32_t)st->pos2 + margin >= abs_write)) {
            stopped = 1;
            break;
        }
        /* ---- output ---- */
        PROF_BEGIN;
        float al, ar;
        tap(s, st, nch, st->pos, whole, &al, &ar);
        if (st->fading) {
            float a = 1.0f - st->t, b = st->t, bl, br;
            float nn = a * a + b * b + 2.0f * st->corr * a * b;
            float g = 1.0f / fsqrt(nn > 1e-9f ? nn : 1e-9f);
            tap(s, st, nch, st->pos2, whole, &bl, &br);
            al = (a * al + b * bl) * g;
            ar = (a * ar + b * br) * g;
            st->t += st->dt;
            st->pos2 += p;
        }
        out_l[k] = to_i16(al);
        if (nch == 2)
            out_r[k] = to_i16(ar);
        PROF_END(2);
        st->pos += p;
        st->ideal += r;
        if (st->fading && st->t >= 1.0f) {
            st->fading = 0;
            st->pos = st->pos2;
        }
        if (st->holdoff > 0)
            st->holdoff--;
        if (st->fading || st->holdoff > 0)
            continue;
#ifdef TSX_PROFILE
        tsx_prof[5]++;                  /* planning passes */
#endif

        /* ---- splice planning ---- */
        float drift = (float)(st->pos - st->ideal);
        float ad = drift < 0.0f ? -drift : drift;
        if (ad < THR * 0.25f)
            continue;
        int32_t ip = (int32_t)st->pos;
        while (st->oi < st->on_count && st->on_pos[st->oi & (TSX_ONSETS - 1)] < ip)
            st->oi++;
        int32_t oi = st->oi;
        int32_t next_on = oi < st->on_count ? st->on_pos[oi & (TSX_ONSETS - 1)] : FAR;
        /* the common case, decided early: drift below the threshold and no attack near
           (so no catch-up either) */
        if (ad <= THR && next_on - ip >= (int32_t)(XFADE * pf) + GUARD_PRE + 256)
            continue;
#ifdef TSX_PROFILE
        tsx_prof[6]++;                  /* passes beyond the early exit */
#endif
        int32_t last_on = -FAR, last_zone = -FAR;
        if (oi > 0 && oi - 1 >= st->on_count - TSX_ONSETS) {
            int q = (oi - 1) & (TSX_ONSETS - 1);
            last_on = st->on_pos[q];
            last_zone = st->on_zone[q] == OPEN ? OPEN : imax(st->on_zone[q], last_on + GUARD_POST);
        }
        int in_attack = ip < last_zone;
        /* source ahead of a jump target that the fade and the correlation windows cover */
        int32_t span = imax(imax((int32_t)(XFADE * pf), CORR_LEN / 2), FINE_LEN) + 16;
        int pre_onset = next_on - ip < (int32_t)(XFADE * pf) + GUARD_PRE + 256 && !in_attack
            && st->pre_skip != oi;
        /* Nothing to do now? Then nothing can change before the drift (moving by p - r per
           sample) reaches the retry distance or the hard limit, the catch-up window of the
           next attack opens, or the current attack zone ends: sleep until the first of those
           (the same decisions as checking every sample, at a fraction of the cost). */
        float dd = drift - st->rej_drift;
        float add = dd < 0.0f ? -dd : dd;
        int idle = (drift * (pf - (float)r) < 0.0f && ad < HARD && !pre_onset)     /* overshoot heals */
            || !((ad > THR && !in_attack) || (pre_onset && ad > THR * 0.25f) || ad > HARD)
            || (add < RETRY && !pre_onset && ad < HARD);
        if (idle) {
            float rate = pf - (float)r, sleep = 256.0f;
            rate = rate < 0.0f ? -rate : rate;
            if (!pre_onset) {
                float t = (float)(next_on - ip - ((int32_t)(XFADE * pf) + GUARD_PRE + 256)) / pf;
                sleep = t < sleep ? t : sleep;
            }
            if (in_attack && last_zone != OPEN) {
                float t = (float)(last_zone - ip) / pf;
                sleep = t < sleep ? t : sleep;
            }
            if (rate > 1e-6f) {
                float t = (HARD - ad) / rate;
                sleep = t < sleep ? t : sleep;
                if (add < RETRY) {
                    t = (RETRY - add) / rate;
                    sleep = t < sleep ? t : sleep;
                }
                t = (THR - ad) / rate;  /* crossing THR changes the main condition */
                if (t > 0.0f && t < sleep)
                    sleep = t;
            }
            st->holdoff = sleep > 1.0f ? (int32_t)sleep - 1 : 0;
            continue;
        }
        int32_t lookahead = imin(LOOKAHEAD, abs_write - ip - margin);
        int32_t history = imin(HISTORY, ip - lo_valid - margin - DECIM) - CORR_LEN / 2;
        int32_t j0 = (int32_t)(-drift);
        int snap = 0;
        int32_t L = XFADE, j_lo, j_hi, bj;
        float c, cf, cc;
        int32_t room = next_on - GUARD_PRE - ip;    /* the outgoing tap must not reach it */
        if ((float)room < L * pf)
            L = (int32_t)((float)room / pf);
        if (j0 > 0) {               /* forward (skip): never skip an attack, land on it */
            j_lo = imax(imax(j0 - SEARCH, 32), j0 / 2);
            j_hi = imin(imin(j0 + SEARCH, lookahead - span), j0 * 19 / 10);
            j_lo = imin(j_lo, j_hi);
            int32_t j_snap = next_on - GUARD_PRE - ip;
            if (next_on - ip <= j_hi + span) {
                if (32 <= j_snap && j_snap <= j0 + THR && (float)room >= XFADE_ONSET * pf) {
                    snap = 1;
                    bj = j_snap;
                    c = 0.0f;
                    L = XFADE_ONSET;
                } else {
                    j_hi = imin(j_hi, j_snap - (int32_t)(L * pf));
                }
            }
            if (!snap) {
                if (j_hi < j_lo || L < 32) {
                    COUNT(0);
                    st->holdoff = 32;
                    continue;
                }
                {
                    PROF_BEGIN;
                    {
                PROF_BEGIN;
                bj = best_jump(s, st, nch, ip, j_lo, j_hi, &cf, &cc, search_buf);
                PROF_END(3);
            }
                    PROF_END(3);
                }
                c = cf < cc ? cf : cc;
            }
        } else {                    /* backward (repeat): not before the last attack zone */
            j_lo = imax(imax(j0 - SEARCH, -history), -((-j0) * 19 / 10));
            j_hi = imin(imin(j0 + SEARCH, -32), -((-j0) / 2));
            j_hi = imax(j_hi, j_lo);
            if (ip + j_lo < last_zone && last_zone != OPEN)
                j_lo = last_zone - ip;
            else if (last_zone == OPEN)
                j_lo = j_hi + 1;    /* zone still open: nothing allowed */
            if (j_hi < j_lo || L < 32) {
                if (ad > HARD && L >= 32) {
                    j_lo = imax(j0 - SEARCH, -history);
                    j_hi = imin(j0 + SEARCH, -32);
                } else {
                    COUNT(1);
                    st->holdoff = 32;
                    continue;
                }
            }
            {
                PROF_BEGIN;
                bj = best_jump(s, st, nch, ip, j_lo, j_hi, &cf, &cc, search_buf);
                PROF_END(3);
            }
            c = cf < cc ? cf : cc;
        }
        if (!snap && ad < HARD) {   /* demand a good match while there's time */
            float c_req = pre_onset ? C_PRE : C_MIN * (HARD - ad) / (HARD - 2 * THR);
            if (c_req > C_MIN && !pre_onset)
                c_req = C_MIN;
            if (c < c_req) {
                COUNT(2);
                if (pre_onset)
                    st->pre_skip = oi;
                else
                    st->rej_drift = drift;
                continue;
            }
        }
        if (!snap) {                /* poor match: longer fade, still before the attack */
            float cl = c > 1.0f ? 1.0f : (c < 0.0f ? 0.0f : c);
            L = (int32_t)(XFADE + (XFADE_MAX - XFADE) * (1.0f - cl));
            if ((float)room < L * pf)
                L = imax((int32_t)((float)room / pf), 32);
            if ((float)bj + L * pf > (float)lookahead)
                L = imax((int32_t)((float)(lookahead - bj) / pf), 32);
        }
        LOG(k, bj, c, drift, snap ? 1 : (pre_onset ? 2 : (ad >= HARD ? 3 : 0)), st->pos);
        st->fading = 1;
        st->pos2 = st->pos + bj;
        st->t = 0.0f;
        st->dt = 1.0f / (float)L;
        if (snap) {
            st->corr = 0.0f;
        } else {
            int32_t len = imax((int32_t)(L * pf), 1);
            PROF_BEGIN;
            float cr = ncc_mono(s, st, nch, ip, ip + bj, len);
            PROF_END(4);
            st->corr = cr > 1.0f ? 1.0f : (cr < 0.0f ? 0.0f : cr);
        }
        st->holdoff = 0;
    }

    /* report the read position just behind our lowest tap: the writer then fills ahead of
       us, and what it hasn't overwritten yet stays usable as history */
    double lowest = st->fading && st->pos2 < st->pos ? st->pos2 : st->pos;
    int32_t new_rd = imin(imax(st->abs_rd, (int32_t)lowest - half - 16), abs_write - 1);
    int32_t delta = new_rd - st->abs_rd;
    uint32_t idx = ridx(st, new_rd);
    rl->read_idx = idx;
    rl->moved += delta;
    rl->read_block = idx >> 7;
    if (nch == 2) {
        rr->read_idx = idx;
        rr->moved += delta;
        rr->read_block = idx >> 7;
    }
    st->abs_rd = new_rd;
    st->splice = st->fading;
    *pos_out = stopped ? -1 : rl->block_pos[ridx(st, (int32_t)st->pos) >> 11];
    return k;
}

/* ---- hooks for Roland's Resampler_Stereo / Resampler_Mono thunks ---- */

#ifndef TSX_HOST
#include "imports.h"

typedef int (*stereo_fn)(TsxStretcher *, int16_t *, int16_t *, int, int32_t *);
typedef int (*mono_fn)(TsxStretcher *, int16_t *, int, int32_t *);
#define ROLAND_STEREO ((stereo_fn)IMP(Resampler_Stereo))
#define ROLAND_MONO ((mono_fn)IMP(Resampler_Mono))

/* Voice_RenderSetup renders into one of two OCRAM buffer sets; the set's two int32
   conversion buffers (8 KB each, adjacent) are free while the resampler runs. */
static float *scratch_for(const int16_t *out_l)
{
    if ((uintptr_t)out_l == IMP(g_render_out_1))
        return (float *)IMP(g_render_scratch_1);
    if ((uintptr_t)out_l == IMP(g_render_out_0))
        return (float *)IMP(g_render_scratch_0);
    return 0;
}

int tsx_resampler_stereo(TsxStretcher *s, int16_t *out_l, int16_t *out_r, int n,
                         int32_t *pos_out)
{
    float *scratch = scratch_for(out_l);
    if (s->mode != TSX_MODE || !scratch || !s->state) {
        vari_leave_tsx(s);
        return ROLAND_STEREO(s, out_l, out_r, n, pos_out);
    }
    return tsx_run(s, (TsxState *)(uintptr_t)s->state, out_l, out_r, n, pos_out, 2, scratch);
}

int tsx_resampler_mono(TsxStretcher *s, int16_t *out, int n, int32_t *pos_out)
{
    float *scratch = scratch_for(out);
    if (s->mode != TSX_MODE || !scratch || !s->state) {
        vari_leave_tsx(s);
        return ROLAND_MONO(s, out, n, pos_out);
    }
    return tsx_run(s, (TsxState *)(uintptr_t)s->state, out, 0, n, pos_out, 1, scratch);
}
#endif

_Static_assert(offsetof(TsxRing, write_idx) == 0x14, "ring layout");
_Static_assert(offsetof(TsxRing, read_idx) == 0x38, "ring layout");
_Static_assert(offsetof(TsxRing, moved) == 0x48, "ring layout");
_Static_assert(sizeof(TsxRing) == 0xA060, "ring size");
_Static_assert(offsetof(TsxStretcher, mode) == 0x1C, "stretcher layout");
_Static_assert(offsetof(TsxStretcher, ring_l) == 0x40, "stretcher layout");
_Static_assert(offsetof(TsxStretcher, ring_r) == 0xA0A0, "stretcher layout");
_Static_assert(offsetof(TsxStretcher, state) == 0x14100, "stretcher layout");
_Static_assert(offsetof(TsxStretcher, ratio) == 0x14104, "stretcher layout");
_Static_assert(offsetof(TsxState, splice) == 0x48, "state layout");
_Static_assert(offsetof(TsxState, marker) == 0x4C, "state layout");
_Static_assert(offsetof(TsxState, _period) == 0x54, "state layout");
_Static_assert(offsetof(TsxState, _mod_depth) == 0x2C8, "state layout");
_Static_assert(sizeof(TsxState) == 0x328, "state size");
_Static_assert((FINE_LEN + 2 * FINE_REGION) * 2 <= TSX_SCRATCH_FLOATS * 4,
               "scratch: fine search buffers");
_Static_assert((CORR_LEN / DECIM + 2 * CAND_MAX) * 2 <= TSX_SCRATCH_FLOATS * 4,
               "scratch: coarse search buffers");
