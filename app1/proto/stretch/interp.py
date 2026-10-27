"""Interpolation variants for Plus: same splicing, cheaper filters (CPU on the device).

    python interp.py            # metrics table + work/listen_interp.html (~2 min)
    python interp.py --no-clips # metrics only

Cost column: multiply-adds per channel per output sample (doubled during crossfades).
"""
import argparse
import os

import numpy as np

import corpus
import metrics
import render
from tsplice import (INTERP_FIXED, INTERP_HERMITE, INTERP_LINEAR, INTERP_SINC, DEFAULTS,
                     stretch_ts)

SR = corpus.SR

# name: (interp, taps)
VARIANTS = {
    "tsS6": (INTERP_SINC, 6),       # the first C version: half-width 6, widened by 1/cutoff above p = 1
    "tsS4": (INTERP_SINC, 4),
    "tsF6": (INTERP_FIXED, 6),      # 12 taps at any pitch
    "tsF4": (INTERP_FIXED, 4),      # 8 taps
    "tsF3": (INTERP_FIXED, 3),      # 6 taps
    "tsH":  (INTERP_HERMITE, 2),    # 4-point cubic, no anti-aliasing
    "tsL":  (INTERP_LINEAR, 1),     # 2 taps, like Roland's resampler; chosen (= ts now)
}
for name, (interp, taps) in VARIANTS.items():
    render.register(name, lambda x, s, r, i=interp, t=taps: stretch_ts(x, s, r, interp=i, taps=t))

SETTINGS = {k: render.SETTINGS[k] for k in ("up7", "up12", "down5", "down12", "up5slow")}
SETTINGS["up11"] = (11, 1.0)    # +12 is exactly 2x: no fractional positions, every filter looks ideal
CLIPS = ["piano", "guitar", "vox_loop", "ep_chord", "break_dry", "bells", "mix"]


def taps_per_sample(interp, taps, semis):
    p = 2 ** (semis / 12)
    if p == 1.0:
        return 0
    if interp == INTERP_SINC:
        cutoff = DEFAULTS["aa"] if p < 1 else DEFAULTS["aa"] / p
        return 2 * int(np.ceil(taps / cutoff))
    return {INTERP_FIXED: 2 * taps, INTERP_HERMITE: 4, INTERP_LINEAR: 2}[interp]


def tone(f, secs=2.0, amp=0.5):
    t = np.arange(int(secs * SR)) / SR
    return np.round(amp * 32767 * np.sin(2 * np.pi * f * t)).astype(np.int16)[:, None]


def rms_db(y, ref):
    y = y.astype(np.float64)[SR // 4:-SR // 4]
    return 20 * np.log10(max(np.sqrt(np.mean(y ** 2)), 1e-3) / ref)


def metrics_table(algos):
    print(f"{'setting':8s} {'algo':6s} {'taps':>5s} {'alias dB':>9s} {'6k purity':>10s}")
    rows = []
    for sname, (semis, ratio) in SETTINGS.items():
        p = 2 ** (semis / 12)
        for a in algos:
            interp, taps = VARIANTS.get(a, (None, None))
            fn = render.ALGOS[a]
            alias = float("nan")
            if p > 1:       # a tone that lands 6 kHz above Nyquist (alias at 18 kHz) should vanish
                f_in = 30000.0 / p
                if f_in < 22000:
                    x = tone(f_in)
                    alias = rms_db(fn(x, semis, ratio), 0.5 * 32767 / np.sqrt(2))
            pur = metrics.purity_db(fn(tone(6000.0), semis, ratio), [6000.0 * p])
            cost = taps_per_sample(interp, taps, semis) if interp is not None else 2
            rows.append((sname, a, cost, alias, pur))
            print(f"{sname:8s} {a:6s} {cost:5d} {alias:9.1f} {pur:10.1f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-clips", action="store_true")
    a = ap.parse_args()
    algos = list(VARIANTS) + ["fw1"]
    rows = metrics_table(algos)
    if a.no_clips:
        return
    render.clip_tests(algos, SETTINGS, CLIPS)
    table = "".join(f"<tr><td>{s}</td><td>{al}</td><td>{c}</td><td>{'' if np.isnan(x) else f'{x:.1f}'}</td>"
                    f"<td>{pu:.1f}</td></tr>" for s, al, c, x, pu in rows)
    note = ("<h2>Interpolation variants (CPU)</h2><p>All ts* use the same splicing; only the "
            "interpolation filter differs. <b>tsS6</b> = the first C version (sinc, half-width 6, widening when "
            "pitched up); <b>tsS4</b> half-width 4, widening; <b>tsF6/F4/F3</b> sinc with a fixed "
            "12/8/6 taps at any pitch; <b>tsH</b> 4-point cubic (Hermite), no anti-aliasing; "
            "<b>tsL</b> linear (chosen); <b>fw1</b> = Roland's Backing for reference. taps = multiply-adds per "
            "channel per sample (the main cost when pitched). up12 is exactly 2x (no fractional positions), so its "
            "purity says nothing; up11 is the representative high setting. alias dB: level of a tone that should "
            "vanish, landing 6 kHz above Nyquist (lower is better); 6k purity: how clean a 6 kHz tone stays (higher is better).</p>"
            "<table><tr><th>setting</th><th>algo</th><th>taps</th><th>alias dB</th><th>6k purity</th></tr>"
            + table + "</table>")
    render.listening_page(algos, SETTINGS, CLIPS, "listen_interp.html", note)
    print("wrote", os.path.join(render.WORK, "listen_interp.html"))


if __name__ == "__main__":
    main()
