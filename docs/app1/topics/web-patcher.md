# Jam Tricks: the web patcher

## Summary

**Jam Tricks** (name chosen by the user, 2026-10-27) is the public name of the musical patches:
free patches for live playing at jam sessions, offered on the user's website. The user gives
the page Roland's firmware zip, and JavaScript in the browser builds the patched
`SP404MKII_APP1.bin`. The site never distributes Roland's binary. The diagnostic CPU meter stays out.

The goal beyond v5.52: the page finds the patch sites itself by pattern, so the patches keep
working when Roland releases a new version with small changes.

## State (2026-10-28)

- **Live on the user's site, v5.52 only**, tested by the user: page `tools/sp404mk2-patcher/`
  (URL kept from before the name), section "Tools" on the home page. Offers `no-efx-timeout`,
  `scale-pads` / `scale-pads-wide`, `chrom-voices`, `vari4-stretch`, `tuner`.
- **Why v5.52 only:** the page recognises the firmware by SHA-256 and writes bytes at fixed
  file offsets from v5.52. Any other file is refused. 5.52 is Roland's newest release, so
  users of older versions can update first; the resolver matters once 5.53 appears.
- **Done in the firmware project:** the payload's import table (`app1/payload/imports.h`, see
  Design), `make_image.py`'s check against hard-coded Roland addresses, `export_web.py`.
- **Built 2026-10-28, offline only, not used by the page:** the pattern manifest
  (`app1/tools/anchors.py`, `resolve` in `patches.json`, `relocatable` and `imports` per patch)
  and the resolver (`resolve()` in `patcher.js`). Treating v5.52 as unknown, it finds all 94
  anchors and builds all 47 selections byte-identically to the SHA-256 path
  (`tests/verify_web.py`). Bench over older releases (`tests/resolve_bench.mjs`):

  | Release | Anchors | Patches resolved |
  |---|---|---|
  | v5.51, v5.50 | 94 / 94 | all but `tuner` |
  | v5.01, v5.00 | 91 / 94 | all but `vari4-stretch`, `tuner` |
  | v4.07 – v4.05 | 85–86 / 94 | `no-efx-timeout`, both scale pads, `chrom-voices` |
  | v4.04 and older | 43–81 / 94 | `no-efx-timeout` |

  `tuner` is refused because `Audio_RxHandler`'s replayed `movw r0, #0x9d0` (the low half of
  `g_param_store`) differs before v5.52; see To do 5. (Correction to the measurement below:
  `TopScreen_KeyDown` is found in v5.51; only its `movw`/`movt` immediates and `bl` offsets differ.)
- **Not built:** the RAM checks (region 9, skip-back buffer), so the page still accepts v5.52 only.

## To do (in order)

1. **Move our state out of MPU region 9** (`ram.h`, see Open questions): it sits 8 bytes above
   Roland's use there, and that use has grown between releases. A firmware change, so test on
   the device. Recommended first, since it's the one known flaw.
2. ~~**Pattern manifest.**~~ Done: `anchors.py` (rules and how they are derived in its docstring).
3. ~~**Resolver in `patcher.js`**~~ Done: `resolve(original, data)` returns a plan for
   `applyPatches` and a per-patch report. Not yet called by `app.js`.
4. **Skip-back buffer check and shrink** (section below): find every site that builds the
   buffer end, lower it if it would overlap the payload, check nothing else points into the range.
5. **Strict checks:** the instructions `hooks.S` replays must match exactly (done: their old
   bytes are exact). That makes `tuner` unavailable wherever `g_param_store` moved; to fix,
   `audio_rx_orig` could load `g_param_store` from the import table and continue after the
   `movt` (a payload change, test on the device). Struct offsets: field imports are found
   through a load or store with the offset kept exact in the pattern; still assumed rather than
   checked: `g_rec_ctrl_source` (`g_rec_ctrl + 0x258`) and offsets inside the payload's C code
   (e.g. voice `+0x14274`). The four render-buffer imports have one reference each in v5.52, so
   one pattern each.
6. **Test bench** (done: `node tests/resolve_bench.mjs [-v] <APP1 files>`): the resolver over
   older releases (going backwards in time, so only a robustness check). The real test is the first 5.53: resolve, flash, try each
   patch on the device.
7. **Page:** label a recognised but untested version clearly; show which patches were found.

Where to resume (2026-10-28): step 4 needs no device and fits the resolver: find the buffer-end
`movw`/`movt` sites per version, check them against the payload range and add the result to
`resolve()`'s report. Steps 1 and the `audio_rx_orig` change in 5 alter the payload and need a
device test. `app.js` should call `resolve()` only once 1 and 4 are done.

