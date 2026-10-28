# SP-404MKII Jam Tricks

Musical firmware patches (Jam Tricks v1.0) for playing the Roland SP-404MKII at jam sessions, plus
notes and tools for understanding its APP1 main application.

- **Scale pads:** all 16 chromatic pads play notes in the selected scale; optional ±24 pitch range.
- **Chromatic voices:** LEGATO, MONO and voice limits, with the pad pitch as the base tuning.
- **VARI Plus:** a fourth time-stretch mode with transient-aware WSOLA splicing.
- **Guitar tuner:** BANK A + BANK B opens a tuner on the sampler.
- **Effect screen:** stays open until EXIT.

The musical features have been tested on a device with firmware **5.52**. The
CPU meter is an optional development aid. Future firmware versions are not supported yet.

Use the [published browser patcher](https://asfaust.github.io/tools/sp404mk2-patcher/).
The website loads the complete patcher from this repository when the page opens.
[web/](web/) contains the source and a generated `standalone.html` for deployment.
It patches a file you supply locally in your browser; firmware is not uploaded.

## Work on the features

See [app1/README.md](app1/README.md) for build and Ghidra setup,
[the feature roadmap](docs/app1/goals.md), and [the APP1 notes](docs/app1/topics/README.md).

```
app1/payload/         C and Thumb assembly for the features
app1/tools/           checked patch builder, browser exporter, APP1 analysis tools
app1/proto/stretch/   offline time-stretch models and benchmarks
web/                 standalone browser patcher and generated patch manifest
docs/app1/           APP1 format, patching method, feature findings and roadmap
LICENSES/            upstream notices
```

No Roland firmware, modified firmware images, generated ELF files, decompiler output,
or Ghidra database is distributed here. Obtain the original system update from Roland.
The repository starts with a fresh history containing only this extraction.

Patching can cause crashes or data loss. Back up projects, keep the original APP1 for
recovery, and read [the installation and recovery notes](docs/app1/patching.md).
This is an independent project, unaffiliated with Roland.

[Third-party acknowledgements](THIRD_PARTY_NOTICES.md).
