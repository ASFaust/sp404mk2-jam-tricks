#!/usr/bin/env python3
"""Build the patched APP1 image from Roland's original.

Every patch names its runtime address, the original bytes and the new bytes. The original
bytes are checked before anything is changed, so a wrong address stops the build instead
of reaching the device. Each change is printed.

Patches that need our own code (app1/payload/) compile it with arm-none-eabi-gcc, append it to
the image in free flash at PAYLOAD_FLASH (copied to SDRAM at boot, see add_payload_copy), and
take addresses from its symbols. Patches in the same group are alternatives: at most one of
them can be chosen.

Usage:
    python app1/tools/make_image.py [--only ID[,ID...]] [output]
        default: all patches not marked experimental, output app1/build/SP404MKII_APP1.bin
    python app1/tools/make_image.py --list

Install: copy the output to a freshly formatted FAT32 card as SP404MKII_APP1.bin, hold SHIFT while powering on, press VALUE, and confirm
"UPDATE COMPLETE!" before anything else.
"""

import hashlib
import os
import struct
import subprocess
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app1/
REPO = os.path.dirname(ROOT)
ORIGINAL = os.environ.get("SP404_APP1", os.path.join(REPO, "resources", "firmware", "sp404mk2_sys_v552", "SP404MKII_APP1.bin"))
ORIGINAL_SHA256 = "4a3d67711e14dcc97d50249a4eee7dd6df0251a2f37757cbbe2556c233730d80"
IMAGE_NAME = "SP404MKII_APP1.bin"
PAYLOAD_DIR = os.path.join(ROOT, "payload")
PAYLOAD_BUILD = os.path.join(ROOT, "build", "payload")

# The main code region: copied unchanged from flash to SDRAM 0x80000000 at boot.
SDRAM_CODE = 0x80000000
SDRAM_CODE_FILE_OFFSET = 0x21AC0
SDRAM_CODE_SIZE = 0x245740

# ITCM code (0x400..0x1FC00), copied unchanged from the image at boot like the SDRAM code.
ITCM_CODE = 0x400
ITCM_CODE_FILE_OFFSET = 0xAF0
ITCM_CODE_SIZE = 0x1F800

# Our code is stored in flash after the end of APP1 (0x60307A00) and runs from SDRAM at
# PAYLOAD_RAM (app1/payload/payload.ld): Roland's start-up copies it there. Never run it from
# flash: the firmware erases its settings sector at run time with interrupts enabled
# (docs/app1/topics/payload-ram.md). The updater accepts images up to flash 0x3FF000.
FLASH_BASE = 0x60080000
PAYLOAD_FLASH = 0x60308000
PAYLOAD_RAM = 0x83FE0000
PAYLOAD_RAM_LIMIT = 0x83FF0000
IMAGE_LIMIT = 0x6037F000 + 0x80000      # flash 0x3FF000

# __scatterload (0x60080054) finds its table through two words relative to 0x60080080: start
# and limit. 12 entries of (source, destination, size, handler), no room after them, so the
# patched image gets a copy with one more entry in the padding before PAYLOAD_FLASH.
SCATTER_REL_BASE = 0x60080080
SCATTER_TABLE = 0x6008062C
SCATTER_ENTRIES = 12
SCATTER_COPY = 0x600800E4               # __scatterload_copy: 16-byte blocks, then words
SCATTER_TABLE_NEW = 0x60307F00

# This release of the patches. Shown after Roland's version on the device (hooks.S) and appended
# to every image, so the web patcher can refuse an image it already patched.
JAM_TRICKS_VERSION = "1.0"
MARKER = f"+jam-tricks-v{JAM_TRICKS_VERSION}".encode()

CFLAGS = ["-mcpu=cortex-m7", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv5-d16", "-O2",
          "-ffreestanding", "-fno-builtin", "-nostdlib", "-Wall", "-Wextra", "-Werror",
          f'-DJAM_TRICKS_VERSION="{JAM_TRICKS_VERSION}"']


@dataclass
class Patch:
    id: str
    title: str
    doc: str                 # docs/topics note with the details
    changes: object          # [(runtime address, original bytes, new bytes, what it does)],
                             # or a function(symbols) returning that list
    experimental: bool = False   # not applied by default
    group: str = None        # alternatives share a group; at most one per group
    warning: str = None      # shown to the user before choosing it
    payload: bool = False    # needs app1/payload/ compiled and appended
    diagnostic: bool = False # a tool for testing (meters, overlays), not a feature


