#!/usr/bin/env python3
"""Quick look-ups in APP1's runtime memory, without opening Ghidra.

Rebuilds the memory layout the same way app1/tools/build_elf.py does (replaying the
scatter-load table), then answers small questions from the command line:

    fwmem.py words ADDR [N]     N 32-bit words from ADDR (default 16)
    fwmem.py dis ADDR [N]       disassemble N Thumb instructions from ADDR (default 20)
    fwmem.py str ADDR           the C string at ADDR
    fwmem.py find TEXT          every address where the ASCII string TEXT starts
    fwmem.py refs VALUE         every aligned word equal to VALUE (also VALUE|1),
                                i.e. literal pools, vtables and tables that point at it

Addresses are hex runtime addresses (0x80xxxxxx for the main code). As a library:
`mem = Memory()`, then `mem.read(addr, n)`, `mem.word(addr)`, `mem.cstr(addr)`.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_elf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app1/
REPO = os.path.dirname(ROOT)
APP1 = os.environ.get("SP404_APP1", os.path.join(REPO, "resources", "firmware", "sp404mk2_sys_v552", "SP404MKII_APP1.bin"))


class Memory:
    def __init__(self, path=APP1):
        with open(path, 'rb') as image:
            img = image.read()
        stdout, sys.stdout = sys.stdout, open(os.devnull, "w")  # build_regions prints
        try:
            regions = build_elf.build_regions(img)
        finally:
            sys.stdout.close()
            sys.stdout = stdout
        # (start, bytes); BSS regions are left out, they are all zero at boot
        self.regions = [(addr, data) for _, addr, data, _, _ in regions if data is not None]

    def read(self, addr, n):
        for start, data in self.regions:
            if start <= addr and addr + n <= start + len(data):
                return data[addr - start:addr - start + n]
        raise KeyError(f"{addr:#x} is not in an initialised region")

    def word(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def cstr(self, addr, limit=200):
        out = bytearray()
        while len(out) < limit:
            b = self.read(addr + len(out), 1)[0]
            if b == 0:
                break
            out.append(b)
        return out.decode("latin-1")

    def find_bytes(self, needle):
        for start, data in self.regions:
            i = data.find(needle)
            while i >= 0:
                yield start + i
                i = data.find(needle, i + 1)

    def find_words(self, values):
        values = set(values)
        for start, data in self.regions:
            for off in range(0, len(data) - 3, 4):
                if struct.unpack_from("<I", data, off)[0] in values:
                    yield start + off


def disassemble(mem, addr, count):
    from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs
    cs = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    code = mem.read(addr, count * 4)
    for n, ins in enumerate(cs.disasm(code, addr)):
        if n >= count:
            break
        yield ins


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    cmd, arg = argv[1], argv[2]
    mem = Memory()
    if cmd == "words":
        addr, n = int(arg, 16), int(argv[3]) if len(argv) > 3 else 16
        for i in range(n):
            print(f"{addr + 4 * i:08x}: {mem.word(addr + 4 * i):08x}")
    elif cmd == "dis":
        addr, n = int(arg, 16) & ~1, int(argv[3]) if len(argv) > 3 else 20
        for ins in disassemble(mem, addr, n):
            print(f"{ins.address:08x}: {ins.bytes.hex():10s} {ins.mnemonic:8s} {ins.op_str}")
    elif cmd == "str":
        print(repr(mem.cstr(int(arg, 16))))
    elif cmd == "find":
        for a in mem.find_bytes(arg.encode("latin-1")):
            print(f"{a:08x}")
    elif cmd == "refs":
        v = int(arg, 16) & ~1
        for a in mem.find_words([v, v | 1]):
            print(f"{a:08x}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
