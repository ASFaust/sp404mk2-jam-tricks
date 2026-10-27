#!/usr/bin/env python3
"""Rebuild the SP-404MKII APP1 firmware as an ELF file for Ghidra.

APP1 is a raw flash image (loaded at 0x60080000 on an NXP i.MX RT1060). At boot,
the ARM Compiler runtime (__scatterload) copies and decompresses parts of it into
ITCM, DTCM, OCRAM and external SDRAM. Most of the code only runs from those RAM
addresses, so importing the raw .bin at a single address gives a broken picture.

This script replays the scatter-load table offline and writes an ELF in which every
region sits at its runtime address, plus symbols for the startup code and the
vector table. See docs/app1/firmware-format.md for the details.

Usage:
    python app1/tools/build_elf.py resources/firmware/sp404mk2_sys_v552/SP404MKII_APP1.bin app1/build/app1.elf
"""

import struct
import sys

FLASH_BASE = 0x60080000
SCATTER_TABLE_PTR = 0x80  # file offset of the two words giving the table bounds
NUM_VECTORS = 256

# Cortex-M system exception names (vector index -> name)
SYSTEM_VECTORS = {
    1: "Reset_Handler",
    2: "NMI_Handler",
    3: "HardFault_Handler",
    4: "MemManage_Handler",
    5: "BusFault_Handler",
    6: "UsageFault_Handler",
    11: "SVC_Handler",
    12: "DebugMon_Handler",
    14: "PendSV_Handler",
    15: "SysTick_Handler",
}


def decompress(src, pos, out_size):
    """ARM Compiler RW-data decompressor (__decompress, LZ77 variant).

    Reimplemented from the routine at 0x60080088 in the firmware.
    Each token byte: low 3 bits = literal count + 1 (0 -> next byte),
    high 4 bits = match length (0 -> next byte), bit 3 = back-reference
    (else a run of zeros).
    """
    out = bytearray()
    while len(out) < out_size:
        tok = src[pos]; pos += 1
        lit = tok & 7
        if lit == 0:
            lit = src[pos]; pos += 1
        length = tok >> 4
        if length == 0:
            length = src[pos]; pos += 1
        for _ in range(lit - 1):
            out.append(src[pos]); pos += 1
        if tok & 8:
            off = src[pos]; pos += 1
            for _ in range(length + 2):
                out.append(out[-off])
        else:
            out.extend(b"\0" * length)
    if len(out) != out_size:
        raise ValueError(f"decompressed {len(out):#x} bytes, expected {out_size:#x}")
    return bytes(out), pos


def parse_scatter_table(img):
    """Return [(src_addr, dst_addr, size, handler_addr)] from the scatter-load table."""
    start_rel, end_rel = struct.unpack_from("<II", img, SCATTER_TABLE_PTR)
    start = SCATTER_TABLE_PTR + start_rel
    end = SCATTER_TABLE_PTR + end_rel
    return [struct.unpack_from("<4I", img, o) for o in range(start, end, 16)], start, end


def build_regions(img):
    entries, tbl_start, tbl_end = parse_scatter_table(img)
    # The three scatter-load handlers, identified by their code in the startup routine.
    h_copy = FLASH_BASE + 0xE4
    h_decomp = FLASH_BASE + 0x88
    h_zero = FLASH_BASE + 0x100

    regions = []  # (name, addr, data_or_None, size, executable)
    # Code that runs in place from flash: reset code, scatter-load runtime, the table.
    regions.append(("flash_boot", FLASH_BASE, img[:tbl_end], tbl_end, True))

    for src, dst, size, handler in entries:
        off = src - FLASH_BASE
        if handler == h_copy:
            data = img[off:off + size]
            kind = "copy"
        elif handler == h_decomp:
            data, _ = decompress(img, off, size)
            kind = "lz"
        elif handler == h_zero:
            data = None
            kind = "bss"
        else:
            raise ValueError(f"unknown scatter handler {handler:#x}")
        regions.append((region_name(dst, kind), dst, data, size, kind == "copy" and is_code(dst)))
        print(f"  {kind:4s} {src:#010x} -> {dst:#010x} size {size:#09x}  {regions[-1][0]}")
    return regions


def is_code(addr):
    return addr < 0x20000000 or 0x80000000 <= addr < 0x80245740


def region_name(addr, kind):
    if addr < 0x00080000:
        area = "itcm"
    elif 0x20000000 <= addr < 0x20200000:
        area = "dtcm"
    elif 0x20200000 <= addr < 0x20300000:
        area = "ocram"
    else:
        area = "sdram"
    suffix = {"copy": "", "lz": "_data", "bss": "_bss"}[kind]
    if area == "itcm" and addr == 0:
        return "itcm_vectors"
    if kind == "copy" and area == "sdram" and addr == 0x80000000:
        return "sdram_code"
    return f"{area}{suffix}_{addr:08x}"


