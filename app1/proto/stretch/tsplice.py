"""New stretcher prototype: transient-aware WSOLA splicing ("tsplice").

Same architecture as the firmware (docs/app1/topics/time-stretch.md): one read tap moving at
the pitch rate p, an ideal position moving at the time ratio r, and a splice (crossfade
to a second tap) whenever the two drift apart. What changes:

  1. WSOLA: the jump is searched in +-search around the drift-correcting jump, for the
     highest normalised cross-correlation of the audio about to be played (L+R).
  2. Crossfade gains normalised for the measured correlation (constant level whether the
     two segments match or not).
  3. Transients, from an onset map (energy of the first difference in 64-sample frames):
     no splices just after an attack; catch up just before one; forward jumps never skip an
     attack (they land on it); backward jumps never replay one.
  4. Interpolation: windowed sinc, cutoff lowered to 1/p when pitching up (anti-aliasing).

Look-ahead and history are limited to what the firmware's 0x2800-sample ring can hold.

    y = stretch_ts(x, semis, ratio, **params)       # int16 (n, ch) in and out
"""
import numpy as np
from numba import njit

DEFAULTS = dict(
    thr=480,          # drift (source samples) that triggers a splice
    hard=2400,        # drift that forces a splice even inside an attack (50 ms)
    c_min=0.95,       # correlation required at drift thr, relaxing to 0 at hard
    c_pre=0.0,        # correlation required for a catch-up splice just before an attack
    retry=512,        # after a rejected search, wait until the drift moved this much
    xfade=480,        # crossfade length at a perfect match, output samples
    xfade_max=1920,   # ... growing to this at correlation 0
    xfade_onset=96,   # crossfade when landing on an attack
    search=800,       # +- WSOLA search around the nominal jump (half the longest period, ~30 Hz)
    corr_len=2048,    # coarse correlation window, centred (about 1.5 periods of 30 Hz bass)
    fine_len=512,     # fine (full-rate) correlation window
    decim=8,          # coarse search on an 8x decimated copy
    guard_pre=64,     # land this many samples before an onset
    guard_post=1200,  # attack zone: at least this long after an onset (source samples)
    zone_db=8.0,      # ... until the 10 ms energy fell this far below its peak
    zone_max=7200,    # ... but at most 150 ms
    onset_ratio=2.0,  # frame energy rise that counts as an onset (x max of the last 32 ms)
    onset_floor=1e-6, # minimum frame energy (normalised) for an onset
    lookahead=6144,   # source samples the ring holds ahead of the reader (60 % fill)
    history=4096,     # source samples the ring holds behind the reader
    taps=6,           # sinc half-width at p <= 1 (scaled by p above): 12-tap kernel
    aa=0.92,          # interpolation cutoff (x Nyquist, / p when pitching up)
    interp=3,         # INTERP_LINEAR (as the C; listening test in interp.py), INTERP_SINC
                      # (taps/aa above), INTERP_FIXED (sinc, 2*taps taps), INTERP_HERMITE
)

INTERP_SINC, INTERP_FIXED, INTERP_HERMITE, INTERP_LINEAR = 0, 1, 2, 3

FRAME = 64
ONSET_REF = 24     # frames of history an attack must rise above


