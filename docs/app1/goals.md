# Feature roadmap

| Feature | State | Notes |
|---|---|---|
| Scale pads and chromatic voices | Tested on device | [Chromatic mode](topics/chromatic-mode.md) |
| Effects screen without timeout | Tested on device | [Effects timeout](topics/effects-timeout.md) |
| VARI Plus time stretch | Tested on device | [Time stretch](topics/time-stretch.md) |
| Guitar tuner | Tested on device | [Tuner](topics/tuner.md) |
| Browser patcher | Published for v5.52; pattern resolver planned | [Web patcher](topics/web-patcher.md) |
| Free-length / multitrack looper | Paused; UX design required before implementation | [Looper](topics/looper.md) |
| Smooth sample loops | Parked; needs audio memory | [Sample loops](topics/sample-loop.md) |

Development aids: [CPU meter](topics/cpu-meter.md) and [custom pages](topics/custom-pages.md).

Before supporting other firmware versions, move payload state out of the narrowly
available region-9 tail or validate that space, and implement strict pattern resolution.
The current public builder deliberately accepts only the known original v5.52 image.