def collect_symbols(img, regions):
    syms = []  # (name, addr, is_func)
    vec_off = 0x6EC  # flash copy of the vector table (copied to 0x0 at boot)
    vectors = struct.unpack_from(f"<{NUM_VECTORS}I", img, vec_off)
    default = max(set(vectors[16:]), key=vectors[16:].count)
    for i, v in enumerate(vectors[1:], start=1):
        if v == 0:
            continue
        if i in SYSTEM_VECTORS:
            name = SYSTEM_VECTORS[i]
        elif i >= 16 and v == default:
            continue
        elif i >= 16:
            name = f"IRQ{i - 16}_Handler"
        else:
            continue
        syms.append((name, v & ~1, True))
    syms.append(("Default_IRQ_Handler", default & ~1, True))
    syms += [
        ("__scatterload", FLASH_BASE + 0x54, True),
        ("__decompress", FLASH_BASE + 0x88, True),
        ("__scatterload_copy", FLASH_BASE + 0xE4, True),
        ("__scatterload_zeroinit", FLASH_BASE + 0x100, True),
        ("SystemInit_early", FLASH_BASE + 0x120, True),
        ("__scatterload_table", FLASH_BASE + 0x62C, False),
        ("__initial_sp", vectors[0], False),
    ]
    return syms


def write_elf(path, regions, symbols, entry):
    """Minimal ELF32 little-endian ARM writer: one PT_LOAD and one section per region."""
    shstr = bytearray(b"\0")
    strtab = bytearray(b"\0")

    def add_str(tab, s):
        o = len(tab); tab += s.encode() + b"\0"; return o

    ehsize, phentsize, shentsize = 52, 32, 40
    phnum = len(regions)
    data_off = ehsize + phnum * phentsize
    blobs, phdrs, shdrs = [], [], [(0,) * 10]
    cur = data_off
    for name, addr, data, size, exe in regions:
        filesz = len(data) if data is not None else 0
        flags = 4 | 2 | (1 if exe else 0)  # R W X
        phdrs.append(struct.pack("<8I", 1, cur, addr, addr, filesz, size, flags, 4))
        sh_type = 1 if data is not None else 8  # PROGBITS / NOBITS
        sh_flags = 2 | 1 | (4 if exe else 0)  # ALLOC WRITE EXECINSTR
        shdrs.append((add_str(shstr, "." + name), sh_type, sh_flags, addr, cur, size, 0, 0, 4, 0))
        if data is not None:
            blobs.append(data); cur += filesz

    # symbol table (index 0 is the null symbol)
    def section_of(addr):
        for i, (_, a, _, s, _) in enumerate(regions, start=1):
            if a <= addr < a + s:
                return i
        return 0xFFF1  # SHN_ABS

    symtab = bytearray(b"\0" * 16)
    for name, addr, is_func in sorted(symbols, key=lambda s: s[1]):
        info = (1 << 4) | (2 if is_func else 1)  # GLOBAL, FUNC/OBJECT
        symtab += struct.pack("<IIIBBH", add_str(strtab, name), addr | (1 if is_func else 0), 0, info, 0, section_of(addr))

    strtab_idx = len(shdrs) + 1
    for name, typ, body, link, entsize in ((".symtab", 2, symtab, strtab_idx, 16),
                                           (".strtab", 3, strtab, 0, 0)):
        shdrs.append((add_str(shstr, name), typ, 0, 0, cur, len(body), link, 1 if typ == 2 else 0, 4, entsize))
        blobs.append(bytes(body)); cur += len(body)
    shstr_name = add_str(shstr, ".shstrtab")
    shdrs.append((shstr_name, 3, 0, 0, cur, len(shstr), 0, 0, 1, 0))
    blobs.append(bytes(shstr)); cur += len(shstr)

    shoff = (cur + 3) & ~3
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\0" * 8
    ehdr = ident + struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, entry, ehsize, shoff,
                               0x05000400, ehsize, phentsize, phnum, shentsize,
                               len(shdrs), len(shdrs) - 1)
    with open(path, "wb") as f:
        f.write(ehdr)
        for p in phdrs:
            f.write(p)
        for b in blobs:
            f.write(b)
        f.write(b"\0" * (shoff - cur))
        for s in shdrs:
            f.write(struct.pack("<10I", *s))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    img = open(sys.argv[1], "rb").read()
    print("scatter-load regions:")
    regions = build_regions(img)
    symbols = collect_symbols(img, regions)
    write_elf(sys.argv[2], regions, symbols, entry=FLASH_BASE | 1)
    print(f"wrote {sys.argv[2]} ({len(regions)} regions, {len(symbols)} symbols)")


if __name__ == "__main__":
    main()