def word(v):
    return struct.pack("<I", v)


def half(v):
    return struct.pack("<H", v)


def thumb_movw_movt(op, rd, imm16):
    """Encode a Thumb-2 MOVW (op 'w') or MOVT (op 't') of imm16 into register rd."""
    i, imm4, imm3, imm8 = (imm16 >> 11) & 1, imm16 >> 12, (imm16 >> 8) & 7, imm16 & 0xFF
    first = (0xF240 if op == "w" else 0xF2C0) | (i << 10) | imm4
    return half(first) + half((imm3 << 12) | (rd << 8) | imm8)


def mov32(rd, value):
    return thumb_movw_movt("w", rd, value & 0xFFFF) + thumb_movw_movt("t", rd, value >> 16)


def assemble_at(addr, source):
    """Assemble Thumb code to run at addr (branches to absolute addresses work as usual)."""
    os.makedirs(PAYLOAD_BUILD, exist_ok=True)
    base = os.path.join(PAYLOAD_BUILD, f"snippet_{addr:08x}")
    with open(base + ".S", "w") as f:
        f.write("        .syntax unified\n        .thumb\n        .text\n" + source + "\n")
    subprocess.run(["arm-none-eabi-as", "-mcpu=cortex-m7", "-mthumb", base + ".S", "-o", base + ".o"],
                   check=True)
    subprocess.run(["arm-none-eabi-ld", f"-Ttext={addr:#x}", "-e", f"{addr:#x}", base + ".o", "-o", base + ".elf"],
                   check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", base + ".elf", base + ".bin"],
                   check=True)
    return open(base + ".bin", "rb").read()