@njit(cache=True)
def onset_map(mono, ratio_thr, floor, zone_db, zone_min, zone_max):
    """Onsets and attack zones (source samples). An onset is a frame whose first-difference
    energy rises above ratio_thr x the loudest of the previous ONSET_REF frames (fewer at the
    start: a sample that starts loud starts with an onset). Its zone lasts until the 10 ms
    energy has fallen zone_db below its peak (at least zone_min, at most zone_max samples):
    a snare's body, or a whole strum."""
    n = len(mono) // FRAME
    e = np.zeros(n)
    for k in range(n):
        s = 0.0
        b = k * FRAME
        for i in range(max(b, 1), b + FRAME):
            d = mono[i] - mono[i - 1]
            s += d * d
        e[k] = s / FRAME
    es = np.zeros(n)                   # ~10 ms smoothed
    for k in range(n):
        lo = max(k - 3, 0)
        hi = min(k + 5, n)
        es[k] = e[lo:hi].mean()
    on = np.full(n, -1, dtype=np.int64)
    zone = np.full(n, -1, dtype=np.int64)
    m = 0
    last = -10 ** 9
    drop = 10.0 ** (-zone_db / 10.0)
    for k in range(n):
        ref = 0.0                      # loudest frame of the last ~32 ms
        for i in range(max(k - ONSET_REF, 0), k):
            if e[i] > ref:
                ref = e[i]
        if e[k] > floor and e[k] > ratio_thr * max(ref, floor * 0.1) \
                and (k - last) * FRAME > 2400:
            peak = es[k]
            j = k
            while j < n - 1 and (j - k) * FRAME < zone_max:
                j += 1
                if es[j] > peak:
                    peak = es[j]
                if (j - k) * FRAME >= zone_min and es[j] < peak * drop:
                    break
            on[m] = k * FRAME
            zone[m] = j * FRAME
            m += 1
            last = k
    return on[:m], zone[:m]


@njit(cache=True)
def sinc_kernel(u, taps):
    """The interpolation kernel as a function of u = cutoff * distance: sinc times a
    Blackman window over |u| < taps (the C version reads it from a table)."""
    if abs(u) >= taps:
        return 0.0
    s = 1.0 if u == 0.0 else np.sin(np.pi * u) / (np.pi * u)
    return s * (0.42 + 0.5 * np.cos(np.pi * u / taps) + 0.08 * np.cos(2 * np.pi * u / taps))


@njit(cache=True)
def _x(x, i, ch):
    return x[i, ch] if 0 <= i < x.shape[0] else 0.0


@njit(cache=True)
def _sinc_tap(x, ch, pos, taps, cutoff, interp=0):
    """Interpolation at fractional position pos (see DEFAULTS["interp"]); the sinc kernels are
    normalised by their sum. cutoff 0 = plain copy (pitch 1.0: positions are whole samples)."""
    i0 = int(np.floor(pos))
    f = pos - i0
    if cutoff == 0.0:
        return x[i0, ch] if 0 <= i0 < x.shape[0] else 0.0
    if interp == INTERP_LINEAR:
        return (1.0 - f) * _x(x, i0, ch) + f * _x(x, i0 + 1, ch)
    if interp == INTERP_HERMITE:                 # Catmull-Rom
        xm, x0, x1, x2 = _x(x, i0 - 1, ch), _x(x, i0, ch), _x(x, i0 + 1, ch), _x(x, i0 + 2, ch)
        c1 = 0.5 * (x1 - xm)
        c2 = xm - 2.5 * x0 + 2.0 * x1 - 0.5 * x2
        c3 = 0.5 * (x2 - xm) + 1.5 * (x0 - x1)
        return ((c3 * f + c2) * f + c1) * f + x0
    if interp == INTERP_FIXED:                   # cutoff as usual, window fixed to +-taps samples
        acc = 0.0
        wsum = 0.0
        for k in range(-taps + 1, taps + 1):
            d = k - f
            u = cutoff * d
            sv = 1.0 if u == 0.0 else np.sin(np.pi * u) / (np.pi * u)
            h = sv * (0.42 + 0.5 * np.cos(np.pi * d / taps) + 0.08 * np.cos(2 * np.pi * d / taps))
            wsum += h
            acc += h * _x(x, i0 + k, ch)
        return acc / wsum if wsum != 0.0 else 0.0
    half = int(np.ceil(taps / cutoff))
    acc = 0.0
    wsum = 0.0
    n = x.shape[0]
    for k in range(-half + 1, half + 1):
        h = sinc_kernel(cutoff * (k - f), taps)
        wsum += h
        i = i0 + k
        if 0 <= i < n:
            acc += h * x[i, ch]
    return acc / wsum if wsum != 0.0 else 0.0


