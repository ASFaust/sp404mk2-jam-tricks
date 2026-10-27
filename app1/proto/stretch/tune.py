"""Parameter sweeps for tsplice: one line of summary numbers per parameter set.

    python tune.py c_min=0.85,0.9,0.95  thr=480,720
(every combination is run; other parameters stay at tsplice.DEFAULTS)
"""
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import corpus
import metrics
from tsplice import stretch_ts

SETTINGS = {"slow80": (0, 0.8), "fast125": (0, 1.25), "up7": (7, 1.0), "down5": (-5, 1.0),
            "slow50": (0, 0.5)}
CHORD_F = (220.0, 277.18, 329.63, 440.0)


def evaluate(kw):
    clips = corpus.load_corpus()
    chord = corpus.synth_chord(CHORD_F)
    clicks, first, period = corpus.synth_clicks(tail=0.0)
    extra, err, sharp, purity, doubles, splices = [], [], [], [], [], 0
    for semis, ratio in SETTINGS.values():
        for name, x in clips.items():
            y = stretch_ts(x, semis, ratio, **kw)
            splices += stretch_ts.last_stats["splices"]
            m = metrics.onset_scores(x, y, ratio)
            extra.append(m["on_extra"])
            err.append(m["on_err_ms"])
            sharp.append(m["on_sharp"])
        y = stretch_ts(chord, semis, ratio, **kw)
        purity.append(metrics.purity_db(y, [f * 2 ** (semis / 12) for f in CHORD_F]))
        doubles.append(metrics.click_scores(stretch_ts(clicks, semis, ratio, **kw),
                                            first, period, ratio)["click_doubles"])
    return dict(extra=np.nanmean(extra), extra_med=np.nanmedian(extra), err_ms=np.nanmean(err),
                sharp=np.nanmean(sharp), chord_db=np.mean(purity), doubles=np.mean(doubles),
                splices=splices)


def main():
    grid = {}
    for arg in sys.argv[1:]:
        k, v = arg.split("=")
        grid[k] = [type_of(x) for x in v.split(",")]
    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*grid.values())] or [{}]
    with ProcessPoolExecutor() as ex:
        results = list(ex.map(evaluate, combos))
    for kw, r in zip(combos, results):
        print(f"{str(kw):40s} " + " ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                                           for k, v in r.items()))


def type_of(s):
    try:
        return int(s)
    except ValueError:
        return float(s)


if __name__ == "__main__":
    main()
