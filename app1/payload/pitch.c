/* Pitch of the SP's mix, for the tuner (docs/app1/topics/tuner.md, patch "tuner").
 *
 * Capture: Audio_RxHandler (ITCM 0x1CA80) runs on the audio task for every received
 * half-buffer: 64 frames of 8 words at 48 kHz, each word a stereo pair (left in the high
 * half). Word 0 is the finished mix, input FX included (measured with the probe). The
 * patch jumps from its first instructions to hook_audio_rx, which adds L + R of word 0,
 * averages pairs of frames (24 kHz) into a ring, and continues in Roland's code
 * (audio_rx_orig, hooks.S).
 *
 * Detection, on the UI task: YIN (de Cheveigne & Kawahara 2002) at 12 kHz, stopping at the
 * first clear dip, then the exact period from the plain difference function at 24 kHz
 * around it. The working buffers come from Roland's heap while capturing: PITCH_RAM was
 * non-cacheable until 2026-10-28, and the inner loops there were the tuner's main cost.
 */
#include <stdint.h>

#include "imports.h"
#include "pitch.h"
#include "ram.h"

#define REG(a) (*(volatile uint32_t *)(a))

#define op_new ((void *(*)(uint32_t))IMP(operator_new))
#define op_delete ((void (*)(void *))IMP(operator_delete))

int audio_rx_orig(const volatile int32_t *half, int bytes);    /* hooks.S */

#define CAPTURE REG(PITCH_RAM + 0x00)
#define CAPTURE_ON 0x50495443u          /* "PITC"; anything else (RAM garbage at boot) = off */
#define POS REG(PITCH_RAM + 0x04)       /* samples written so far (wraps) */
#define BUF REG(PITCH_RAM + 0x08)       /* heap block for the buffers below, or 0 */
#define LAST_POWER (*(volatile float *)(PITCH_RAM + 0x0C))     /* newest 21 ms, last call */
#define ONSET_LEFT REG(PITCH_RAM + 0x10)        /* calls still skipped after an attack */
#define RING ((volatile int16_t *)(PITCH_RAM + 0x100))
#define RING_SIZE 2048                  /* 85 ms */
#define FS 24000.0f

/* fine: 24 kHz; coarse: 12 kHz (pairs averaged) */
#define W 1024                          /* window, 43 ms */
#define TAU_MAX 600                     /* 40 Hz */
#define N (W + TAU_MAX)
#define CW (W / 2)
#define CTAU_MIN 5                      /* 2400 Hz */
#define CTAU_MAX (TAU_MAX / 2)
#define BUF_FLOATS (N + N / 2 + CTAU_MAX)

#define THRESHOLD 0.15f                 /* YIN's absolute threshold */
#define CONFIDENT 0.2f                  /* no dip under THRESHOLD: accept the minimum below this */
#define MIN_POWER 400.0f                /* mean square (16-bit units): about -38 dBFS */
#define ONSET_RATIO 4.0f                /* newest 21 ms 6 dB louder than at the last call */
#define ONSET_CALLS 2                   /* then skip 100 ms: the attack isn't periodic */
#define RECENT 512                      /* samples for the onset level */

int hook_audio_rx(const volatile int32_t *half, int bytes)
{
    if (CAPTURE == CAPTURE_ON) {
        uint32_t pos = POS;
        for (int f = 0; f < 64; f += 2) {
            int32_t a = half[f * 8], b = half[(f + 1) * 8];
            int32_t s = (int16_t)(a >> 16) + (int16_t)a + (int16_t)(b >> 16) + (int16_t)b;
            RING[pos++ % RING_SIZE] = (int16_t)(s >> 2);
        }
        POS = pos;
    }
    return audio_rx_orig(half, bytes);
}

