#!/usr/bin/env python3
"""Rules that find make_image.py's Roland addresses in other APP1 versions (web patcher).

The web patcher accepts v5.52 by its SHA-256 and writes fixed bytes. For other versions it has
to find every address itself: each patch site and each import the payload uses
(app1/payload/imports.h). This module derives the rules from v5.52; export_web.py writes them
into patches.json and web/patcher.js (resolve) follows them. docs/app1/topics/web-patcher.md.

Anchors name an address and say how to find it:
  find    byte patterns cut from v5.52 (hex, ?? = any byte), each unique in v5.52. A match
          gives the address one of four ways (how): 'at' the matched position, the 'word'
          stored there, the value a 'movw'/'movt' pair there builds, or the target of a 'bl'
          there; then + add. Every pattern that matches exactly once must give the same
          address, and at least one must match.
  from    another anchor + add (a place inside a found function).
  wordof  the address of the only aligned word in Roland's code regions that holds another
          anchor + add (a vtable slot).
  load    the word stored at another anchor (a handler slot's handler).

Patterns are masked where releases differ although the code is the same: bl and b.w offsets,
address-like literal words, movw/movt pairs that build an address. Each window grows until it
is unique. A load or store that adds a field offset to such a pair stays exact, so a changed
struct layout makes the pattern fail instead of giving a wrong address.

Changes become templates {at, old, new}: lists of parts, each bytes (hex) or an encoding of a
resolved address (word, mov32, tbh switch entry, ldrpc 'ldr.w pc, [pc, #n]'). Old bytes may
hold ?? where releases differ and the bytes get replaced anyway. export_web.py checks that the
templates reproduce make_image.py's v5.52 changes byte for byte.
"""

import os
import re
import struct
import subprocess

import make_image as mi

IMPORTS_H = os.path.join(mi.PAYLOAD_DIR, "imports.h")

# Roland's code as copied at boot: (runtime address, file offset, size)
REGIONS = ((mi.ITCM_CODE, mi.ITCM_CODE_FILE_OFFSET, mi.ITCM_CODE_SIZE),
           (mi.SDRAM_CODE, mi.SDRAM_CODE_FILE_OFFSET, mi.SDRAM_CODE_SIZE))

PAIR_SPAN = 32          # bytes from a movw to the movt completing it (Roland's compiler interleaves)
FIELD_SPAN = 40        # bytes after the movt in which a load or store may use the field
MIN_WINDOW = 12
MAX_WINDOW = 160
MIN_FIXED = 10          # bytes a pattern must require before it is searched for
CANDIDATES = 2          # patterns per way of finding (at the address, from references)


def addr_like(v):
    return 0x20000000 <= v < 0x20300000 or 0x60000000 <= v < 0x60400000 or 0x80000000 <= v < 0x84000000


def file_offset(addr):
    for rt, fo, size in REGIONS:
        if rt <= addr < rt + size:
            return addr - rt + fo
    return None


def runtime(off):
    for rt, fo, size in REGIONS:
        if fo <= off < fo + size:
            return off - fo + rt
    raise ValueError(f"file offset {off:#x} is not in Roland's code")


def is32(h1):
    return h1 >> 11 in (0x1D, 0x1E, 0x1F)


def is_branch(h1, h2):
    """bl, or an unconditional b.w (offsets change when the target moves)"""
    return (h1 & 0xF800) == 0xF000 and (h2 & 0xD000) in (0xD000, 0x9000)


def bl_target(h1, h2, pc):
    s = h1 >> 10 & 1
    i1, i2 = 1 ^ (h2 >> 13 & 1) ^ s, 1 ^ (h2 >> 11 & 1) ^ s
    imm = s << 24 | i1 << 23 | i2 << 22 | (h1 & 0x3FF) << 12 | (h2 & 0x7FF) << 1
    return pc + 4 + imm - (s << 25)