def pad_nops(code, length):
    """code followed by 16-bit NOPs up to length bytes."""
    if len(code) > length or (length - len(code)) % 2:
        raise ValueError(f"{len(code)} bytes of code don't fit in {length}")
    return code + b"\x00\xbf" * ((length - len(code)) // 2)


# The VARI cycle on the pitch/speed panel (SHIFT + VALUE wheel, FUN_800F8190): Roland steps
# VINYL -> Off -> Backing -> Ensemble -> VINYL with one block per target. The "-> Off" block
# at 0x800F822A becomes a generic "VARI = sb" block (two instructions change; its call to
# Param_Set stays where it is), and the old decision code plus the then unused "-> Backing"
# and "-> Ensemble" blocks (0x800F8268..0x800F82C3) hold a new dispatcher with Plus (3).
# Registers there: r6 = wheel direction, r7 = VINYL, r0 = VARI.
VARI_WHEEL_DISPATCH = """
        cmp     r7, #1
        beq     vinyl_on
        cmp     r6, #1
        blt     left
        cmp     r0, #3              @ right: Off -> Backing -> Ensemble -> Plus -> VINYL
        bge     to_vinyl
        adds    r0, #1
        b       set
left:   cmp     r0, #0              @ left: the reverse
        ble     to_vinyl
        subs    r0, #1
        b       set
vinyl_on:
        cmp     r6, #1
        ite     ge
        movge   r0, #0              @ VINYL -> Off (right) or -> Plus (left)
        movlt   r0, #3
set:    mov     r9, r0
        b.w     0x800F822A          @ the generic block: VINYL off, VARI = r9
to_vinyl:
        b.w     0x800F8324          @ Roland's "VINYL on, VARI Off" block
"""

# The name shown for VARI value 3 (Roland's unused label "TBD2"): exactly 4 characters,
# or fewer padded with NULs.
VARI4_LABEL = "Plus"


def vari4_stretch(sym):
    """VARI value 3: our time stretcher (app1/payload/tsx.c) for pads set to it."""
    label = VARI4_LABEL.encode("ascii")[:4].ljust(4, b"\0")
    return [
        (0x800D2E2E, mov32(12, 0x000140C5), mov32(12, sym["tsx_resampler_stereo"] | 1),
         "Resampler_Stereo thunk: 'movw/movt ip' 0x140C5 -> tsx_resampler_stereo (mode 3 ours, others Roland's)"),
        (0x800D2E38, mov32(12, 0x0001409B), mov32(12, sym["tsx_resampler_mono"] | 1),
         "Resampler_Mono thunk: 'movw/movt ip' 0x1409B -> tsx_resampler_mono"),
        (0x800E4392, b"\x06", b"\x04",
         "Voice_RenderSetup: 'and r2, #6' -> #4, so mode 3 fills the source ring to 60 % like Ensemble"),
        (0x800D37F0, half(0x011E), half((0x800D5784 - 0x800D3604) // 2),
         "Param_GetRange switch entry for 0xF6 (VARI): shared 0..2 case 0x800D3840 -> 0x800D5784"),
        (0x800D5784, bytes(4), bytes.fromhex("dff8b4f1"),
         "padding -> 'ldr.w pc, [pc, #0x1B4]' (jumps via the word at 0x800D593C)"),
        (0x800D593C, bytes(4), word(sym["range_vari4"] | 1),
         "padding -> address of range_vari4 (VARI 0..3)"),
        (0x800C63A6, mov32(0, 0x32444254), mov32(0, int.from_bytes(label, "little")),
         f"Param_FormatValue(0xF6, 3): label 'TBD2' -> '{VARI4_LABEL}'"),
        (0x800F8222, bytes.fromhex("012e20db"), pad_nops(assemble_at(0x800F8222, "b 0x800F8268"), 4),
         "VARI wheel: 'cmp r6, #1; blt' -> 'b 0x800F8268' (the new dispatcher)"),
        (0x800F823E, bytes.fromhex("4ff00009"), bytes.fromhex("aff30080"),
         "VARI wheel, '-> Off' block: 'mov.w r9, #0' -> nop.w (r9 = VARI from the dispatcher)"),
        (0x800F8248, bytes.fromhex("0022"), bytes.fromhex("4a46"),
         "VARI wheel, '-> Off' block: 'movs r2, #0' -> 'mov r2, r9'"),
        (0x800F8268, ORIGINAL_WHEEL, pad_nops(assemble_at(0x800F8268, VARI_WHEEL_DISPATCH), len(ORIGINAL_WHEEL)),
         "VARI wheel: old decisions and the '-> Backing'/'-> Ensemble' blocks -> new dispatcher with Plus"),
        (0x800DBDC4, VARI_RESTART_ORIGINAL,
         pad_nops(assemble_at(0x800DBDC4, VARI_RESTART_NEW.format(hook=sym["hook_vari_changed"] | 1)),
                  len(VARI_RESTART_ORIGINAL)),
         "PadStore_Set, pad param 0x70 with new VARI bits: stop + re-trigger the pad's voices -> "
         "hook_vari_changed (switch them in place, app1/payload/vari.c)"),
    ]


# PadStore_Set, pad param 0x70 whose VARI bits changed (r7 = pad, r6 = the new word, already
# stored): Roland marks the pad (FUN_800D6390) and restarts its playing voices from the start
# (FUN_800D7668). We switch them in place instead; the branch after it (to 0x800DC352) stays.
VARI_RESTART_ORIGINAL = bytes.fromhex("3846faf7e3fa41f600003946c8f25900fbf748fc")
VARI_RESTART_NEW = """
        mov     r0, r7
        mov     r1, r6
        movw    r3, #:lower16:{hook}
        movt    r3, #:upper16:{hook}
        blx     r3
"""


ORIGINAL_WHEEL = bytes.fromhex(  # 0x800F8268..0x800F82C3 in Roland's image
    "012f1bd0022808d00128dad0a0bb55e0022853d0012811d070bb0ffa8af7"
    "40f6d016c8f2e0267721002230463b46e6f73ffdf62101224ff001090fe0"
    "0ffa8af740f6d016c8f2e0267721002230463b46e6f72efdf62102224ff0"
    "0209")

VARI4_WARNING = ("Pads set to the new VARI value play like 'Backing' (with a longer splice) "
                 "on an SP-404MKII without this patch.")


# Pad_Trigger, chromatic notes (both Voice_Start paths): Roland sets the voice's PITCH to the
# chromatic note and FINE to 0, replacing the pad's own pitch/fine (pad parameter block at
# sp + 0x180, +0x34 PITCH, +0x38 FINE). Instead: PITCH = pad PITCH + note, FINE kept, so the
# pitch/speed panel's setting is the base (SPEED was never replaced). +0x4C of the slot keeps
# the plain note (Roland matches a key pressed again with it).
PITCH_BASE_ORIGINAL = bytes.fromhex("6d9000216e91c8f84c00")
PITCH_BASE_NEW = """
        ldr     r1, [sp, #0x1b4]    @ the pad's PITCH
        add     r1, r0              @ + the chromatic note
        str     r1, [sp, #0x1b4]    @ (FINE at +0x1b8 stays the pad's)
        str.w   r0, [r8, #0x4c]
"""


# The chromatic page's key and pad-down methods go to app1/payload/chrom.c for both scale-pads and
# chrom-voices; each patch switches its part on with a flag byte in the payload.
def chrom_page_hooks(sym):
    return [
        (0x802214CC, word(0x801120D9), word(sym["hook_chrom_on_key"] | 1),
         "chromatic page vtable slot 0xD8 (ChromPage_OnKey) -> hook_chrom_on_key"),
        (0x802214F0, word(0x80111B39), word(sym["hook_chrom_pad_down"] | 1),
         "chromatic page vtable slot 0xFC (ChromPage_OnPadDown) -> hook_chrom_pad_down"),
    ]


# Pad param 0x70 validation (FUN_8001A570, applied to every runtime write): Roland zeroes the
# whole word when it's above 0x3F. chrom-voices keeps the POLY voice count in bits 6-8, so the
# limit becomes 0x1FF. (Project loading copies the word unchecked.)
PAD70_CHECK_NEW = """
        lsrs    r0, r2, #9          @ bits above 8 set (or negative)?
        it      ne
        movne   r2, #0              @ then 0, as Roland did above 0x3F
"""

CHROM_VOICES_WARNING = ("On an SP-404MKII without this patch, editing VARI or the chromatic mode of a "
                        "pad set to POLY 2-8 resets that pad's VARI and mode to their defaults.")


def chrom_voices(sym):
    """Chromatic POLY voice limit (REMAIN + wheel) and the pad's pitch as the base."""
    changes = chrom_page_hooks(sym) + [
        (sym["chrom_voices"], b"\0", b"\1", "payload flag chrom_voices: REMAIN + wheel, voice limit on"),
        (0x8001A8D4, bytes.fromhex("3f2a88bf0022"), assemble_at(0x8001A8D4, PAD70_CHECK_NEW),
         "pad param 0x70 validation: 'cmp r2, #0x3f; it hi; movhi r2, #0' -> zero only above 0x1FF"),
        (0x800A2C98, bytes.fromhex("b0b50546006800 28".replace(" ", "")),
         bytes.fromhex("dff800f0") + word(sym["hook_voice_set_param"] | 1),
         "Voice_SetParam: first 4 instructions -> 'ldr.w pc, [pc]' + hook_voice_set_param "
         "(chromatic notes get the pad's PITCH/FINE; voice_set_param_orig runs the 4 and returns)"),
    ]
    for addr in (0x800D71BA, 0x800D72CE):
        changes.append((addr, PITCH_BASE_ORIGINAL, assemble_at(addr, PITCH_BASE_NEW),
                        "Pad_Trigger, chromatic note: PITCH = pad PITCH + note, pad FINE kept"))
    return changes


def scale_pads(sym):
    """In-key pads: hooks into the chromatic page, plus a chromatic-only +/-24 range."""
    return chrom_page_hooks(sym) + [
        (sym["chrom_layout"], b"\0", b"\1", "payload flag chrom_layout: in-key pad tables on"),
        (0x8022150C, word(0x80111DF9), word(sym["hook_chrom_build"] | 1),
         "chromatic page vtable slot 0x118 (layout build) -> hook_chrom_build"),
        (0x800D373A, half(0x0162), half(0xCFA0),
         "Param_GetRange switch entry for 0x9B: case 0x800D38C8 (shared with pad PITCH) -> 0x800ED544"),
        (0x800ED544, bytes(4), bytes.fromhex("dff80cf0"),
         "padding -> 'ldr.w pc, [pc, #0xC]' (jumps via the word at 0x800ED554)"),
        (0x800ED554, bytes(4), word(sym["range_chrom_24"] | 1),
         "padding -> address of range_chrom_24 (chromatic range -24..24)"),
    ]


def cpu_meter(sym):
    """CPU load on the top pixel row, playing voices on the bottom row (app1/payload/meter.c)."""
    return [
        (0x0000E2D0, bytes.fromhex("f02010b5f6f785fa"), bytes.fromhex("dff800f0") + word(sym["hook_idle"] | 1),
         "Kernel_IdleWait (ITCM): 'movs r0, #0xf0; push; bl' -> 'ldr.w pc, [pc]' + address of hook_idle"),
        (0x8021D018, word(0x80072CF1), word(sym["hook_panel_event"] | 1),
         "panel task vtable slot 0x20 (event handler) -> hook_panel_event (meter on its tick)"),
    ]


# Custom pages (app1/payload/page.c): the record of page id 93 (16 bytes per id at 0x801F8F74)
# points at a word holding its handler. Pointing it at cpage_host_slot sends that id's
# messages to cpage_dispatch, which forwards them to Roland's handler (0x80064751) unless
# one of our pages is open. Needed by every patch with a page of its own.
CPAGE_HOST_ID = 93


def version_mark(sym):
    """'+jam-tricks-v1.0' after Roland's version on both screens that show it (hooks.S). Part
    of every patch with a payload; patches without one have no code to put it there."""
    return [
        (0x8014B13E, bytes.fromhex("47f220606d46c8f22e00"),
         bytes.fromhex("dff804f000bf") + word(sym["hook_version_text"] | 1),
         "'Version: %s' (FUN_8014b120): 'movw r0; mov r5, sp; movt r0' -> 'ldr.w pc, [pc, #4]' + "
         "nop + address of hook_version_text (Jam Tricks version after Roland's)"),
        (0x800F7416, bytes.fromhex("47f2206146f25002c8f2"),
         bytes.fromhex("dff804f000bf") + word(sym["hook_app1_line"] | 1),
         "'APP1: %s (%s)' (FUN_800f72d8): 'movw r1; movw r2; movt r1' -> 'ldr.w pc, [pc, #4]' + "
         "nop + address of hook_app1_line (Jam Tricks version after Roland's)"),
    ]


def patch_changes(p, sym):
    """The changes patch p makes, with the version mark if it has a payload"""
    changes = p.changes(sym) if callable(p.changes) else p.changes
    return changes + version_mark(sym) if p.payload else changes


def custom_pages(sym):
    return [
        (0x801F8F74 + 16 * CPAGE_HOST_ID, word(0x8023B58C), word(sym["cpage_host_slot"]),
         f"page record {CPAGE_HOST_ID}: handler slot 0x8023B58C -> cpage_host_slot (custom pages)"),
    ]


def tuner(sym):
    """BANK A + BANK B on the top screen opens the tuner page (app1/payload/tuner.c)."""
    return custom_pages(sym) + [
        (0x8022CE74, word(0x801839B9), word(sym["hook_top_key_down"] | 1),
         "top screen vtable slot 0x18 (key down) -> hook_top_key_down (BANK A + B)"),
        (0x0001CA80, bytes.fromhex("f0b581b0044640f6d010c8f2e020"),
         bytes.fromhex("dff800f0") + word(sym["hook_audio_rx"] | 1) + bytes.fromhex("00bf00bf00bf"),
         "Audio_RxHandler (ITCM): 'push; sub sp; mov r4, r0; movw/movt r0, g_param_store' -> "
         "'ldr.w pc, [pc]' + address of hook_audio_rx + nops (receive word 0 into the tuner's "
         "ring; audio_rx_orig does the same and continues at 0x1CA8E)"),
    ]



WIDE_PITCH = [
    (0x800D38CA, b"\x0b", b"\x17", "Param_GetRange(0x73 PITCH): min 'mvn r0, #0xb' (-12) -> #0x17 (-24)"),
    (0x800D38CE, b"\x0c", b"\x18", "Param_GetRange(0x73 PITCH): max 'movs r0, #0xc' (12) -> #0x18 (24)"),
]

INCOMPATIBLE = ("Projects saved with a pad PITCH beyond +/-12 may not load correctly on an "
                "SP-404MKII without this patch.")

PATCHES = [
    Patch("no-efx-timeout", "Effect display stays open until EXIT",
          "docs/app1/topics/effects-timeout.md",
          [(0x801467D2, b"\x32", b"\x00",
            "EfxEdit_Update: 'sub.w r1, r0, #0x32' becomes #0, countdown never runs out")]),
    Patch("scale-pads", "Scale pads: all 16 chromatic pads play notes of the scale (range +/-24)",
          "docs/app1/topics/chromatic-mode.md", scale_pads,
          experimental=True, group="chromatic", payload=True),
    Patch("scale-pads-wide", "Scale pads + wide pitch: also every pad's PITCH goes to +/-24",
          "docs/app1/topics/chromatic-mode.md", lambda sym: scale_pads(sym) + WIDE_PITCH,
          experimental=True, group="chromatic", payload=True, warning=INCOMPATIBLE),
    Patch("chrom-voices", "Chromatic voices: LEGATO/MONO/POLY 2-8/POLY inf with REMAIN + wheel; "
          "pitch/speed panel as the base pitch",
          "docs/app1/topics/chromatic-mode.md", chrom_voices, experimental=True, payload=True,
          warning=CHROM_VOICES_WARNING),
    Patch("cpu-meter", "CPU meter: load on the top pixel row, playing voices on the bottom row",
          "docs/app1/topics/cpu-meter.md", cpu_meter, experimental=True, payload=True, diagnostic=True),
    Patch("vari4-stretch", "Time stretch: a 4th VARI option with a better stretcher",
          "docs/app1/topics/time-stretch.md", vari4_stretch,
          experimental=True, payload=True, warning=VARI4_WARNING),
    Patch("tuner", "Tuner: BANK A + BANK B shows the pitch of the mix",
          "docs/app1/topics/tuner.md", tuner, experimental=True, group="tuner", payload=True),
    Patch("tuner-probes", "Tuner + probes: VALUE press on the tuner cycles input probe, sample probe",
          "docs/app1/topics/tuner.md",
          lambda sym: tuner(sym) + [(sym["tuner_probes"], b"\0", b"\1",
                                     "payload flag tuner_probes: VALUE press cycles the probe views")],
          experimental=True, group="tuner", payload=True, diagnostic=True),
]


def file_offset(addr):
    if PAYLOAD_FLASH <= addr < IMAGE_LIMIT:     # our appended code and data
        return addr - FLASH_BASE
    if PAYLOAD_RAM <= addr < PAYLOAD_RAM_LIMIT:  # the same, by its run-time address
        return addr - PAYLOAD_RAM + PAYLOAD_FLASH - FLASH_BASE
    if ITCM_CODE <= addr < ITCM_CODE + ITCM_CODE_SIZE:
        return addr - ITCM_CODE + ITCM_CODE_FILE_OFFSET
    if not SDRAM_CODE <= addr < SDRAM_CODE + SDRAM_CODE_SIZE:
        raise ValueError(f"{addr:#x} is not in the payload, ITCM or SDRAM code region")
    return addr - SDRAM_CODE + SDRAM_CODE_FILE_OFFSET


def build_payload(log):
    """Compile app1/payload/ for the SP. Returns (bytes to put at PAYLOAD_FLASH, {symbol: address})."""
    os.makedirs(PAYLOAD_BUILD, exist_ok=True)
    elf = os.path.join(PAYLOAD_BUILD, "payload.elf")
    objs = []
    for src in sorted(os.listdir(PAYLOAD_DIR)):
        if src.endswith((".c", ".S")) and not src.startswith("test_"):
            obj = os.path.join(PAYLOAD_BUILD, src + ".o")
            subprocess.run(["arm-none-eabi-gcc", *CFLAGS, "-c", os.path.join(PAYLOAD_DIR, src),
                            "-o", obj], check=True)
            objs.append(obj)
    subprocess.run(["arm-none-eabi-ld", "-T", os.path.join(PAYLOAD_DIR, "payload.ld"), *objs,
                    "-o", elf], check=True)
    bin_path = os.path.join(PAYLOAD_BUILD, "payload.bin")
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".payload", elf, bin_path],
                   check=True)
    syms = {}
    nm = subprocess.run(["arm-none-eabi-nm", elf], capture_output=True, text=True, check=True)
    for line in nm.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    data = open(bin_path, "rb").read()
    if syms.get("payload_ram_start") != PAYLOAD_RAM or syms["payload_ram_end"] != PAYLOAD_RAM + len(data):
        raise ValueError("payload.ld and make_image.py disagree about PAYLOAD_RAM")
    check_imports_only(elf, data, syms)
    log(f"payload: {len(data)} bytes at {PAYLOAD_FLASH:#x}, run from {PAYLOAD_RAM:#x}, from {', '.join(os.path.basename(o)[:-2] for o in objs)}")
    return data, syms