Whenever a patch changes: run `python3 app1/tools/export_web.py web`, test it, and commit the regenerated manifest
and `web/standalone.html` with the source change. The export records the source revision
and working-tree state, then bundles the page for deployment.

## Deployment

The personal website's existing patcher URL is a small loader. On each visit it fetches
`web/standalone.html` from this repository's `main` branch using `raw.githubusercontent.com`.
The generated file embeds the page, JavaScript modules, manifest, styles and fonts, so there
are no separate raw-host script or stylesheet requests. Raw GitHub's text/plain response is
read as text and rendered by the loader. Failed fetches show a retry option.

After changing page sources, run `python3 app1/tools/package_web.py`. After firmware changes,
`export_web.py` rebuilds both the manifest and standalone page. Commit and push the generated
files with the sources. A freshness check runs in CI without needing Roland's firmware.

## The website (2026-10-27)

How the page is built (details for the to-do above):


- The standalone patcher lives in `web/`: `patcher.js` (zip
  reading with `DecompressionStream`, SHA-256, applying changes; runs in Node too), `app.js`
  (the page), `patches.json`.
- `patches.json` comes from `python3 app1/tools/export_web.py web`.
  Its `PUBLIC` dict is the list of patches the site offers, with the text users see: today
  `no-efx-timeout`, `scale-pads`, `scale-pads-wide`, `chrom-voices`, `vari4-stretch`, `tuner`
  (works on the device, user, 2026-10-27); `cpu-meter` is diagnostic.
  The export replays the JSON for all allowed combinations (47) and compares each with
  `make_image.build()`. Export alongside source changes; the footer records the build revision.
- For now the page recognises the firmware by SHA-256 (v5.52 only) and applies exact changes.
  The pattern resolver will slot in where `identify()` is.
- Tested: Node against Roland's `sp404mk2_sys_v552.zip` (every combination equals the Python
  replay; test scripts were throwaway, a pattern for a permanent one: run `patcher.js` in Node
  over all combinations and compare SHA-256 with `export_web.apply()`); headless Chrome loading the page, choosing the zip, building: the download was
  byte-identical to `make_image.py --only no-efx-timeout,scale-pads-wide,chrom-voices,vari4-stretch`.

## Why fixed addresses can't work

v5.51 and v5.52 differ in 1.4 M bytes: inserted code shifts everything after it. Code and data
also move independently (in v5.51 the chromatic page's functions are where they are in v5.52,
but its vtable is 0x10 lower).

## Measurement: finding the anchors by pattern

The musical patches and their payload use 85 addresses in Roland's code: patch sites plus the
functions and globals the C payload calls. For each, a 12–64 byte pattern from v5.52 was
searched in each old release. `bl`/`b.w` offsets and pointer-like literal words were
wildcarded, and only a unique match counted:

| Release | Found uniquely |
|---|---|
| v5.51 | 75 / 85 |
| v5.50 | 70 / 85 |
| v5.01, v5.00 | 64 / 85 |
| v4.07 | 62 / 85 |
| v3.02 | 41 / 85 |

Most misses are data (vtable slots, the page-record table at `0x801F8F74`, the chromatic
semitone tables). Their bytes are mostly absolute pointers, so they change when functions move.
**Chained lookup** solves that: find the function by pattern, then search for the aligned word
`address | 1`. That found all three chromatic-page vtable slots (`0x802214CC`, `0x802214F0`,
`0x8022150C`) in v5.51, v5.50 and v5.01. The tuner's key handler (`0x801839B8`) is not found
in any older release: Roland changed it in v5.52, so the patcher would refuse `tuner` there.

## RAM for the payload: the skip-back buffer

The looper/skip-back buffer is raw SDRAM addressed by constants (not in the scatter-load
table). Its start and end are built with `movw`/`movt`:

| Release | Start | End | Size | Gap end → `0x83FF0000` | End sites (`movt`) |
|---|---|---|---|---|---|
| v3.02 | `0x8384B430` | `0x83F8B430` | `0x740000` | 402 KB | 2 |
| v4.04 | `0x8388FE5C` | `0x83FCFE5C` | `0x740000` | 128 KB | 7 |
| v4.07 | `0x83894224` | `0x83FD4224` | `0x740000` | 111 KB | 7 |
| v5.00 | `0x8383DDA4` | `0x83F7DDA4` | `0x740000` | 456 KB | 7 |
| v5.52 | `0x8383A424` | `0x83F7A424` | `0x740000` | 470 KB | 7 |

- **The size is constant** (7.25 MB, matching the `7.25` float in the skip-back setup that
  Tallfree patches). **The top boundary is constant**: `0x83FF0000`, MPU region 9 and
  `.sdram_data_83ff0000`.