def mem_access(h1, h2):
    """(base register, offset, length in bytes) of a load or store with an immediate offset"""
    if not is32(h1):
        top = h1 >> 11
        scale = {0x0C: 4, 0x0D: 4, 0x0E: 1, 0x0F: 1, 0x10: 2, 0x11: 2}.get(top)
        if scale:
            return h1 >> 3 & 7, (h1 >> 6 & 31) * scale, 2
        return None
    if (h1 & 0xFE00) == 0xF800 and (h1 & 0xF) != 15:
        if h1 & 0x80:
            return h1 & 0xF, h2 & 0xFFF, 4
        if h2 & 0x800 and (h2 & 0x500) == 0x400:     # P set, W clear: plain offset
            return h1 & 0xF, (h2 & 0xFF) * (1 if h2 & 0x200 else -1), 4
    if (h1 & 0xFE40) == 0xE840 and h1 & 0x100 and not h1 & 0x20 and (h1 & 0xF) != 15:  # ldrd/strd
        return h1 & 0xF, (h2 & 0xFF) * 4 * (1 if h1 & 0x80 else -1), 4
    return None


class Firmware:
    """v5.52 with the facts the rules are made from: mask, references, branches"""

    def __init__(self, img):
        self.img = img
        self.fixed = bytearray(len(img))         # 1 where a pattern may require the byte
        self.words, self.pairs, self.bls = {}, {}, {}   # value -> [file offsets]
        self.movt_of = {}                        # movw offset -> movt offset
        for _, fo, size in REGIONS:
            self.fixed[fo:fo + size] = b"\1" * size
            self._scan(fo, size)

    def _scan(self, fo, size):
        img, fixed = self.img, self.fixed
        for o in range(fo, fo + size, 4):
            v = struct.unpack_from("<I", img, o)[0]
            self.words.setdefault(v, []).append(o)
            if addr_like(v):
                fixed[o:o + 4] = bytes(4)
        movw, o = {}, fo
        while o + 4 <= fo + size:
            h1, h2 = struct.unpack_from("<2H", img, o)
            if not is32(h1):
                o += 2
                continue
            rd = h2 >> 8 & 0xF
            if is_branch(h1, h2):
                fixed[o:o + 4] = bytes(4)
                if h2 & 0x4000:
                    self.bls.setdefault(bl_target(h1, h2, runtime(o)) | 1, []).append(o)
            elif (h1 & 0xFBF0) == 0xF240 and not h2 & 0x8000:
                movw[rd] = (mi.movw_movt_imm(h1, h2), o)
            elif (h1 & 0xFBF0) == 0xF2C0 and not h2 & 0x8000 and rd in movw and o - movw[rd][1] <= PAIR_SPAN:
                lo, w = movw.pop(rd)
                v = mi.movw_movt_imm(h1, h2) << 16 | lo
                self.pairs.setdefault(v, []).append(w)
                self.movt_of[w] = o
                if addr_like(v) or 0x10000 <= v < 0x20000:
                    fixed[w:w + 4] = fixed[o:o + 4] = bytes(4)
            o += 4

    def half(self, o):
        return struct.unpack_from("<H", self.img, o)[0]

    def window(self, p, lo, hi, force_any=(), force_fixed=()):
        """Pattern for img[p+lo:p+hi] as [(byte, required)]"""
        out = []
        for i in range(lo, hi):
            req = bool(self.fixed[p + i]) and i not in force_any or i in force_fixed
            out.append((self.img[p + i], req))
        return out

    def unique(self, p, span, force_any=(), force_fixed=(), shape=(1, 1)):
        """The smallest window around [p, p+span) with exactly one match in the image: (lo, hi)"""
        before, after = shape
        lo, hi = -MIN_WINDOW // 2 * before // max(shape) & ~1, span + MIN_WINDOW // 2 * after // max(shape)
        hits = None
        while hi - lo - span <= MAX_WINDOW:
            if p + lo >= 0 and p + hi <= len(self.img):
                pat = self.window(p, lo, hi, force_any, force_fixed)
                if sum(req for _, req in pat) < MIN_FIXED:
                    pass                            # mostly pointers so far (a table): grow first
                elif hits is None:
                    hits = self.search(pat, lo)
                else:
                    hits = [q for q in hits if self.matches(pat, q + lo)]
                if hits == [p]:
                    return lo, hi
                if hits is not None and p not in hits:
                    return None
                if sum(req for _, req in pat) < (hi - lo) // 4 and hi - lo - span > 64:
                    return None                     # a table of pointers: hopeless
            lo -= 2 * before
            hi += 2 * after
        return None

    def search(self, pat, lo):
        """Every q such that the pattern (which starts at q + lo) matches"""
        rx = re.compile(b"(?=" + b"".join(re.escape(bytes([b])) if req else b"." for b, req in pat) + b")",
                        re.DOTALL)
        return [m.start() - lo for m in rx.finditer(self.img)]

    def matches(self, pat, start):
        if start < 0 or start + len(pat) > len(self.img):
            return False
        img = self.img
        return all(not req or img[start + i] == b for i, (b, req) in enumerate(pat))

    def candidate(self, p, span, how, add=0, force_any=(), force_fixed=(), shape=(1, 1), **extra):
        w = self.unique(p, span, force_any, force_fixed, shape)
        if not w:
            return None
        lo, hi = w
        pat = self.window(p, lo, hi, force_any, force_fixed)
        while not pat[0][1]:            # no leading or trailing wildcards
            pat, lo = pat[1:], lo + 1
        while not pat[-1][1]:
            pat = pat[:-1]
        text = "".join(f"{b:02x}" if req else "??" for b, req in pat)
        return {"how": how, "p": text, "k": -lo, **{k: v - p - lo for k, v in extra.items()}, "add": add}


