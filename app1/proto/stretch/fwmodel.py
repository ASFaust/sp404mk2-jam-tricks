"""Model of the SP-404MKII v5.52 time stretcher (ITCM Resampler_Process, 0x12DC0).

Reproduces the firmware's integer arithmetic and control flow for VARI modes
0 (Off), 1 (Backing) and 2 (Ensemble); see docs/app1/topics/time-stretch.md.
Simplifications, none of which change the sound in normal playback:
  - the 0x2800-sample source rings are modelled as one linear array with a moving
    write position (wrap-around handling is left out);
  - the ratio modulation (pad params 0x8A..0x8C) is off;
  - Voice_RenderSetup's refill is modelled as "before each 0x800-sample output block,
    add 0x800-sample chunks until 40 % (Ensemble: 60 %) of the ring is ahead".

    out = stretch_fw(x, semis=0.0, ratio=1.0, mode=0)
x and out are int16 arrays of shape (n, channels).
"""
import numpy as np
from numba import njit

RING = 0x2800
BLOCK = 0x800
ONE_Q28 = 0x10000000


@njit(cache=True)
def _sat16(v):
    """Ring sample (int16 << 13) back to int16, as the output stage does."""
    if v >= ONE_Q28:
        return 0x7FFF
    if v <= -ONE_Q28:
        return -0x7FFF
    return v >> 13


@njit(cache=True)
def _tap(src, ch, i, frac_q12, write):
    """Linear interpolation between ring[i] and ring[i+1] (0 beyond the writer)."""
    if i < 0 or i >= write:
        return 0
    cur = np.int64(src[i, ch]) << 13
    if frac_q12 == 0:
        return cur
    nxt = np.int64(src[i + 1, ch]) << 13 if i + 1 < write else 0
    return ((nxt - cur + 0x800) >> 12) * frac_q12 + cur


@njit(cache=True)
def _period_from_lag(lag, default_period):
    """Stretch_PeriodFromLag (0x13D9A)."""
    if lag < 0:
        return default_period
    if lag == 0:
        lag = 600
    if lag >= 600:
        return lag
    p = lag
    while p < 600:
        if p > 299:
            return p << 1
        if p > 0x95:
            return p << 2
        if p > 0x4A:
            return p << 3
        p = p << 4
    return p


@njit(cache=True)
def _fallback_period(p, default_period):
    """Stretch_FallbackPeriod (0x13D82)."""
    if p < 0:
        return default_period
    return p >> 1 if p > default_period else p


