# Jam Tricks development

This repository contains the musical features and selected APP1 findings. Keep development
here self-contained. Never copy another checkout wholesale or import its Git history.
Review individual source files and documentation sections before adding outside material.
Do not commit firmware, generated analysis databases, decompiler dumps, audio samples, or
patched images. Build artifacts belong in ignored directories.

Keep payload firmware addresses in `app1/payload/imports.h`; the builder checks this.
Check expected original bytes at every patch site. Accept only explicitly supported firmware.
After feature changes, run `export_web.py`, `tests/verify_web.py`, and the scale-layout check
as documented in `app1/README.md`. Device validation is separate; do not call an offline-only
change tested on hardware. Preserve upstream attribution.

The website loads `web/standalone.html` from this repository through a GitHub raw URL.
After page or manifest changes, regenerate it with `package_web.py` and run its `--check`.
`export_web.py` regenerates it automatically. Commit the generated HTML with its sources.
