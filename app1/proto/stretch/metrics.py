"""Objective measures for stretched / pitched audio. Each targets one known weakness:

  am_depth      steady tone/chord in -> amplitude wobble out (splice phasiness), % (lower = better)
  purity_db     energy outside the expected partials, dB below total (aliasing, splice
                noise; more negative = better)
  click_scores  click train in -> one sharp click per beat out: concentration (1 = perfect),
                doubled clicks per beat, timing error in ms
  onset_scores  real material: onsets found where expected, extra (doubled) onsets,
                timing error, and how sharp they stay relative to the input
"""
import numpy as np
from scipy.signal import find_peaks, welch

SR = 48000


def _mono(y):
    y = np.asarray(y, dtype=np.float64)
    return (y.mean(axis=1) if y.ndim == 2 else y) / 32768.0


def am_depth(y, frame_ms=5.0, trim_s=0.25):
    """Std/mean of short-frame RMS over the steady part, in %."""
    x = _mono(y)
    x = x[int(trim_s * SR):len(x) - int(trim_s * SR)]
    n = int(frame_ms * SR / 1000)
    frames = x[:len(x) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return 100.0 * rms.std() / max(rms.mean(), 1e-12)


def purity_db(y, freqs, rel_bw=0.02, trim_s=0.25):
    """Power outside +-rel_bw around the expected partials, relative to total (dB)."""
    x = _mono(y)
    x = x[int(trim_s * SR):len(x) - int(trim_s * SR)]
    f, p = welch(x, SR, nperseg=8192)
    inband = np.zeros_like(p, dtype=bool)
    for fc in freqs:
        inband |= np.abs(f - fc) <= max(rel_bw * fc, 2 * SR / 8192)
    out = p[~inband].sum()
    return 10 * np.log10(max(out, 1e-20) / max(p.sum(), 1e-20))


def click_scores(y, first, period, ratio, win_ms=1.5):
    """For a click train (first click at input sample `first`, every `period`), stretched
    by `ratio` (output time = input time / ratio)."""
    x = np.abs(_mono(y))
    w = int(win_ms * SR / 1000)
    half = int(period / ratio / 2)
    conc, doubles, errs = [], [], []
    t_in = first
    while True:
        c = int(round(t_in / ratio))
        if c + half >= len(x):
            break
        if c - half >= 0:
            seg = x[c - half:c + half]
            e = seg ** 2
            tot = e.sum()
            if tot > 0:
                pk = int(np.argmax(seg))
                conc.append(e[max(pk - w, 0):pk + w].sum() / tot)
                errs.append((pk - half) * 1000.0 / SR)
                # separate peaks of a 2 ms RMS envelope with real dips between them
                env = np.sqrt(np.convolve(seg ** 2, np.ones(2 * w) / (2 * w), mode="same"))
                pk_idx, _ = find_peaks(env, prominence=0.25 * env.max(), distance=int(0.005 * SR))
                n_pk = len(pk_idx)
                doubles.append(max(n_pk - 1, 0))
        t_in += period
    return {
        "click_conc": float(np.mean(conc)) if conc else 0.0,
        "click_doubles": float(np.mean(doubles)) if doubles else 0.0,
        "click_err_ms": float(np.mean(np.abs(errs))) if errs else 0.0,
    }


def onset_scores(x_in, y, ratio, tol_ms=40.0):
    """Onsets of the input, moved to t / ratio, matched against onsets of the output:
    recall (found where expected), extra (unmatched output onsets per expected: doubled or
    invented attacks), err_ms (mean |timing error| of matches), sharp (onset strength
    relative to the input's; 1 = as sharp)."""
    import librosa
    a, b = _mono(x_in).astype(np.float32), _mono(y).astype(np.float32)
    hop = 256
    env_a = librosa.onset.onset_strength(y=a, sr=SR, hop_length=hop)
    env_b = librosa.onset.onset_strength(y=b, sr=SR, hop_length=hop)
    on_a = librosa.onset.onset_detect(onset_envelope=env_a, sr=SR, hop_length=hop, units="frames")
    on_b = librosa.onset.onset_detect(onset_envelope=env_b, sr=SR, hop_length=hop, units="frames")
    nan = float("nan")
    if len(on_a) == 0:
        return {"on_recall": nan, "on_extra": nan, "on_err_ms": nan, "on_sharp": nan}
    exp = on_a * hop / SR / ratio
    keep = exp < len(b) / SR - 0.05
    exp, on_a = exp[keep], on_a[keep]
    got = on_b * hop / SR
    tol = tol_ms / 1000.0
    used = np.zeros(len(got), dtype=bool)
    hits, sharp, errs = 0, [], []
    for k, t in enumerate(exp):
        d = np.abs(got - t)
        d[used] = np.inf
        j = int(np.argmin(d)) if len(d) else -1
        if j >= 0 and d[j] <= tol:
            used[j] = True
            hits += 1
            errs.append(d[j] * 1000.0)
            sharp.append(env_b[on_b[j]] / max(env_a[on_a[k]], 1e-9))
    n = max(len(exp), 1)
    return {"on_recall": hits / n, "on_extra": (len(got) - hits) / n,
            "on_err_ms": float(np.mean(errs)) if errs else nan,
            "on_sharp": float(np.median(sharp)) if sharp else nan}