void pitch_capture(int on)
{
    if (on && CAPTURE != CAPTURE_ON) {
        POS = 0;
        for (int i = 0; i < RING_SIZE; i++)
            RING[i] = 0;
        BUF = (uint32_t)op_new(BUF_FLOATS * sizeof(float));
        LAST_POWER = 0;
        ONSET_LEFT = 0;
        CAPTURE = CAPTURE_ON;
    } else if (!on && CAPTURE == CAPTURE_ON) {
        CAPTURE = 0;
        if (BUF)
            op_delete((void *)BUF);
        BUF = 0;
    }
}

static float sq_diff(const float *x, int tau, int n)       /* sum (x[j] - x[j + tau])^2 */
{
    float acc = 0;
    const float *y = x + tau;
    for (int j = 0; j < n; j++) {
        float diff = x[j] - y[j];
        acc += diff * diff;
    }
    return acc;
}

/* YIN on the coarse signal: the period in coarse samples, or -1 if not clearly periodic. */
static int coarse_period(const float *c, float *d)
{
    float sum = 0;
    int best = -1;
    d[0] = 1;
    for (int tau = 1; tau < CTAU_MAX; tau++) {
        float acc = sq_diff(c, tau, CW);
        sum += acc;
        d[tau] = sum > 0 ? acc * tau / sum : 1;
        if (tau <= CTAU_MIN)
            continue;
        if (best < 0 && d[tau] < THRESHOLD)
            best = tau;                 /* first dip: follow it down */
        else if (best >= 0) {
            if (d[tau] < d[best])
                best = tau;
            else
                return best;            /* past its minimum: done, the rest isn't needed */
        }
    }
    if (best >= 0)
        return best < CTAU_MAX - 1 ? best : -1;
    best = CTAU_MIN + 1;                /* no dip under the threshold: the deepest, if clear */
    for (int tau = CTAU_MIN + 2; tau < CTAU_MAX - 1; tau++)
        if (d[tau] < d[best])
            best = tau;
    return d[best] < CONFIDENT ? best : -1;
}

int pitch_detect(float *hz)
{
    if (CAPTURE != CAPTURE_ON || !BUF)
        return PITCH_NONE;
    float *x = (float *)BUF, *c = x + N, *d = c + N / 2;

    /* the newest N samples; the audio task adds a few dozen while we work, the ring has room */
    uint32_t start = POS - N;
    for (int i = 0; i < N; i++)
        x[i] = RING[(start + i) % RING_SIZE];

    float power = 0, recent = 0;
    for (int i = 0; i < W; i++)
        power += x[i] * x[i];
    for (int i = N - RECENT; i < N; i++)
        recent += x[i] * x[i];
    float last = LAST_POWER;
    LAST_POWER = recent;
    if (recent > ONSET_RATIO * last && recent > MIN_POWER * RECENT)
        ONSET_LEFT = ONSET_CALLS;
    if (ONSET_LEFT) {
        ONSET_LEFT--;
        return PITCH_UNSURE;
    }
    if (power < MIN_POWER * W)
        return PITCH_NONE;

    for (int i = 0; i < N / 2; i++)
        c[i] = (x[2 * i] + x[2 * i + 1]) * 0.5f;
    int ct = coarse_period(c, d);
    if (ct < 0)
        return PITCH_UNSURE;

    /* exact period: the minimum of the plain difference function near 2 * ct, interpolated */
    int lo = 2 * ct - 2, hi = 2 * ct + 2, best = lo;
    float v[5];
    for (int t = lo; t <= hi; t++) {
        v[t - lo] = sq_diff(x, t, W);
        if (v[t - lo] < v[best - lo])
            best = t;
    }
    if (best == lo || best == hi)       /* edge: widen once */
        return PITCH_UNSURE;
    float a = v[best - 1 - lo], b = v[best - lo], e = v[best + 1 - lo], den = a - 2 * b + e;
    float shift = den > 0 ? (a - e) / (2 * den) : 0;
    *hz = FS / (best + shift);
    return PITCH_OK;
}