def spread(offsets, n):
    """Up to n offsets from the list, far apart (different functions)"""
    chosen = []
    for o in sorted(offsets, key=lambda o: (o * 2654435761) % 2 ** 32):    # a fixed shuffle
        if all(abs(o - c) > 0x400 for c in chosen):
            chosen.append(o)
    return chosen[:n * 3]


def find_rules(fw, value, span=2, name="", refer=0):
    """'find' candidates for an address: at it (if it is in the image) and at code referring
    to it (or to it + refer). Raises if there is none."""
    out = []
    at = file_offset(value & ~1)
    if refer:
        return [dict(c, add=c["add"] - refer) for c in find_rules(fw, value + refer, 0, name)
                if c["how"] != "at"] or find_rules(fw, value, span, name)
    if at is not None:
        for shape in ((1, 1), (1, 4)):
            c = fw.candidate(at, span, "at", value - runtime(at), shape=shape)
            if c and c not in out:
                out.append(c)
    refs = []
    for o in spread(fw.bls.get(value, []), CANDIDATES):
        refs.append(fw.candidate(o, 4, "bl", 1, force_any=range(4)))
    for o in spread(fw.pairs.get(value, []), CANDIDATES):
        t = fw.movt_of[o]
        refs.append(fw.candidate(o, t + 4 - o, "movw", force_any=(0, 1, 2, 3, t - o, t - o + 1, t - o + 2, t - o + 3),
                                 k2=t))
    for o in spread(fw.words.get(value, []), CANDIDATES):
        refs.append(fw.candidate(o, 4, "word", force_any=range(4)))
    refs = [c for c in refs if c][:CANDIDATES]
    if not refs and at is None:
        refs = field_rules(fw, value)
    out += refs
    if not out:
        raise ValueError(f"no unique pattern finds {name or hex(value)} ({value:#x}); add a rule in anchors.py")
    return out