# Roland's memory that the payload may reach only through its import table (imports.h), so
# the web patcher can move every address: SDRAM below the payload, and DTCM/OCRAM.
ROLAND_RANGES = ((0x80000000, PAYLOAD_RAM), (0x20000000, 0x202C0000))


def movw_movt_imm(h1, h2):
    return ((h1 & 0xF) << 12) | (((h1 >> 10) & 1) << 11) | (((h2 >> 12) & 7) << 8) | (h2 & 0xFF)


def check_imports_only(elf, data, syms):
    """Raise if payload code outside the import table holds a Roland address, as a data word
    (literal pool, table) or a movw/movt pair. The ELF's mapping symbols ($t code, $d data)
    say which bytes are which; string literals (from payload_strings on) aren't checked.
    ITCM addresses
    are too close to ordinary numbers to check this way."""
    def roland(v):
        return any(lo <= v < hi for lo, hi in ROLAND_RANGES)
    nm = subprocess.run(["arm-none-eabi-nm", "-n", "--special-syms", elf], capture_output=True,
                        text=True, check=True)
    marks, owners = [], []          # (address, '$t' or '$d'), (address, symbol)
    for line in nm.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or not PAYLOAD_RAM <= int(parts[0], 16) < PAYLOAD_RAM + len(data):
            continue
        addr, name = int(parts[0], 16), parts[2]
        if name[:2] in ("$t", "$d"):
            marks.append((addr, name[:2]))
        elif not name.startswith(("imp_", "$")):
            owners.append((addr, name))
    table_end = syms["roland_imports"] + 8 + 4 * struct.unpack_from(
        "<I", data, syms["roland_imports"] - PAYLOAD_RAM + 4)[0]

    def owner(addr):
        return max(((a, n) for a, n in owners if a <= addr), default=(0, None))[1]

    found = []
    ends = [a for a, _ in marks[1:]] + [PAYLOAD_RAM + len(data)]
    for (start, kind), end in zip(marks, ends):
        off, stop = max(start, table_end) - PAYLOAD_RAM, min(end, syms["payload_strings"]) - PAYLOAD_RAM
        if kind == "$d":
            for o in range((off + 3) & ~3, stop - 3, 4):
                v = struct.unpack_from("<I", data, o)[0]
                if roland(v):
                    found.append((v, PAYLOAD_RAM + o))
            continue
        movw = {}                   # register -> (low half, offset of the movw)
        while off + 2 <= stop:
            h1 = struct.unpack_from("<H", data, off)[0]
            if h1 >> 11 not in (0x1D, 0x1E, 0x1F):
                off += 2
                continue
            h2 = struct.unpack_from("<H", data, off + 2)[0]
            rd = h2 >> 8 & 0xF
            if (h1 & 0xFBF0) == 0xF240 and not h2 & 0x8000:
                movw[rd] = (movw_movt_imm(h1, h2), off)
            elif (h1 & 0xFBF0) == 0xF2C0 and not h2 & 0x8000 and rd in movw and off - movw[rd][1] <= 16:
                v = movw_movt_imm(h1, h2) << 16 | movw[rd][0]
                if roland(v):
                    found.append((v, PAYLOAD_RAM + off))
            off += 4
    found = [f"{v:#010x} at {a:#x} ({owner(a)})" for v, a in found]
    if found:
        raise ValueError("payload holds Roland addresses outside its import table "
                         "(add them to app1/payload/imports.h): " + "; ".join(found))