@njit(cache=True)
def _run(src, rate_q14, ratio_inc, mode, out):
    n_src, nch = src.shape
    default_period = 600
    # StretchState after Stretch_Init + the period set by SmplCharge_Init
    period = 600 if mode == 0 else (400 if mode == 1 else 800)
    frac = 0
    holdoff = 0
    xfade_left = 0
    xfade_step = 0x6D3A0
    splice = 0
    jump = 1
    gain_out = 0
    gain_in = 0
    detected = -1
    corr_k = -1
    best_lag = 0
    best_corr = 0
    corr_anchor = 800
    guard_env = 0
    guard_hold = 0
    guard_count = 0
    read = 0
    ideal = 0          # Q14
    write = 0
    fill_pct = 60 if mode == 2 else 40
    n_out = 0
    n_max = out.shape[0]

    while n_out < n_max:
        # Voice_RenderSetup: top the source ring up
        while (write - read) * 100 // RING < fill_pct and write < n_src \
                and write - read + BLOCK <= RING:
            write = min(write + BLOCK, n_src)
        for j in range(BLOCK):
            if n_out >= n_max:
                return n_out
            ideal += ratio_inc
            frac += rate_q14
            step = frac >> 14
            if read + step >= write and step > 0:      # Ring_Advance refuses
                return n_out
            read += step
            if mode > 0:                                # Stretch_TransientGuard
                gi = read + 2000
                if gi < write:
                    v = np.int64(src[gi, 0]) << 13
                    if -ONE_Q28 < v < ONE_Q28:
                        lvl = abs(v >> 13)
                        rising = lvl > guard_env
                    else:
                        lvl = 0x7FFF
                        rising = guard_env <= 0x7FFE
                    if rising:
                        guard_env = lvl
                        guard_count = 0
                    elif guard_count < 0:
                        if guard_hold < 1:
                            guard_env = guard_env * 0x26AC // 10000
                    else:
                        c = guard_count
                        guard_count += 1
                        guard_env = guard_env * 9999 // 10000
                        if c > 0x3E6 and guard_hold < 1:
                            holdoff = holdoff + 2000 if splice != 0 else 2000
                            guard_env = guard_env * 0x65 // 100
                            guard_hold = 3000
                            guard_count = -1
                    if guard_hold >= 0:
                        guard_hold -= 1
            frac &= 0x3FFF
            fq = ((frac << 14) + 0x8000) >> 16
            if holdoff >= 0:
                holdoff -= 1

            # ---- output sample ----
            for ch in range(nch):
                a = _tap(src, ch, read, fq, write)
                if splice == 0:
                    acc = a
                else:
                    b_idx = read + period if splice == 1 else read - jump * period
                    b = _tap(src, ch, b_idx, fq, write)
                    go = (gain_out + 0x8000) >> 16
                    gn = (gain_in + 0x8000) >> 16
                    acc = go * ((a + 0x800) >> 12) + gn * ((b + 0x800) >> 12)
                out[n_out, ch] = _sat16(acc)

            # ---- splice bookkeeping ----
            decide = False
            if splice != 0:
                gain_out -= xfade_step
                gain_in += xfade_step
                old = xfade_left
                xfade_left -= 1
                if old < 1:
                    gain_out = ONE_Q28
                    gain_in = 0
                    xfade_left = period - 1
                    xfade_step = ONE_Q28 // (period - 1)
                    i_ideal = ideal >> 14
                    if splice == 1:
                        read += period
                        if read >= write:
                            return n_out
                        if read + period < i_ideal:
                            jump = 2 if 2 * period < i_ideal - (read + period) else 1
                            if read + 2 * period >= write:
                                period = _fallback_period(detected, default_period)
                                xfade_left = period - 1
                                xfade_step = ONE_Q28 // (period - 1)
                        else:
                            splice = 0
                            jump = 1
                            decide = holdoff < 0
                    else:
                        read -= jump * period
                        if read < 0:
                            return n_out
                        if i_ideal < read - period:
                            jump = 2 if 2 * period < read - i_ideal else 1
                        else:
                            splice = 0
                            jump = 1
                            decide = holdoff < 0
            else:
                decide = holdoff < 0

            if decide:
                i_ideal = ideal >> 14
                if read + default_period < i_ideal:
                    if mode == 2:
                        period = _period_from_lag(detected, default_period)
                    if read + 2 * period >= write:
                        period = _fallback_period(detected, default_period)
                    gain_out = ONE_Q28
                    gain_in = 0
                    xfade_left = period - 1
                    xfade_step = ONE_Q28 // (period - 1)
                    splice = 1
                    holdoff = 0x400
                    jump = 1
                    if read + period >= write:
                        return n_out
                elif i_ideal < read - period:
                    if mode == 2:
                        period = _period_from_lag(detected, default_period)
                    xfade_left = period - 1
                    gain_out = ONE_Q28
                    gain_in = 0
                    xfade_step = ONE_Q28 // (period - 1)
                    splice = 2
                    holdoff = 0x400
                    jump = 1

            # ---- Ensemble: one correlation lag every 35 (25) output samples ----
            every = 0x19 if detected == -1 else 0x23
            if mode == 2 and j % every == 0:
                k = corr_k
                if k > 100 or k == 100:
                    corr_k = 0
                    corr_anchor = read + 0x4B0
                else:
                    corr_k = k + 1
                k = corr_k
                if k == 100:
                    detected = best_lag if best_lag >= 400 else best_lag * 2
                elif k == 0:
                    best_corr = 0
                else:
                    lag = k * 5
                    acc = 0
                    for i in range(1, 400, 2):
                        ia = corr_anchor + i
                        ib = corr_anchor + lag + i + 400
                        a = _sat16(np.int64(src[ia, 0]) << 13) >> 7 if ia < n_src else 0
                        b = _sat16(np.int64(src[ib, 0]) << 13) >> 7 if ib < n_src else 0
                        acc += a * b
                    if acc > best_corr:
                        best_corr = acc
                        best_lag = lag + 400
            n_out += 1
    return n_out


def rate_q14(semis):
    r = 2.0 ** (semis / 12.0)
    return (int(r) << 14) | (int((r - int(r)) * 16384.0) & 0x3FFF)


def stretch_fw(x, semis=0.0, ratio=1.0, mode=0, max_len=None):
    """Firmware-style pitch/time processing of int16 audio x (n, ch).

    ratio: time ratio as the firmware uses it (SPEED/10000, or project/sample BPM);
    output length ~ n / ratio. semis: pitch in semitones. mode: VARI 0/1/2.
    """
    x = np.ascontiguousarray(x, dtype=np.int16)
    if x.ndim == 1:
        x = x[:, None]
    ratio_inc = int(np.float32(ratio) * np.float32(16384.0))
    n = max_len or int(len(x) / ratio) + BLOCK
    out = np.zeros((n, x.shape[1]), dtype=np.int16)
    got = _run(x, rate_q14(semis), ratio_inc, mode, out)
    return out[:got]