def field_rules(fw, value):
    """Patterns at a movw/movt pair building a base, followed by a load or store at
    base + (value - base) through the same register."""
    uses = {}                   # movw offset -> (movt offset, offset and length of the access)
    for base, offsets in fw.pairs.items():
        d = value - base
        if not -0x100 <= d < 0x1000 or not d:
            continue
        for o in offsets:
            t = fw.movt_of[o]
            rd = fw.half(t + 2) >> 8 & 0xF
            q = t + 4
            while q < t + 4 + FIELD_SPAN:
                h1, h2 = fw.half(q), fw.half(q + 2)
                m = mem_access(h1, h2)
                if m and m[0] == rd and m[1] == d:
                    uses[o] = (t, q, m[2], d)
                    break
                q += 4 if is32(h1) else 2
    out = []
    for o in spread(uses, CANDIDATES):
        t, q, n, d = uses[o]
        c = fw.candidate(o, q + n - o, "movw", d,
                         force_any=(0, 1, 2, 3, t - o, t - o + 1, t - o + 2, t - o + 3),
                         force_fixed=range(q - o, q - o + n), k2=t)
        if c:
            out.append(c)
        if len(out) >= CANDIDATES:
            break
    return out


def read_imports():
    """[(name, v5.52 value)] in table order"""
    text = open(IMPORTS_H).read()
    return [(n, int(v, 16)) for n, v in re.findall(r"X\((\w+),\s*(0x[0-9A-Fa-f]+)\)", text)]


# Imports that patterns can't find directly: where a trampoline in hooks.S continues, and data
# reached through a pointer.
IMPORT_RULES = {
    "Voice_SetParam_cont": {"from": "Voice_SetParam", "add": 8},
    "Audio_RxHandler_cont": {"from": "0001CA80", "add": 0xF},
    # Three page handlers are identical apart from their (masked) addresses: take page 93's
    # from its record instead.
    "Page93_Handle": {"load": "slot_Page93_Handle"},
    # Reached only as 'this' of the recorder's methods. Assumes the field stays at +0x258.
    "g_rec_ctrl_source": {"from": "g_rec_ctrl", "add": 0x258},
}

# Imports that code refers to at another address: name -> the difference
REFER = {
    "vt_XPage": -8,         # the vtable's header (offset, RTTI)
}

# Further anchors the change templates use: name -> (v5.52 address, rule)
EXTRA = {
    "slot_ChromPage_OnKey": (0x802214CC, {"wordof": "ChromPage_OnKey"}),
    "slot_ChromPage_OnPadDown": (0x802214F0, {"wordof": "ChromPage_OnPadDown"}),
    "slot_ChromPage_BuildLayout": (0x8022150C, {"wordof": "ChromPage_BuildLayout"}),
    "slot_TopScreen_KeyDown": (0x8022CE74, {"wordof": "TopScreen_KeyDown"}),
    "page_records": (0x801F8F74, None),              # 16 bytes per page id (custom-pages.md)
    "page_record_93": (0x801F9544, {"from": "page_records", "add": 16 * 93}),
    "slot_Page93_Handle": (0x8023B58C, {"load": "page_record_93"}),
    "g_rec_ctrl": (0x82E0E71C, None),
    "ParamGetRange_table": (0x800D3604, None),       # tbh table after 'tbh [pc, r1, lsl #1]'
    "ParamGetRange_vari": (0x800D3840, None),        # the case shared by VARI (0xF6)
    "ParamGetRange_pitch": (0x800D38C8, None),       # the case of chromatic pitch (0x9B) and PITCH
}

# Bytes each site's pattern must cover beyond its own change (code our new bytes rely on)
SPANS = {
    0x800F8222: 0x106,      # the VARI wheel up to Roland's 'VINYL on' block at 0x800F8324
    0x800ED544: 0x14,       # both padding words
    0x800D3604: 2,
}


def site(addr):
    return f"{addr:08X}"


def tbh(target, base):
    return {"tbh": [[target, 0], [base, 0]]}


def jump_via_word(old, new):
    """'ldr.w pc, [pc]' + word (+ the rest): the word must follow the instruction directly"""
    return [{"ldrpc": ["@", 4]}, {"hex": new[4:].hex()}]