def add_payload_copy(img, size, log):
    """Make Roland's start-up copy the payload from flash to PAYLOAD_RAM: a copy of the
    scatter-load table plus the payload entry, and __scatterload pointed at it."""
    def at(addr):
        return addr - FLASH_BASE
    rel = struct.unpack_from("<2I", img, at(SCATTER_REL_BASE))
    table_end = SCATTER_TABLE + 16 * SCATTER_ENTRIES
    if rel != (SCATTER_TABLE - SCATTER_REL_BASE, table_end - SCATTER_REL_BASE):
        raise ValueError(f"scatter-load table words at {SCATTER_REL_BASE:#x} aren't Roland's")
    if size % 16 or PAYLOAD_RAM + size > PAYLOAD_RAM_LIMIT:
        raise ValueError(f"payload size {size:#x} isn't a multiple of 16 or doesn't fit in RAM")
    table = bytes(img[at(SCATTER_TABLE):at(table_end)])
    table += struct.pack("<4I", PAYLOAD_FLASH, PAYLOAD_RAM, size, SCATTER_COPY)
    new = at(SCATTER_TABLE_NEW)
    if img[new:new + len(table)] != b"\xff" * len(table) or SCATTER_TABLE_NEW + len(table) > PAYLOAD_FLASH:
        raise ValueError(f"no free flash for the scatter-load table at {SCATTER_TABLE_NEW:#x}")
    img[new:new + len(table)] = table
    struct.pack_into("<2I", img, at(SCATTER_REL_BASE), SCATTER_TABLE_NEW - SCATTER_REL_BASE,
                     SCATTER_TABLE_NEW + len(table) - SCATTER_REL_BASE)
    log(f"start-up: scatter-load table moved to {SCATTER_TABLE_NEW:#x} with "
        f"1 extra entry: "
        f"copy {size:#x} bytes {PAYLOAD_FLASH:#x} -> {PAYLOAD_RAM:#x}")


