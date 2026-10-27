"""Test material: excerpts from the user's sample library plus synthetic signals.

Library clips are resampled to 48 kHz (the SP's rate; the stretcher's periods are in
48 kHz samples), cropped and cached as 16-bit WAV in work/corpus/.

    clips = load_corpus()          # {name: int16 array (n, ch)}
"""
import os
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SR = 48000
LIBRARY = os.environ.get("SP404_SAMPLE_LIBRARY", os.path.expanduser("~/Music/samples"))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "work", "corpus")

# name: (path in the library, start s, max length s)
CLIPS = {
    "break_dry":   ("loops/drums/90_CrnchyBrksDry_38_64_SP.wav", 0.0, 8.0),
    "break_wet":   ("loops/drums/100_CrnchyBrksWt_15_64_SP.wav", 0.0, 8.0),
    "tabla":       ("loops/drums/168_Gm_SturdyTabla_01_697.wav", 0.0, 8.0),
    "vox_loop":    ("loops/100_C_FemaleVox_SP_270_01.wav", 0.0, 8.0),
    "vox_ragga":   ("fx/vocal/Bm_Ragga_345_01.wav", 0.0, 8.0),
    "piano":       ("chords/piano/82_Am_SadnessPiano_853.wav", 0.0, 8.0),
    "ep_chord":    ("chords/piano/Gm_Gm7_SilkVintageEp_01_606.wav", 0.0, 8.0),
    "strings":     ("loops/strings/120_Am_Violin2_33_189_SP.wav", 0.0, 8.0),
    "choir_pad":   ("chords/pad/chord long/Cm_FGChoirPad_SP_202_01.wav", 0.0, 8.0),
    "guitar":      ("single notes/guitar/Am_Am7_ChordNylonGtr_01_714.wav", 0.0, 8.0),
    "flute":       ("loops/flute/100_Cm_DawnFlute_01_678.wav", 0.0, 8.0),
    "trumpet":     ("single notes/brass/Mdm_Swll_Flgl_Trmpt_03_C_265.wav", 0.0, 8.0),
    "upright":     ("single notes/bass/100_Fm_UprightAcBass01_575.wav", 0.0, 8.0),
    "bass_808":    ("single notes/bass/C#_Smooth808Bass_01_682.wav", 0.0, 8.0),
    "reese":       ("fx/reese/175_BendReese_SP_196_03.wav", 0.0, 8.0),
    "bells":       ("fx/bell-y/100_Bb_UpCloseBell_01_555.wav", 0.0, 8.0),
    "mix":         ("songs/Walking_2026_mix.wav", 40.0, 8.0),
}


def _to_int16(x):
    return np.clip(np.round(x * 32767.0), -32767, 32767).astype(np.int16)


def _load_library_clip(path, start, length):
    x, sr = sf.read(os.path.join(LIBRARY, path), dtype="float64", always_2d=True)
    if sr != SR:
        g = gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g, axis=0)
    a = int(start * SR)
    return _to_int16(x[a:a + int(length * SR)])


def load_corpus(names=None):
    os.makedirs(CACHE, exist_ok=True)
    out = {}
    for name in names or CLIPS:
        f = os.path.join(CACHE, name + ".wav")
        if not os.path.exists(f):
            sf.write(f, _load_library_clip(*CLIPS[name]), SR, subtype="PCM_16")
        out[name] = sf.read(f, dtype="int16", always_2d=True)[0]
    return out


# ---- synthetic signals with known ideal output ----

def synth_tone(f=440.0, secs=3.0, amp=0.5):
    t = np.arange(int(secs * SR)) / SR
    return _to_int16(amp * np.sin(2 * np.pi * f * t))[:, None]


def synth_chord(fs=(220.0, 277.18, 329.63, 440.0), secs=3.0, amp=0.2):
    t = np.arange(int(secs * SR)) / SR
    return _to_int16(sum(amp * np.sin(2 * np.pi * f * t + i) for i, f in enumerate(fs)))[:, None]


def synth_clicks(bpm=120.0, secs=4.0, amp=0.8, tail=0.0):
    """Short decaying clicks on each beat; the decay tail (s) makes them drum-like."""
    n = int(secs * SR)
    x = np.zeros(n)
    period = int(SR * 60.0 / bpm)
    k = np.arange(int(SR * max(tail, 0.002)))
    shape = np.exp(-k / (SR * max(tail, 0.0005))) * np.cos(2 * np.pi * 1000 * k / SR)
    for p in range(period // 2, n - len(k), period):
        x[p:p + len(k)] += amp * shape
    return _to_int16(x)[:, None], period // 2, period


def synth_sweep(f0=100.0, f1=8000.0, secs=3.0, amp=0.5):
    t = np.arange(int(secs * SR)) / SR
    phase = 2 * np.pi * f0 * secs / np.log(f1 / f0) * (np.exp(t / secs * np.log(f1 / f0)) - 1)
    return _to_int16(amp * np.sin(phase))[:, None]