@njit(cache=True)
def _ncc(mono, a, b, length, step):
    """Normalised cross-correlation of mono[a:a+length] and mono[b:b+length] (stride step)."""
    sab = 0.0
    saa = 0.0
    sbb = 0.0
    n = len(mono)
    for i in range(0, length, step):
        if a + i >= n or b + i >= n or a + i < 0 or b + i < 0:
            break
        va = mono[a + i]
        vb = mono[b + i]
        sab += va * vb
        saa += va * va
        sbb += vb * vb
    m = length // step
    if saa < 1e-9 * m and sbb < 1e-9 * m:
        return 1.0                     # both silent (< -90 dBFS): any splice is fine
    if saa <= 1e-12 or sbb <= 1e-12:
        return 0.0
    return sab / np.sqrt(saa * sbb)


@njit(cache=True)
def _best_jump(mono, mono_d, pos, j_lo, j_hi, length, fine_len, decim):
    """Coarse search on the decimated signal (window length centred on the splice points,
    lag step decim) keeping the best 3 candidates; each refined at full rate (+-decim, window fine_len, full band).
    Returns (jump, full-band correlation, coarse correlation of that candidate)."""
    ld = max(length // decim, 8)
    pd = pos // decim - ld // 2          # coarse windows are centred on the splice points
    cv = np.full(3, -2.0)
    cj = np.zeros(3, dtype=np.int64)
    for jd in range((j_lo + decim - 1) // decim, j_hi // decim + 1):
        c = _ncc(mono_d, pd, pd + jd, ld, 1)
        j = jd * decim
        # replace the weakest kept candidate unless a neighbour of a kept one is better
        near = -1
        for q in range(3):
            if cv[q] > -2.0 and abs(cj[q] - j) <= 2 * decim:
                near = q
        if near >= 0:
            if c > cv[near]:
                cv[near] = c
                cj[near] = j
            continue
        w = 0
        for q in range(1, 3):
            if cv[q] < cv[w]:
                w = q
        if c > cv[w]:
            cv[w] = c
            cj[w] = j
    best = -2.0
    fb = j_lo
    fc = -2.0
    for q in range(3):
        if cv[q] <= -2.0:
            continue
        for j in range(max(j_lo, cj[q] - decim), min(j_hi, cj[q] + decim) + 1):
            c = _ncc(mono, pos, pos + j, fine_len, 1)
            if c > best:
                best = c
                fb = j
                fc = cv[q]
    return fb, best, fc


@njit(cache=True)
def _run(x, mono, mono_d, onsets, zones, p, r, thr, hard, c_min, c_pre, retry, xfade, xfade_max, xfade_onset, search, corr_len, fine_len, decim,
         guard_pre, guard_post, lookahead, history, taps, aa, interp, out, stats, log):
    n_src, nch = x.shape
    cutoff = 0.0 if p == 1.0 else (aa if p < 1.0 else aa / p)
    pos = 0.0           # main tap, source samples
    pos2 = 0.0          # second tap while crossfading
    ideal = 0.0
    fading = False
    t = 0.0
    dt = 0.0
    corr = 0.0
    holdoff = 0
    rej_drift = 1e18    # drift at the last rejected search
    pre_skip = -1       # onset whose catch-up search failed
    oi = 0              # index of the first onset >= pos
    n_on = len(onsets)
    for n in range(out.shape[0]):
        if pos >= n_src - 2 or pos2 >= n_src - 2:
            return n
        # ---- output ----
        if fading:
            # gains: linear shape normalised for correlation c (constant power)
            a = 1.0 - t
            b = t
            norm = np.sqrt(max(a * a + b * b + 2.0 * corr * a * b, 1e-9))
            ga = a / norm
            gb = b / norm
            for ch in range(nch):
                v = ga * _sinc_tap(x, ch, pos, taps, cutoff, interp) + gb * _sinc_tap(x, ch, pos2, taps, cutoff, interp)
                out[n, ch] = v
            t += dt
            pos2 += p
        else:
            for ch in range(nch):
                out[n, ch] = _sinc_tap(x, ch, pos, taps, cutoff, interp)
        pos += p
        ideal += r
        if fading and t >= 1.0:
            fading = False
            pos = pos2
        if holdoff > 0:
            holdoff -= 1
        if fading or holdoff > 0:
            continue

        # ---- splice planning ----
        ip = int(pos)
        while oi < n_on and onsets[oi] < ip:
            oi += 1
        next_on = onsets[oi] if oi < n_on else 1 << 60
        last_on = onsets[oi - 1] if oi > 0 else -(1 << 60)
        last_zone = max(zones[oi - 1], last_on + guard_post) if oi > 0 else -(1 << 60)
        drift = pos - ideal
        ad = abs(drift)
        in_attack = ip < last_zone
        # source ahead of a jump target that the fade and the correlation windows cover
        span = max(int(xfade * p), corr_len // 2, fine_len) + 16
        # catch up just before an attack (late, so little drift is left), never inside a zone
        pre_onset = next_on - ip < int(xfade * p) + guard_pre + 256 and not in_attack \
            and pre_skip != oi
        if ad < thr * 0.25:
            continue
        # drift against the natural direction (an overshoot) heals by itself
        if drift * (p - r) < 0.0 and ad < hard and not pre_onset:
            continue
        if not ((ad > thr and not in_attack) or (pre_onset and ad > thr * 0.25) or ad > hard):
            continue
        if abs(drift - rej_drift) < retry and not pre_onset and ad < hard:
            continue
        j0 = int(-drift)
        snap = False
        # rule 1: the outgoing tap must not reach the next attack during the fade,
        # or the attack is heard from it and again later from the incoming tap
        L = xfade
        room = next_on - guard_pre - ip           # source samples before the attack
        if room < L * p:
            L = int(room / p)
        if j0 > 0:
            # forward (skip): never skip an attack; land on it instead,
            # but only if that doesn't overshoot the ideal position (rule 2)
            # land within 0.5..1.9 x the correcting jump: every splice reduces the drift
            j_lo = max(j0 - search, 32, j0 // 2)
            j_hi = min(j0 + search, lookahead - span, j0 * 19 // 10)
            j_lo = min(j_lo, j_hi)            # drift beyond the look-ahead: jump as far as it allows
            j_snap = next_on - guard_pre - ip
            if next_on - ip <= j_hi + span:
                if 32 <= j_snap <= j0 + thr and room >= xfade_onset * p:
                    snap = True
                    bj = j_snap
                    c = 0.0
                    L = xfade_onset
                else:
                    # stay clear of it: the incoming fade must end before the attack
                    j_hi = min(j_hi, j_snap - int(L * p))
            if not snap:
                if j_hi < j_lo or L < 32:
                    holdoff = 32
                    continue
                flen = fine_len
                bj, cf, cc = _best_jump(mono, mono_d, ip, j_lo, j_hi, corr_len, flen, decim)
                c = min(cf, cc)
                stats[3] += ((j_hi - j_lo) // decim + 1) * (corr_len // decim) + 3 * (2 * decim + 1) * flen
                stats[4] += 1
        else:
            # backward (repeat): never jump to before the last attack (no double hits)
            j_lo = max(j0 - search, -(history - corr_len // 2), -((-j0) * 19 // 10))
            j_hi = min(j0 + search, -32, -((-j0) // 2))
            j_hi = max(j_hi, j_lo)            # drift beyond the history: jump as far as it allows
            floor_t = last_zone                   # earliest allowed target: after the attack zone
            if ip + j_lo < floor_t:
                j_lo = floor_t - ip
            if j_hi < j_lo or L < 32:
                if ad > hard and L >= 32:
                    j_lo = max(j0 - search, -(history - corr_len // 2))
                    j_hi = min(j0 + search, -32)
                else:
                    holdoff = 32
                    continue
            flen = fine_len
            bj, cf, cc = _best_jump(mono, mono_d, ip, j_lo, j_hi, corr_len, flen, decim)
            c = min(cf, cc)
            stats[3] += ((j_hi - j_lo) // decim + 1) * (corr_len // decim) + 3 * (2 * decim + 1) * flen
            stats[4] += 1
        # demand a good match while the drift is small; relax towards the hard limit
        if not snap and ad < hard:
            c_req = c_pre if pre_onset else min(c_min, c_min * (hard - ad) / (hard - 2 * thr))
            if c < c_req:
                if pre_onset:
                    pre_skip = oi          # no usable catch-up: this attack plays with its drift
                else:
                    rej_drift = drift      # search again once the drift moved by `retry`
                stats[5] += 1
                if ad > 2 * thr:
                    stats[6] += 1
                continue
        # poor match: longer fade (smooth blend of noise-like material); still ends before the attack
        if not snap:
            L = int(xfade + (xfade_max - xfade) * (1.0 - max(min(c, 1.0), 0.0)))
            if room < L * p:
                L = max(int(room / p), 32)
            if bj + L * p > lookahead:          # the incoming tap must stay inside the ring
                L = max(int((lookahead - bj) / p), 32)
        fading = True
        pos2 = pos + bj
        t = 0.0
        dt = 1.0 / L
        # gains from the full-band correlation over exactly the faded source
        corr = 0.0 if snap else max(min(_ncc(mono, ip, ip + bj, max(int(L * p), 1), 1), 1.0), 0.0)
        holdoff = 0
        k = int(stats[0])
        if k < log.shape[0]:
            log[k, 0] = n
            log[k, 1] = bj
            log[k, 2] = c
            log[k, 3] = drift
            log[k, 4] = 1.0 if snap else (2.0 if pre_onset else (3.0 if ad >= hard else 0.0))
        stats[0] += 1
        stats[1] += c
        if snap:
            stats[2] += 1
    return out.shape[0]


def stretch_ts(x, semis=0.0, ratio=1.0, **kw):
    prm = dict(DEFAULTS)
    prm.update(kw)
    x = np.ascontiguousarray(x, dtype=np.int16)
    if x.ndim == 1:
        x = x[:, None]
    xf = x.astype(np.float64)
    mono = xf.mean(axis=1) / 32768.0
    d = prm["decim"]
    # box low-pass then decimate (the firmware would keep this per 0x800 block)
    mono_d = np.convolve(mono, np.ones(d) / d, mode="full")[d - 1:][::d].copy()
    onsets, zones = onset_map(mono, prm["onset_ratio"], prm["onset_floor"], prm["zone_db"],
                              prm["guard_post"], prm["zone_max"])
    p = 2.0 ** (semis / 12.0)
    n = int(len(x) / ratio)
    out = np.zeros((n, x.shape[1]))
    stats = np.zeros(7)
    log = np.zeros((4096, 5))        # per splice: output n, jump, corr, drift, reason
    got = _run(xf, mono, mono_d, onsets, zones, p, float(ratio), prm["thr"], prm["hard"], prm["c_min"], prm["c_pre"], prm["retry"], prm["xfade"], prm["xfade_max"],
               prm["xfade_onset"], prm["search"], prm["corr_len"], prm["fine_len"], prm["decim"],
               prm["guard_pre"], prm["guard_post"], prm["lookahead"], prm["history"],
               prm["taps"], prm["aa"], prm["interp"], out, stats, log)
    stretch_ts.last_stats = dict(splices=int(stats[0]),
                                 mean_corr=float(stats[1] / max(stats[0], 1)),
                                 snaps=int(stats[2]), onsets=len(onsets),
                                 searches=int(stats[4]), rejected=int(stats[5]), rejected_far=int(stats[6]),
                                 search_mac_per_sample=float(stats[3] / max(got, 1)),
                                 log=log[:min(int(stats[0]), len(log))])
    return np.clip(np.round(out[:got]), -32767, 32767).astype(np.int16)