def select(ids):
    selected = [p for p in PATCHES if (not p.experimental if ids is None else p.id in ids)]
    unknown = set(ids or ()) - {p.id for p in PATCHES}
    if unknown:
        raise ValueError(f"unknown patch id(s): {', '.join(sorted(unknown))}")
    groups = {}
    for p in selected:
        if p.group:
            groups.setdefault(p.group, []).append(p.id)
    for g, members in groups.items():
        if len(members) > 1:
            raise ValueError(f"choose only one of {', '.join(members)} (group {g})")
    return selected


def build(ids=None, log=print):
    """Return the patched image as bytes. ids: patch ids to apply (None = all non-experimental).
    Raises ValueError if the original isn't v5.52 or an original byte doesn't match."""
    with open(ORIGINAL, 'rb') as original:
        img = bytearray(original.read())
    if hashlib.sha256(img).hexdigest() != ORIGINAL_SHA256:
        raise ValueError(f"{ORIGINAL} is not Roland's original v5.52")
    selected = select(ids)
    syms = {}
    if any(p.payload for p in selected):
        payload, syms = build_payload(log)
        start = PAYLOAD_FLASH - FLASH_BASE
        if len(img) > start or FLASH_BASE + start + len(payload) > IMAGE_LIMIT:
            raise ValueError("payload doesn't fit between the end of APP1 and the image limit")
        img += b"\xff" * (start - len(img)) + payload
        add_payload_copy(img, len(payload), log)
    applied = {}                        # addr -> (new bytes, patch id): shared changes
    for p in selected:
        log(f"[{p.id}] {p.title}")
        if p.warning:
            log(f"  WARNING: {p.warning}")
        for addr, old, new, why in patch_changes(p, syms):
            off = file_offset(addr)
            found = bytes(img[off:off + len(old)])
            if len(old) != len(new):
                raise ValueError(f"{addr:#x}: new bytes ({len(new)}) differ in length from the original ({len(old)})")
            if addr in applied and applied[addr][0] == new and found == new:
                log(f"  {addr:#010x}: already applied by [{applied[addr][1]}]  {why}")
                continue
            if found != old:
                raise ValueError(f"{addr:#x}: expected {old.hex()}, found {found.hex()}; not patching")
            img[off:off + len(new)] = new
            applied[addr] = (new, p.id)
            log(f"  {addr:#010x} (file {off:#08x}): {old.hex()} -> {new.hex()}  {why}")
    log(f"{len(selected)} patch(es) applied, Jam Tricks v{JAM_TRICKS_VERSION}")
    return bytes(img) + MARKER


def main(argv):
    args = argv[1:]
    if args and args[0] == "--list":
        for p in PATCHES:
            tags = [t for t in (p.group and f"group {p.group}", p.experimental and "not default",
                                p.diagnostic and "diagnostic") if t]
            print(f"{p.id:16s} {p.title}{'  [' + ', '.join(tags) + ']' if tags else ''}  ({p.doc})")
            if p.warning:
                print(f"{'':16s} WARNING: {p.warning}")
        return
    ids = None
    if args and args[0] == "--only":
        ids = set(args[1].split(","))
        args = args[2:]
    out = args[0] if args else os.path.join(ROOT, "build", IMAGE_NAME)
    try:
        img = build(ids)
    except (ValueError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "wb").write(img)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
