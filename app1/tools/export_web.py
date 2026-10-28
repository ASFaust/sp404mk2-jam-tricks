#!/usr/bin/env python3
"""Export the public patches for the web patcher (docs/app1/topics/web-patcher.md).

Writes patches.json: the firmware it fits (v5.52, by SHA-256), the payload, the changes that
make Roland's start-up copy the payload, and each public patch's changes with their original
bytes, all from make_image.py's patch definitions. The page applies them in the browser to the
firmware the user brings.

Before writing, the JSON is replayed here for every combination of patches the page allows,
and each result must equal make_image.build() byte for byte.

Usage:
    python app1/tools/export_web.py [output directory]
        default: web/
"""

import base64
import datetime
import hashlib
import itertools
import json
import os
import subprocess
import sys

import anchors
import make_image as mi
import package_web

# The patches the website offers, with the text users see. Diagnostic patches (cpu-meter,
# tuner-probes) stay out of the website selection.
PUBLIC = {
    "no-efx-timeout": {
        "name": "Effect screen stays open",
        "text": "The effect edit screen no longer jumps back to the main screen after a few "
                "seconds. It stays until you press EXIT.",
    },
    "scale-pads": {
        "name": "Scale pads",
        "text": "In chromatic mode with a scale chosen, all 16 pads play notes of the scale, "
                "from the bottom left upwards: about two octaves, starting an octave below the "
                "root. Stock firmware darkens the pads outside the scale instead. The VALUE knob "
                "moves the grid by a row (with SHIFT, by one step). The playable range is "
                "+/-24 semitones.",
    },
    "scale-pads-wide": {
        "name": "Scale pads + wide PITCH",
        "text": "Scale pads, and every pad's PITCH reaches +/-24 semitones instead of +/-12.",
    },
    "chrom-voices": {
        "name": "Chromatic voices",
        "text": "In chromatic mode, hold REMAIN and turn VALUE to choose LEGATO, MONO, POLY 2 to "
                "POLY 8 voices, or unlimited POLY. With a voice count, the oldest note stops when "
                "you play one more. The pitch/speed panel's PITCH and FINE now tune the "
                "chromatic notes.",
    },
    "vari4-stretch": {
        "name": "VARI Plus time stretch",
        "text": "A fourth VARI option, Plus, next to Off, Backing and Ensemble (hold SHIFT on "
                "the pitch/speed panel). It stretches and pitch-shifts with fewer smeared "
                "attacks and less phasiness. It costs more CPU than the stock modes, so many "
                "voices at once can be too much. Changing VARI while a pad plays no longer "
                "restarts it.",
    },
    "tuner": {
        "name": "Guitar tuner",
        "text": "Press BANK A and BANK B together on the main screen to open a tuner: it shows "
                "the nearest note and how many cents sharp or flat you are. It listens to the "
                "SP's output, input effects included, so keep other sounds quiet while tuning; "
                "EXT SOURCE is switched on so the input is heard. EXIT or the same two buttons "
                "take you back.",
    },
}


def file_changes(changes):
    out = []
    for addr, old, new, why in changes:
        out.append({"offset": mi.file_offset(addr), "old": old.hex(), "new": new.hex(), "why": why})
    return out


class Recorder(dict):
    """The payload symbols, noting which ones a patch's changes use (its entry points)"""

    def __init__(self, syms):
        super().__init__(syms)
        self.read = set()

    def __getitem__(self, name):
        self.read.add(dict.__getitem__(self, name))
        return dict.__getitem__(self, name)


def check_templates(patches, vals):
    """Each relocatable change, at the v5.52 addresses, is the change make_image.py makes"""
    for p in patches:
        for t, c in zip(p["relocatable"], p["changes"]):
            at = anchors.value(t["at"], vals, None)
            old = anchors.encode(t["old"], vals, at)
            new = bytes(anchors.encode(t["new"], vals, at))
            want_old = bytes.fromhex(c["old"])
            if (mi.file_offset(at) != c["offset"] or new.hex() != c["new"] or len(old) != len(want_old)
                    or any(b is not None and b != w for b, w in zip(old, want_old))):
                sys.exit(f"[{p['id']}] the template for {c['offset']:#x} ({c['why']}) differs from make_image.py")


def apply(original, data, ids):
    """Do what the page does (patcher.js applyPatches), for the self-test."""
    img = bytearray(original)
    patches = {p["id"]: p for p in data["patches"]}
    if any(patches[i]["payload"] for i in ids):
        pl = data["payload"]
        img += b"\xff" * (pl["offset"] - len(img)) + base64.b64decode(pl["base64"])
        steps = [("payload", c) for c in data["payload_setup"]]
    else:
        steps = []
    steps += [(i, c) for i in ids for c in patches[i]["changes"]]
    applied = {}
    for who, c in steps:
        off, old, new = c["offset"], bytes.fromhex(c["old"]), bytes.fromhex(c["new"])
        found = bytes(img[off:off + len(old)])
        if applied.get(off) == new and found == new:
            continue
        if found != old:
            raise ValueError(f"[{who}] {off:#x}: expected {old.hex()}, found {found.hex()}")
        img[off:off + len(new)] = new
        applied[off] = new
    return bytes(img) + data["marker"].encode()