# Changes whose bytes depend on where Roland's code is. Others are fixed bytes at their site.
TEMPLATES = {
    0x802214CC: {"at": ["slot_ChromPage_OnKey", 0], "old": [{"word": ["ChromPage_OnKey", 0]}]},
    0x802214F0: {"at": ["slot_ChromPage_OnPadDown", 0], "old": [{"word": ["ChromPage_OnPadDown", 0]}]},
    0x8022150C: {"at": ["slot_ChromPage_BuildLayout", 0], "old": [{"word": ["ChromPage_BuildLayout", 0]}]},
    0x8022CE74: {"at": ["slot_TopScreen_KeyDown", 0], "old": [{"word": ["TopScreen_KeyDown", 0]}]},
    0x801F9544: {"at": ["page_record_93", 0], "old": [{"word": ["slot_Page93_Handle", 0]}]},
    0x800D373A: {"at": ["ParamGetRange_table", 2 * 0x9B], "old": [tbh("ParamGetRange_pitch", "ParamGetRange_table")],
                 "new": [tbh("800ED544", "ParamGetRange_table")]},
    0x800D37F0: {"at": ["ParamGetRange_table", 2 * 0xF6], "old": [tbh("ParamGetRange_vari", "ParamGetRange_table")],
                 "new": [tbh("800D5784", "ParamGetRange_table")]},
    0x800D38CA: {"at": ["ParamGetRange_pitch", 2]},
    0x800D38CE: {"at": ["ParamGetRange_pitch", 6]},
    0x800ED544: {"new": [{"ldrpc": ["800ED544", 0x10]}]},
    0x800ED554: {"at": ["800ED544", 0x10]},
    0x800D5784: {"new": [{"ldrpc": ["800D593C", 0]}]},
    0x800D2E2E: {"old": [{"mov32": [12, ["Resampler_Stereo", 0]]}]},
    0x800D2E38: {"old": [{"mov32": [12, ["Resampler_Mono", 0]]}]},
    0x800F823E: {"at": ["800F8222", 0x1C]},
    0x800F8248: {"at": ["800F8222", 0x26]},
    0x800F8268: {"at": ["800F8222", 0x46], "old": "mask"},    # replaced: its addresses needn't match
    0x800DBDC4: {"old": "mask"},                # Roland's two bl offsets: replaced, not checked
    0x800A2C98: {"at": ["Voice_SetParam", -1], "new": jump_via_word},
    # the movw/movt must build this release's g_param_store: audio_rx_orig loads it from the imports
    0x0001CA80: {"old": [{"hex": "f0b581b00446"}, {"mov32": [0, ["g_param_store", 0]]}], "new": jump_via_word},
}


# --- templates --------------------------------------------------------------------------

def value(expr, vals, here):
    if isinstance(expr, int):
        return expr
    name, add = expr
    return (here if name == "@" else vals[name]) + add


