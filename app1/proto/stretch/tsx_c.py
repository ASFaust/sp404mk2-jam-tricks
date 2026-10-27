"""Run the C stretcher (app1/payload/tsx.c) on the PC, inside an emulation of what Roland's
Voice_RenderSetup does around the resampler: source rings filled in 0x800-sample chunks up
to FILL percent, block position tags, output in blocks of 0x800.

    y = stretch_c(x, semis, ratio)        # int16 (n, ch) in and out
    stretch_c.last_stats                  # blocks, underruns
"""
import ctypes
import os
import subprocess

import numpy as np

from fwmodel import rate_q14

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
SO = os.path.join(HERE, "work", "tsx_host.so")
SRC = [os.path.join(ROOT, "payload", f) for f in ("tsx.c",)]

RING = 0x2800
BLOCK = 0x800
FILL = 60                   # Ensemble's fill level; mode 3 gets it through the 0x800E4392 patch
STRETCHER_SIZE = 0x14108
STATE_SIZE = 0x328
RING_L, RING_R = 0x40, 0xA0A0


def _build():
    if os.path.exists(SO) and all(os.path.getmtime(SO) > os.path.getmtime(s) for s in SRC
                                  + [os.path.join(ROOT, "payload", "tsx.h")]):
        return
    os.makedirs(os.path.dirname(SO), exist_ok=True)
    subprocess.run(["gcc", "-O2", "-DTSX_HOST", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                    "-I", os.path.join(ROOT, "payload"), *SRC, "-o", SO, "-lm"], check=True)


_build()
_lib = ctypes.CDLL(SO)
_lib.tsx_run.restype = ctypes.c_int
_lib.tsx_run.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_int, ctypes.POINTER(ctypes.c_int32), ctypes.c_int, ctypes.c_void_p]


def stretch_c(x, semis=0.0, ratio=1.0, fill=FILL):
    x = np.ascontiguousarray(x, dtype=np.int16)
    if x.ndim == 1:
        x = x[:, None]
    nch = x.shape[1]
    s = np.zeros(STRETCHER_SIZE, dtype=np.uint8)
    st = np.zeros(STATE_SIZE, dtype=np.uint8)
    s[0x1C:0x20].view(np.int32)[0] = 3
    s[0x20:0x24].view(np.uint32)[0] = rate_q14(semis)
    s[0x14104:0x14108].view(np.float32)[0] = ratio
    st[0x4C:0x50].view(np.int32)[0] = -1              # what Stretch_Init leaves at voice start
    rings = [(s[o + 0x60:o + 0x60 + 4 * RING].view(np.int32), s[o:o + 0x60].view(np.uint32), o)
             for o in (RING_L, RING_R)[:nch]]
    hdr = s[RING_L:RING_L + 0x60].view(np.uint32)     # block tags 0..4, write 5, read 14
    scratch = np.zeros(4096, dtype=np.float32)
    out_l = np.zeros(BLOCK, dtype=np.int16)
    out_r = np.zeros(BLOCK, dtype=np.int16)
    pos = ctypes.c_int32(0)
    ctypes.c_int.in_dll(_lib, "tsx_log_n").value = 0
    (ctypes.c_int * 8).in_dll(_lib, "tsx_count")[:] = [0] * 8
    src = 0
    chunks, blocks, underruns = [], 0, 0
    while blocks < 100000:
        # Voice_RenderSetup's refill: 0x800 chunks while the ring is below `fill` percent
        while src < len(x):
            write, read = int(hdr[5]), int(hdr[14])
            if (write - read) % RING * 100 // RING >= fill:
                break
            n = min(BLOCK, len(x) - src)
            if (write - read) % RING + n >= RING:     # would overtake the reader
                break
            for ch, (data, h, _) in enumerate(rings):
                idx = (write + np.arange(n)) % RING
                data[idx] = x[src:src + n, ch].astype(np.int32) << 13
                h[write >> 11] = src                  # block position tag
                h[5] = (write + n) % RING
            src += n
        got = _lib.tsx_run(s.ctypes.data, st.ctypes.data, out_l.ctypes.data,
                           out_r.ctypes.data if nch == 2 else None, BLOCK, ctypes.byref(pos),
                           nch, scratch.ctypes.data)
        blocks += 1
        y = np.stack([out_l[:got], out_r[:got]], axis=1)[:, :nch].copy()
        chunks.append(y)
        if got < BLOCK:
            if src >= len(x):
                break
            underruns += 1
            if got == 0 and underruns > 1000:
                break
    log = np.ctypeslib.as_array((ctypes.c_double * 6 * 4096).in_dll(_lib, "tsx_log"))
    nlog = ctypes.c_int.in_dll(_lib, "tsx_log_n").value
    counts = list((ctypes.c_int * 8).in_dll(_lib, "tsx_count"))
    stretch_c.last_stats = dict(blocks=blocks, underruns=underruns, splices=nlog,
                                no_fwd_range=counts[0], no_back_range=counts[1], rejected=counts[2],
                                log=log[:nlog].copy())
    return np.concatenate(chunks) if chunks else np.zeros((0, nch), dtype=np.int16)