def combinations(data):
    """Every selection the page allows: any subset, at most one per group."""
    ids = [p["id"] for p in data["patches"]]
    for n in range(1, len(ids) + 1):
        for combo in itertools.combinations(ids, n):
            groups = [next(p["group"] for p in data["patches"] if p["id"] == i) for i in combo]
            groups = [g for g in groups if g]
            if len(groups) == len(set(groups)):
                yield list(combo)


def main(argv):
    out_dir = argv[1] if len(argv) > 1 else os.path.join(mi.REPO, "web")
    quiet = []
    original = open(mi.ORIGINAL, "rb").read()
    if hashlib.sha256(original).hexdigest() != mi.ORIGINAL_SHA256:
        raise ValueError("input is not the supported original v5.52 APP1")
    payload, syms = mi.build_payload(quiet.append)

    # The start-up change, as the difference it makes to the padded original
    padded = bytearray(original) + b"\xff" * (mi.PAYLOAD_FLASH - mi.FLASH_BASE - len(original)) + payload
    setup = bytearray(padded)
    mi.add_payload_copy(setup, len(payload), quiet.append)
    payload_setup, i = [], 0
    while i < len(setup):
        if setup[i] == padded[i]:
            i += 1
            continue
        j = i
        while j < len(setup) and setup[j] != padded[j]:
            j += 1
        payload_setup.append({"offset": i, "old": padded[i:j].hex(), "new": setup[i:j].hex(),
                              "why": "start-up: copy the payload to SDRAM"})
        i = j

    patches, sites, names = [], {}, set()
    fw = anchors.load(original)
    graph, sym_names = anchors.payload_graph(os.path.join(mi.PAYLOAD_BUILD, "payload.elf"), payload)
    for p in mi.PATCHES:
        if p.id not in PUBLIC:
            continue
        used = Recorder(syms)
        changes = mi.patch_changes(p, used)
        templates = [dict(anchors.template(a, old, new), why=why) for a, old, new, why in changes]
        imports = anchors.imports_reached(graph, sym_names, used.read) if p.payload else []
        for a, old, _, _ in changes:
            if not mi.PAYLOAD_RAM <= a < mi.PAYLOAD_RAM_LIMIT:
                sites[a] = len(old)
        names |= anchors.anchor_names(templates) | set(imports)
        patches.append({"id": p.id, **PUBLIC[p.id], "group": p.group, "payload": p.payload,
                        "warning": p.warning, "doc": p.doc, "changes": file_changes(changes),
                        "relocatable": templates, "imports": imports})
    import_names = [n for n, _ in anchors.read_imports()]
    rules = anchors.make_anchors(fw, names | set(import_names), sites)
    check_templates(patches, anchors.known_addresses(sites))
    commit = subprocess.run(["git", "-C", mi.REPO, "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", mi.REPO, "status", "--porcelain", "app1/"],
                           capture_output=True, text=True).stdout.strip()
    data = {
        "format": 1,
        "version": mi.JAM_TRICKS_VERSION,
        "marker": mi.MARKER.decode(),       # appended to every image; refused as input
        "built": datetime.date.today().isoformat(),
        "source": (commit or "uncommitted") + ("+changes" if dirty else ""),
        "firmware": [{"version": "5.52", "file": "SP404MKII_APP1.bin", "size": len(original),
                      "sha256": hashlib.sha256(original).hexdigest()}],
        "payload": {"offset": mi.PAYLOAD_FLASH - mi.FLASH_BASE,
                    "base64": base64.b64encode(payload).decode(),
                    "imports": syms["roland_imports"] - mi.PAYLOAD_RAM},
        "payload_setup": payload_setup,
        "patches": patches,
        # For other versions: how to find each address (anchors.py) and the import table's order
        "resolve": {"anchors": rules, "imports": import_names, "flash": mi.FLASH_BASE,
                    "payload_ram": mi.PAYLOAD_RAM, "scatter_table": mi.SCATTER_TABLE_NEW - mi.FLASH_BASE},
    }

    n = 0
    for ids in combinations(data):
        if apply(original, data, ids) != mi.build(ids, log=quiet.append):
            sys.exit(f"replaying the JSON for {','.join(ids)} differs from make_image.build()")
        n += 1
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "patches.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    package_web.build(out_dir)
    print(f"checked {n} combinations against make_image.py; wrote {path} "
          f"({len(patches)} patches, payload {len(payload)} bytes, source {data['source']})")


if __name__ == "__main__":
    main(sys.argv)
