# APP1 development

Run commands from the repository root. Install Python 3 and the ARM GCC/binutils toolchain
(`gcc-arm-none-eabi`, `binutils-arm-none-eabi`). Capstone is optional for `fwmem.py`.
Ghidra is optional for analysis. Offline stretch experiments need additional Python
packages listed in [their README](proto/stretch/README.md).

## Firmware input and builds

Obtain the original v5.52 system update from Roland and unpack APP1 into
`resources/firmware/sp404mk2_sys_v552/SP404MKII_APP1.bin`, or point to a local copy.
Roland serves every release as `https://static.roland.com/assets/media/zip/sp404mk2_sys_vNNN.zip`
(NNN = 200, 201, 300, 301, 302, 404–407, 500, 501, 550, 551, 552). Keep the zips in the
ignored `firmware/` directory; older releases are only needed for `resolve_bench.mjs`.

```sh
curl -fLO --output-dir firmware https://static.roland.com/assets/media/zip/sp404mk2_sys_v552.zip
unzip firmware/sp404mk2_sys_v552.zip -d resources/firmware

export SP404_APP1=/absolute/path/to/SP404MKII_APP1.bin
python3 app1/tools/make_image.py --list
python3 app1/tools/make_image.py --only no-efx-timeout,scale-pads,chrom-voices,vari4-stretch,tuner
```

The builder checks the original SHA-256 and every replaced byte. Output goes to
`app1/build/SP404MKII_APP1.bin`, which is ignored by Git. `scale-pads` and
`scale-pads-wide` are alternatives. With no selection, only `no-efx-timeout` is enabled.
Installation and recovery: [patching.md](../docs/app1/patching.md).

## Browser patcher

```sh
python3 app1/tools/export_web.py web
python3 -m http.server 8000
```

Open `http://localhost:8000/web/`. Exporting compiles the payload and verifies all 47
allowed nonempty musical patch selections against the Python builder. Check the browser replay with
`python3 tests/verify_web.py` (optional `SP404_ZIP=/path/to/update.zip` also checks ZIP input).
The export also writes the pattern rules for other firmware versions (`app1/tools/anchors.py`);
`verify_web.py` checks that they rebuild every v5.52 selection, and
`node tests/resolve_bench.mjs -v <APP1 files>` reports what they find in other releases.
Commit `web/patches.json` and `web/standalone.html` alongside source changes. The manifest
records its source revision and working-tree state. Exporting also packages the page as one
HTML file, including its scripts, manifest, styles and fonts.

For page-only changes (no firmware needed), rebuild the standalone file with:

```sh
python3 app1/tools/package_web.py
python3 app1/tools/package_web.py --check
```

The personal website's `tools/sp404mk2-patcher/` URL fetches
`https://raw.githubusercontent.com/ASFaust/sp404mk2-jam-tricks/refs/heads/main/web/standalone.html`
and renders it when opened. Publishing a change means pushing the sources and rebuilt
standalone file to `main` here. No assets need copying into the website, and no separate
GitHub Pages setup is required. GitHub's normal raw-content caching may delay new content
briefly. If GitHub cannot be reached, the website shows a retry option.

You can also serve `web/standalone.html` yourself. It needs no other files or network
requests once loaded. For local development, `web/index.html` still uses the separate sources.

## Recreate the analysis project

The project database is generated locally. The exported names and types are the source of truth.
Install Ghidra and substitute your headless launcher if it is not `ghidra.analyzeHeadless`.

```sh
mkdir -p app1/build app1/ghidra
python3 app1/tools/build_elf.py "${SP404_APP1:-resources/firmware/sp404mk2_sys_v552/SP404MKII_APP1.bin}" app1/build/app1.elf
P="$PWD/app1"
ghidra.analyzeHeadless "$P/ghidra" sp404mk2 -import "$P/build/app1.elf" -processor "ARM:LE:32:Cortex"
ghidra.analyzeHeadless "$P/ghidra" sp404mk2 -process app1.elf -noanalysis \
  -scriptPath "$P/tools/ghidra_scripts" -postScript FixCoverage.java \
  -postScript ApplySymbols.java "$P/tools/symbols_tallfree.txt" \
  -postScript ApplySymbols.java "$P/tools/symbols.txt" \
  -postScript ApplyTypes.java "$P/tools/types.txt"
```

Do not overwrite an existing analysis project. Close the Ghidra GUI before using the
headless tools. `decomp.sh`, `xrefs.sh` and `grepcallers.sh` query the project read-only;
`fwmem.py` offers quick memory and disassembly queries without Ghidra.

## Small offline check

```sh
cc -DHOST_TEST -O2 app1/payload/test_inkey.c app1/payload/inkey.c -o /tmp/jam-tricks-inkey
/tmp/jam-tricks-inkey
```
