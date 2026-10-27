"""Render every algorithm x setting x clip, measure, and build a listening page.

    python render.py                      # all algorithms, settings, clips
    python render.py --algos fw0,fw2 --clips piano,break_dry --settings slow50
    python render.py --synth              # synthetic tests only (metrics table)

Output: work/out/<setting>/<clip>__<algo>.wav, work/metrics_*.csv, work/listen.html
"""
import argparse
import csv
import os
import subprocess
import tempfile

import numpy as np
import soundfile as sf

import corpus
import metrics
from fwmodel import stretch_fw
from tsplice import stretch_ts
from tsplice_v1 import stretch_ts as stretch_ts_v1

SR = corpus.SR
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")

# name: (semitones, time ratio).  ratio < 1 = slower / longer, as the SP's SPEED.
SETTINGS = {
    "slow50":  (0, 0.5),
    "slow80":  (0, 0.8),
    "fast125": (0, 1.25),
    "fast150": (0, 1.5),
    "up7":     (7, 1.0),
    "up12":    (12, 1.0),
    "down5":   (-5, 1.0),
    "down12":  (-12, 1.0),
    "up5slow": (5, 0.8),
}


def rubberband(x, semis, ratio):
    """High-quality offline reference (ffmpeg's librubberband filter, default options)."""
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.wav"), os.path.join(d, "b.wav")
        sf.write(a, x, SR, subtype="PCM_16")
        af = f"rubberband=tempo={ratio}:pitch={2 ** (semis / 12)}"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a, "-af", af, "-ar", str(SR),
                        "-c:a", "pcm_s16le", b], check=True)
        return sf.read(b, dtype="int16", always_2d=True)[0]


ALGOS = {
    "fw0": lambda x, s, r: stretch_fw(x, s, r, 0),   # VARI Off
    "fw1": lambda x, s, r: stretch_fw(x, s, r, 1),   # VARI Backing
    "fw2": lambda x, s, r: stretch_fw(x, s, r, 2),   # VARI Ensemble
    "ts1": stretch_ts_v1,                            # first version you listened to
    "ts":  stretch_ts,                               # new: transient-aware WSOLA splicing
    "rb":  rubberband,                               # reference, not for the SP
}


def register(name, fn):
    """Add an algorithm fn(x_int16, semis, ratio) -> int16 (n, ch)."""
    ALGOS[name] = fn


def synth_tests(algos, settings):
    rows = []
    tone = corpus.synth_tone(440.0)
    chord_f = (220.0, 277.18, 329.63, 440.0)
    chord = corpus.synth_chord(chord_f)
    clicks, first, period = corpus.synth_clicks(tail=0.0)
    drum, dfirst, dperiod = corpus.synth_clicks(tail=0.03)
    for sname, (semis, ratio) in settings.items():
        p = 2 ** (semis / 12)
        for a in algos:
            fn = ALGOS[a]
            r = {"setting": sname, "algo": a}
            y = fn(tone, semis, ratio)
            r["tone_am%"] = metrics.am_depth(y)
            r["tone_purity_db"] = metrics.purity_db(y, [440.0 * p])
            y = fn(chord, semis, ratio)
            r["chord_purity_db"] = metrics.purity_db(y, [f * p for f in chord_f])
            r.update(metrics.click_scores(fn(clicks, semis, ratio), first, period, ratio))
            d = metrics.click_scores(fn(drum, semis, ratio), dfirst, dperiod, ratio)
            r.update({"drum_" + k: v for k, v in d.items()})
            rows.append(r)
    return rows


def clip_tests(algos, settings, clips, write=True):
    rows = []
    data = corpus.load_corpus(clips)
    for sname, (semis, ratio) in settings.items():
        od = os.path.join(WORK, "out", sname)
        os.makedirs(od, exist_ok=True)
        for cname, x in data.items():
            for a in algos:
                y = ALGOS[a](x, semis, ratio)
                if write:
                    sf.write(os.path.join(od, f"{cname}__{a}.wav"), y, SR, subtype="PCM_16")
                r = {"setting": sname, "clip": cname, "algo": a}
                r.update(metrics.onset_scores(x, y, ratio))
                rows.append(r)
    return rows


def write_csv(rows, path):
    if not rows:
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in r.items()})


def print_table(rows, group_keys=("setting", "algo")):
    if not rows:
        return
    keys = [k for k in rows[0] if k not in group_keys and k != "clip"]
    print(" ".join(f"{k:>9.9s}" for k in group_keys) + " " + " ".join(f"{k[-12:]:>12s}" for k in keys))
    for r in rows:
        print(" ".join(f"{str(r[k]):>9.9s}" for k in group_keys) + " "
              + " ".join(f"{r[k]:12.3f}" for k in keys))


def listening_page(algos, settings, clips, page="listen.html", note=""):
    rows = []
    for sname, (semis, ratio) in settings.items():
        rows.append(f"<h2>{sname} &nbsp;<small>pitch {semis:+d} st, time ratio {ratio}</small></h2>")
        rows.append("<table><tr><th>clip</th><th>original</th>"
                    + "".join(f"<th>{a}</th>" for a in algos) + "</tr>")
        for c in clips:
            cells = [f"<td>{c}</td>", f'<td><audio controls preload="none" src="corpus/{c}.wav"></audio></td>']
            for a in algos:
                cells.append(f'<td><audio controls preload="none" src="out/{sname}/{c}__{a}.wav"></audio></td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")
        rows.append("</table>")
    html = ("<!doctype html><meta charset=utf-8><title>Stretch listening</title>"
            "<style>body{font:14px sans-serif;margin:16px}td,th{padding:2px 6px;text-align:left}"
            "audio{width:180px;height:28px}</style>"
            "<h1>SP-404MKII stretch prototypes</h1>"
            "<p>fw0/fw1/fw2 = firmware VARI Off/Backing/Ensemble (model); ts1 = first prototype; ts = improved; rb = rubberband "
            "(offline reference, far too heavy for the SP).</p>" + note + "\n".join(rows))
    with open(os.path.join(WORK, page), "w") as f:
        f.write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--algos", default=",".join(ALGOS))
    ap.add_argument("--settings", default=",".join(SETTINGS))
    ap.add_argument("--clips", default=",".join(corpus.CLIPS))
    ap.add_argument("--synth", action="store_true", help="synthetic tests only")
    ap.add_argument("--page", default="listen.html", help="name of the listening page")
    a = ap.parse_args()
    algos = a.algos.split(",")
    settings = {k: SETTINGS[k] for k in a.settings.split(",")}
    clips = a.clips.split(",")
    os.makedirs(WORK, exist_ok=True)
    rows = synth_tests(algos, settings)
    write_csv(rows, os.path.join(WORK, "metrics_synth.csv"))
    print_table(rows)
    if a.synth:
        return
    rows = clip_tests(algos, settings, clips)
    write_csv(rows, os.path.join(WORK, "metrics_clips.csv"))
    listening_page(algos, settings, clips, a.page)
    # summary: mean over clips
    print()
    summ = []
    for s in settings:
        for al in algos:
            rr = [r for r in rows if r["setting"] == s and r["algo"] == al]
            summ.append({"setting": s, "algo": al, **{
                k: float(np.nanmean([r[k] for r in rr]))
                for k in ("on_recall", "on_extra", "on_err_ms", "on_sharp")}})
    print_table(summ)


if __name__ == "__main__":
    main()