def encode(parts, vals, here):
    """Bytes of a template's parts at runtime address here; ?? bytes come back as None."""
    out = []
    for part in parts:
        (kind, arg), = part.items()
        pos = here + len(out)
        if kind == "hex":
            out += [None if arg[i:i + 2] == "??" else int(arg[i:i + 2], 16) for i in range(0, len(arg), 2)]
            continue
        if kind == "word":
            b = mi.word(value(arg, vals, here) & 0xFFFFFFFF)
        elif kind == "mov32":
            b = mi.mov32(arg[0], value(arg[1], vals, here))
        elif kind == "tbh":
            d = value(arg[0], vals, here) - value(arg[1], vals, here)
            if d % 2 or not 0 <= d < 0x20000:
                raise ValueError(f"switch entry can't reach {d:#x} bytes")
            b = mi.half(d // 2)
        elif kind == "ldrpc":
            target = value(arg, vals, here)
            imm = target - ((pos + 4) & ~3)
            if target % 4 or not -0x1000 < imm < 0x1000:
                raise ValueError(f"'ldr.w pc' at {pos:#x} can't load the word at {target:#x}")
            b = mi.half(0xF8DF if imm >= 0 else 0xF85F) + mi.half(0xF000 | abs(imm))
        else:
            raise ValueError(f"unknown template part {kind}")
        out += list(b)
    return out


def template(addr, old, new):
    """The relocatable form of make_image's change (addr, old, new)"""
    if mi.PAYLOAD_RAM <= addr < mi.PAYLOAD_RAM_LIMIT:
        return {"at": addr, "old": [{"hex": old.hex()}], "new": [{"hex": new.hex()}]}
    t = TEMPLATES.get(addr, {})
    old_parts = t.get("old", [{"hex": old.hex()}])
    if old_parts == "mask":
        fw_off = mi.file_offset(addr)
        old_parts = [{"hex": "".join(f"{b:02x}" if ROLAND.fixed[fw_off + i] else "??" for i, b in enumerate(old))}]
    new_parts = t.get("new", [{"hex": new.hex()}])
    if callable(new_parts):
        new_parts = new_parts(old, new)
    return {"at": t.get("at", [site(addr), 0]), "old": old_parts, "new": new_parts}


def anchor_names(parts_list):
    names = set()

    def walk(x):
        if isinstance(x, list):
            if len(x) == 2 and isinstance(x[0], str) and isinstance(x[1], int):
                if x[0] != "@":
                    names.add(x[0])
            else:
                for y in x:
                    walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
    walk(parts_list)
    return names


# --- anchors ----------------------------------------------------------------------------

ROLAND = None


def load(img):
    global ROLAND
    ROLAND = Firmware(img)
    return ROLAND


def known_addresses(sites):
    """v5.52 value of every anchor name the export can use"""
    vals = dict(read_imports())
    vals.update({n: a for n, (a, _) in EXTRA.items()})
    vals.update({site(a): a for a in sites})
    return vals


def make_anchors(fw, names, sites):
    """Rules for the named anchors (and those they depend on), in an order the resolver can
    follow. sites: change addresses -> length of the change."""
    vals = known_addresses(sites)
    rules = {}

    def add(name):
        if name in rules:
            return
        if name in IMPORT_RULES or name in EXTRA and EXTRA[name][1]:
            rule = dict(IMPORT_RULES.get(name) or EXTRA[name][1])
            add(rule.get("from") or rule.get("wordof") or rule["load"])
        else:
            a = vals[name]
            span = SPANS.get(a, sites.get(a, 2))
            rule = {"find": find_rules(fw, a, span, name, REFER.get(name, 0))}
        rules[name] = rule
        check(fw, name, rule, rules, vals)

    for n in sorted(names):
        add(n)
    return [{"name": n, **r} for n, r in rules.items()]


def check(fw, name, rule, rules, vals):
    """The rule gives the v5.52 address in v5.52"""
    if "from" in rule:
        got = vals[rule["from"]] + rule["add"]
    elif "load" in rule:
        got = struct.unpack_from("<I", fw.img, file_offset(vals[rule["load"]]))[0]
    elif "wordof" in rule:
        want = vals[rule["wordof"]] + rule.get("add", 0)
        hits = [runtime(o) for o in fw.words.get(want, [])]
        if len(hits) != 1:
            raise ValueError(f"{name}: {len(hits)} words hold {want:#x}")
        got = hits[0]
    else:
        for c in rule["find"]:
            pat = [(None, False) if c["p"][i:i + 2] == "??" else (int(c["p"][i:i + 2], 16), True)
                   for i in range(0, len(c["p"]), 2)]
            hits = fw.search(pat, 0)
            if len(hits) != 1 or found_value(fw, c, hits[0]) != vals[name]:
                raise ValueError(f"{name}: pattern {c['p']} doesn't give {vals[name]:#x}")
        return
    if got != vals[name]:
        raise ValueError(f"{name}: rule gives {got:#x}, v5.52 has {vals[name]:#x}")


def found_value(fw, c, start):
    """What a 'find' pattern matched at file offset start says (as patcher.js computes it)"""
    o = start + c["k"]
    if c["how"] == "at":
        v = runtime(o)
    elif c["how"] == "word":
        v = struct.unpack_from("<I", fw.img, o)[0]
    elif c["how"] == "bl":
        v = bl_target(fw.half(o), fw.half(o + 2), runtime(o))
    else:
        t = start + c["k2"]
        v = mi.movw_movt_imm(fw.half(t), fw.half(t + 2)) << 16 | mi.movw_movt_imm(fw.half(o), fw.half(o + 2))
    return (v + c["add"]) & 0xFFFFFFFF


# --- which imports each patch needs ----------------------------------------------------

def payload_graph(elf, data):
    """{payload symbol: set of symbols and imports it refers to}, from the linked payload:
    bl/b.w targets, movw/movt pairs and data words (literal pools, tables)."""
    nm = subprocess.run(["arm-none-eabi-nm", "-n", "--special-syms", elf], capture_output=True, text=True,
                        check=True).stdout
    base, end = mi.PAYLOAD_RAM, mi.PAYLOAD_RAM + len(data)
    marks, syms, imps = [], [], {}
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) != 3 or not base <= int(parts[0], 16) < end:
            continue
        a, name = int(parts[0], 16), parts[2]
        if name[:2] in ("$t", "$d"):
            marks.append((a, name[:2]))
        elif name.startswith("imp_"):
            imps[a] = name[4:]
        elif not name.startswith("$"):
            syms.append((a, name))
    starts = [a for a, _ in syms]

    def owner(a):
        if a in imps:
            return "imp:" + imps[a]
        i = max((i for i, s in enumerate(starts) if s <= a), default=None)
        return None if i is None else syms[i][1]

    graph = {}
    ends = [a for a, _ in marks[1:]] + [end]
    for (start, kind), stop in zip(marks, ends):
        o = start - base
        if kind == "$d":
            for o in range((o + 3) & ~3, stop - base - 3, 4):
                v = struct.unpack_from("<I", data, o)[0] & ~1
                if base <= v < end:
                    graph.setdefault(owner(base + o), set()).add(owner(v))
            continue
        movw = {}
        while o + 2 <= stop - base:
            h1 = struct.unpack_from("<H", data, o)[0]
            if not is32(h1) or o + 4 > stop - base:
                if (h1 & 0xF800) == 0xE000:                   # b (16-bit): may be a tail call
                    t = base + o + 4 + ((h1 & 0x7FF) << 1 ^ 0x800) - 0x800
                    graph.setdefault(owner(base + o), set()).add(owner(t))
                o += 2
                continue
            h2 = struct.unpack_from("<H", data, o + 2)[0]
            rd = h2 >> 8 & 0xF
            if (h1 & 0xF800) == 0xF000 and h2 & 0x8000 and (h2 & 0x5000):
                t = bl_target(h1, h2, base + o) & ~1
                if base <= t < end:
                    graph.setdefault(owner(base + o), set()).add(owner(t))
            elif (h1 & 0xFBF0) == 0xF240 and not h2 & 0x8000:
                movw[rd] = mi.movw_movt_imm(h1, h2)
            elif (h1 & 0xFBF0) == 0xF2C0 and not h2 & 0x8000 and rd in movw:
                v = (mi.movw_movt_imm(h1, h2) << 16 | movw.pop(rd)) & ~1
                if base <= v < end:
                    graph.setdefault(owner(base + o), set()).add(owner(v))
            o += 4
    return graph, {a: n for a, n in syms}


def imports_reached(graph, names, entries):
    """Imports reachable from the payload symbols at the entry addresses"""
    todo = [names.get(a & ~1) or a for a in entries]
    seen = set()
    while todo:
        n = todo.pop()
        if n in seen or n is None:
            continue
        seen.add(n)
        todo += graph.get(n, ())
    return sorted(n[4:] for n in seen if isinstance(n, str) and n.startswith("imp:"))