- **The gap is leftover, not a reservation.** The buffer starts right after the static data
  (in v5.52 `.sdram_8383a30c` ends at `0x8383A424`), so the gap shrinks whenever Roland's
  static data grows: in v4.07 only 111 KB were left.
- Every user computes planes and lengths from (end − start) at run time, so changing the end in
  all its sites shrinks the buffer consistently (Tallfree, `resources/repos/tallfree/image/make_image.py`,
  runs on devices with 2.5 MB). Skip-back capacity is 5/7.25 of the buffer, i.e. proportional.

**Decision:** the payload keeps a fixed range directly under the fixed boundary,
`0x83FF0000 − size` (today `0x83FE0000`, 64 KB). The patcher sets the buffer end to
`min(end, payload start)`. With today's firmware this changes nothing; if Roland's static data
grows into our range, the buffer gives up the difference (≤ 64 KB of 7.25 MB). The payload
address depends only on the payload size, never on the firmware version, so the payload is
still linked once, offline.

Checks before writing (refuse on any failure):
- Find the end and start as `movw`/`movt` pairs; their difference must be `0x740000`. Collect
  *every* site that builds the end (7 today, 2 in v3.02, so the count is not fixed) and patch
  all of them.
- Nothing else may refer into `[new end, 0x83FF0000)`: no literal-pool word and no
  `movw`/`movt` pair. Decode properly: a raw word scan over code reports instruction bytes such
  as `e8bd` as false "pointers".
- No other form of the end: no literal word equal to the end, no `0x740000` constant combined
  with the start.
- The scatter-load table has no region overlapping the payload range.

(Correction: a first scan flagged v4.07 because it builds `0x83FDxxxx` addresses. That is the
buffer end `0x83FD4224`, below `0x83FE0000`, so our range was free there too.)

## Design

- **Python stays the source of truth.** `make_image.py` exports a manifest (JSON): per site a
  masked pattern, the offset into it, the expected original bytes and the replacement, plus
  chained lookups (vtable word = resolved function | 1) and checks on struct offsets read
  from Roland's instructions (e.g. the `#0x1c` of an `ldr`). JavaScript only searches,
  checks and writes.
- **Payload imports (done).** Every Roland address the payload uses is listed once in
  `app1/payload/imports.h` (name, v5.52 value). `imports.S` puts them first in the payload:
  at `0x83FE0000` the word `"IMP1"`, the count (69), then one word per import, symbol
  `imp_<name>`. C code calls through them (`IMP(Key_IsHeld)`); the trampolines in `hooks.S`
  load the continuation address into `r12` (free at those points) and `ldr.w pc, [r12]`.
  The patcher overwrites table words and nothing else in the payload. For v5.52 the table holds
  the old constants, and the patches to Roland's code are byte-identical to before.
  `make_image.py` (`check_imports_only`) refuses a payload whose code or constant tables hold a
  Roland address (SDRAM below the payload, DTCM/OCRAM) outside the table, using the ELF's
  `$t`/`$d` mapping symbols to tell code from data; string literals are linked last and not
  checked. ITCM addresses aren't checked
  (too close to ordinary numbers).
- **A small Thumb-2 encoder** in JavaScript for what we generate: `b.w`, `movw`/`movt`,
  `ldr.w pc, [pc, #n]`. The scatter-table copy is generic already.
- **All or nothing per patch:** a patch is applied only when every anchor resolves uniquely and
  every check passes. Versions never tested on the device are labelled as such.
- **Test bench:** run the resolver over every release in a local collection of older APP1 releases; the results must
  agree with each other and with v5.52's known addresses.

## Open questions

- **Our state in MPU region 9.** `ram.h` keeps state from `0x83FFBF60`, 8 bytes above the
  highest address Roland uses there in v5.52 (`0x83FFBF58`). That top grows between releases
  (`movw/movt` values `0x83FFBB30` in v3.02, `…BB38` v4.x/v5.00, `…BB40` v5.01, `…BB58`
  v5.50+), so this is a second free-memory assumption: the patcher must check it, or the state
  moves somewhere safer.
- **Displaced instructions.** Trampolines in `hooks.S` replay Roland's first instructions of
  a hooked function (e.g. `movw r0, #0x9d0` in `audio_rx_orig`). The manifest must require
  those bytes exactly, not masked.

- Struct layout changes are invisible to byte patterns; which offsets does the payload rely on,
  and can each one be read from a Roland instruction?
- Does anything use the `7.25` float in a non-proportional way (a fixed length from the
  start)? Tallfree's shrink says no for v5.52; to check per version.
- How to tell users to recover from a bad image (see [patching.md](../patching.md)).
