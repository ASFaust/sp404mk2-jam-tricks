# Patching: how we change the firmware

The method behind every feature in [goals.md](goals.md): how a change gets into the
firmware, recovery considerations, and the steps each change goes through.

## We patch, we don't recompile

Decompiled code doesn't compile back into a working firmware. Recovering 10,000+ C++
functions to that level isn't realistic. Instead we **patch Roland's original APP1**, the way
[Tallfree](../references/tallfree.md) does:

- **Byte patches** for small changes: flip a branch, change a constant. Tallfree changes only
  13 bytes inside Roland's code.
- **Injected code** for larger changes: write C, compile just that with `arm-none-eabi-gcc`,
  and append it to the image in free flash. APP1 ends at flash `0x307A00`; the updater
  accepts images up to `0x3FF000`, which leaves about 990 KB. Redirect one existing function
  pointer (a vtable word) or branch to the new code. It does its work and calls the original.

The output is always a **patch script** (like Tallfree's `image/make_image.py`) that reads
Roland's original APP1 and writes a patched one. It checks every original byte before changing
it, so a wrong address fails at build time and never reaches the device.

Prefer patching **data** (function pointers) over **instructions**. The Cortex-M7 caches
instructions, so patching code that is already in SDRAM at run time needs cache maintenance.
Patches applied in the image before flashing don't have that problem.

### Why not recompile all of APP1?

Safety isn't the problem (see below); the size of the job is.
- Ghidra's output is a readable guess per function, not compilable source. Types, structs,
  C++ classes and vtables are lost, and it makes mistakes. Fixing 10,500 functions by hand is
  months to years of work.
- A recompiled firmware must behave *exactly* like the original. One wrong struct offset in
  the audio engine means clicks, drift or crashes that are very hard to trace back.
- Roland used ARM's compiler; we'd use GCC, which produces different code and timing. The
  audio path has a 1.3 ms deadline. Thousands of hard-coded addresses and pointer tables
  in the data would all have to stay consistent.
- Full decompilations exist (Super Mario 64, Zelda), but they took large teams years.

### The middle ground: replace one function at a time

Keep Roland's binary and **rewrite only the functions we change in C**:
- The import table in `app1/payload/imports.h` lists the firmware functions and variables
  our payload uses. `app1/tools/symbols_tallfree.txt` is the start of that list.
- The firmware is redirected (one function pointer or branch) into our version, which may
  call Roland's original for the parts it doesn't change.
- Build = compile our C with `arm-none-eabi-gcc` (`-mcpu=cortex-m7 -mthumb -mfloat-abi=hard
  -mfpu=fpv5-d16`, matching the firmware's calling convention) + run the patch script.
  Rebuilding takes seconds.

This gives the useful part of recompiling (write normal C, change it, rebuild, flash) while
the untouched 99% keeps working because it's still Roland's code. As more subsystems are
understood, larger pieces can move into C. The looper will be built this way.

## Bricking: why it's unlikely, and what to watch for

- **The recovery path can't be overwritten.** The SHIFT+power updater lives in the boot code
  below flash `0x80000` and only erases/writes `0x80000`–`0x3FF000`. A crashing APP1 is
  fixed by flashing Roland's original from the SD card. Tallfree has relied on this repeatedly.
- **No signature check.** Patched APP1 images install without any checksum or signature work.
- Realistic failures and how to avoid them:
  - An update that didn't finish looks like a brick. Always confirm "UPDATE COMPLETE!" on
    screen before doing anything else.
  - A card with a damaged FAT can hang the unit. Use freshly formatted FAT32 cards, and check
    with `fsck.fat -n`.
  - Bus faults from touching unimplemented peripheral addresses. Access only named registers.

## Workflow for each change

1. **Understand.** Find the code in Ghidra. Write it up in `docs/app1/topics/<feature>.md`
   (key functions, data structures, open questions). Usually most of the work.
2. **Design the smallest patch.** Data over instructions; hook one place, not many.
3. **Implement** it in the patch script, with original-byte checks and a printout of every change.
4. **Check offline.** Disassemble the patched spots. Run any new C code under QEMU (Cortex-M7)
   where possible.
5. **Flash and test.** Install via SD card, confirm the update completed, and test on the
   device. Keep Roland's original APP1 on a card ready for recovery.

## Memory for new features

SDRAM is almost fully allocated. The biggest block is the **skip-back (resample) buffer**,
whose end is set at `0x83F7A424` (Tallfree shrank it to make room for its speech engine). It
holds about 30 s of audio output, but in practice only about 1/4 of it gets used, so shrinking
it is the obvious way to get memory for a looper. Tallfree shows how: every user of the buffer
computes its size from (end - start) at run time, so moving the end in the 7 places it's set
shrinks it consistently.

Our patches and Tallfree's both claim that memory, so they can't run on the same device as-is.

When we take it, it should go behind one small **custom audio storage** interface (allocate
named buffers, write/read frames, a budget fixed at build time) rather than each feature
carving out its own piece. Candidates: the smooth-loop crossfade (goal #7), a multitrack looper
(goal #2), caches of resampled notes.

## Tools

Installed; see [requirements.md](../requirements.md).
