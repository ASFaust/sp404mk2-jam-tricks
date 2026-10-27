# Time-stretch prototypes (goal #4)

Offline Python models for developing a fourth VARI option. Background:
[docs/app1/topics/time-stretch.md](../../../docs/app1/topics/time-stretch.md).

| File | What |
|---|---|
| `fwmodel.py` | the firmware's stretcher (`Resampler_Process`), VARI Off/Backing/Ensemble, same integer arithmetic |
| `tsplice.py` | the new one: transient-aware WSOLA splicing (parameters in `DEFAULTS`) |
| `corpus.py` | test clips from `~/Music/samples` (48 kHz excerpts cached in `work/corpus/`) and synthetic signals |
| `metrics.py` | objective measures: wobble, purity, click doubling/timing, onset recall/extra/timing |
| `render.py` | renders all algorithms x settings x clips, writes metrics CSVs and `work/listen.html` |
| `interp.py` | cheaper interpolation filters for Plus (CPU): metrics + `work/listen_interp.html` |
| `tune.py` | parameter sweeps for `tsplice` (`python tune.py c_min=0.9,0.95 thr=480,720`) |
| `tsplice_v1.py` | the version of the first listening round (A/B reference) |
| `tsx_c.py` | runs the C stretcher (`app1/payload/tsx.c`) on the PC inside an emulation of `Voice_RenderSetup`'s ring refill: `stretch_c(x, semis, ratio)` |
| `qemu/` | instruction counts of the ARM build under QEMU (`mps2-an500`, Cortex-M7), see below |

Run with `python3` (numba, scipy, soundfile, librosa; ffmpeg with
rubberband for the `rb` reference). Everything generated goes to `work/` (not in git).

```sh
python render.py --synth                      # synthetic tests only, ~10 s
python render.py                              # everything, ~3 min; then open work/listen.html
python render.py --algos fw2,ts --clips piano,break_dry --settings slow80,up7
```

Settings use the firmware's convention: `ratio` < 1 is slower (longer output), as SPEED;
`semis` is the pitch.

QEMU instruction counts (rebuild after changing `app1/payload/tsx.c`; `-DTSX_PROFILE` adds the
per-part numbers):

```sh
cd qemu
arm-none-eabi-gcc -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -O2 -ffreestanding \
    -fno-builtin -nostdlib -DTSX_PROFILE -I ../../../payload -I . -T link.ld startup.S bench.c \
    ../../../payload/tsx.c -o bench.elf -lgcc
qemu-system-arm -M mps2-an500 -nographic -semihosting -icount shift=0 -kernel bench.elf
```
`clip.h` (3 s of the "mix" clip) comes from `python gen_clip.py` and stays out of git.
