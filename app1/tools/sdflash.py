#!/usr/bin/env python3
"""Put a patched APP1 on an SD card, with a small PyQt6 window.

    python3 app1/tools/sdflash.py

1. Pick the SD card (only removable/hot-plug disks are listed; never system disks).
2. Tick the patches to apply (from app1/tools/make_image.py, which also checks the
   original v5.52 image and every replaced byte).
3. "Write to card": builds the image, optionally formats the card FAT32 (asks first),
   copies it as SP404MKII_APP1.bin, removes SP404MKII_APP0.bin, then tests the card.
   "Test card" runs only the test against the image the current selection would build.

The test checks the card, not the SP: fsck.fat -n (read-only) must find no errors, and
the file read back after a remount must match the built image byte for byte. Flashing the
SP is still done on the unit: hold SHIFT, power on, press VALUE, wait for UPDATE COMPLETE!
Recovery: docs/app1/patching.md.

Mount/unmount go through udisksctl (no password for removable media). Formatting and
fsck need root, so they run through pkexec, which shows the system password dialog.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QGroupBox,
                             QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QRadioButton, QVBoxLayout, QWidget)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_image  # noqa: E402

APP0_NAME = "SP404MKII_APP0.bin"
LABEL = "SP404"
MAX_CARD_BYTES = 2 << 40          # SDXC tops out at 2 TB; anything bigger isn't a card
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/home", "/usr", "/var", "[SWAP]"}


# --- block devices -----------------------------------------------------------------------

def _flag(v):
    return v is True or str(v) in ("1", "true", "True")


def list_cards():
    """Removable disks that look like SD cards: [{dev, part, label, ...}]."""
    out = subprocess.run(["lsblk", "-J", "-b", "-o",
                          "NAME,PATH,SIZE,RM,HOTPLUG,TRAN,TYPE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,VENDOR"],
                         capture_output=True, text=True, check=True).stdout
    cards = []
    for d in json.loads(out)["blockdevices"]:
        size = int(d.get("size") or 0)
        if d.get("type") != "disk" or not (_flag(d.get("rm")) or _flag(d.get("hotplug"))):
            continue
        if size == 0 or size > MAX_CARD_BYTES:      # empty reader slot, or not a card
            continue
        parts = [c for c in d.get("children") or [] if c.get("type") == "part"]
        mounts = {m for x in [d] + parts for m in (x.get("mountpoints") or []) if m}
        if mounts & SYSTEM_MOUNTS:
            continue
        target = parts[0] if parts else d
        name = " ".join(x for x in ((d.get("vendor") or "").strip(), (d.get("model") or "").strip()) if x)
        cards.append({
            "dev": d["path"], "part": target["path"], "whole_disk": not parts,
            "size": size, "name": name or d["name"], "fstype": target.get("fstype") or "",
            "label": target.get("label") or "", "nparts": len(parts),
        })
    return cards


def describe(c):
    gb = c["size"] / 1e9
    fs = f'{c["fstype"] or "no filesystem"}{" " + repr(c["label"]) if c["label"] else ""}'
    return f'{c["dev"]}  {gb:.1f} GB  {c["name"]}  ({c["part"]}: {fs})'


def run(cmd, log, check=True):
    log("$ " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    for line in (p.stdout + p.stderr).splitlines():
        if line.strip():
            log("  " + line)
    if check and p.returncode != 0:
        if cmd[0] == "pkexec" and p.returncode in (126, 127):
            raise RuntimeError("authorisation was cancelled or refused")
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed (exit {p.returncode})")
    return p


def current_mount(part):
    out = subprocess.run(["lsblk", "-J", "-o", "PATH,MOUNTPOINTS", part],
                         capture_output=True, text=True, check=True).stdout
    for d in json.loads(out)["blockdevices"]:
        if d["path"] == part:
            return next((m for m in d.get("mountpoints") or [] if m), None)
    return None


def mount(part, log):
    m = current_mount(part)
    if m:
        return m
    p = run(["udisksctl", "mount", "--no-user-interaction", "-b", part], log)
    found = re.search(r" at (.+?)\.?$", p.stdout.strip())
    m = found.group(1) if found else current_mount(part)
    if not m:
        raise RuntimeError(f"could not find where {part} was mounted")
    return m


def unmount_all(card, log):
    out = subprocess.run(["lsblk", "-J", "-o", "PATH,MOUNTPOINTS", card["dev"]],
                         capture_output=True, text=True, check=True).stdout

    def walk(d):
        yield d
        for c in d.get("children") or []:
            yield from walk(c)
    for top in json.loads(out)["blockdevices"]:
        for d in walk(top):
            if any(d.get("mountpoints") or []):
                run(["udisksctl", "unmount", "--no-user-interaction", "-b", d["path"]], log)
    run(["sync"], log)


def settle(log):
    run(["udevadm", "settle"], log, check=False)
    time.sleep(0.5)


# --- the jobs (run in a worker thread) ---------------------------------------------------

def job_write(card, ids, fmt, remove_app0, log):
    log("== building image")
    img = make_image.build(ids, log=log)
    log(f"sha256 {hashlib.sha256(img).hexdigest()}")
    if fmt:
        log(f"== formatting {card['part']} as FAT32")
        unmount_all(card, log)
        cmd = ["pkexec", "mkfs.fat", "-F", "32", "-n", LABEL]
        if card["whole_disk"]:
            cmd.append("-I")
        run(cmd + [card["part"]], log)
        settle(log)
    log("== writing")
    m = mount(card["part"], log)
    st = os.statvfs(m)
    if st.f_bavail * st.f_frsize < len(img):
        raise RuntimeError("not enough free space on the card")
    for name in os.listdir(m):
        if name.lower() == APP0_NAME.lower():
            if remove_app0:
                os.remove(os.path.join(m, name))
                log(f"removed {name} (the updater would reflash the companion chip)")
            else:
                log(f"warning: {name} is on the card; the updater will reflash the companion chip")
        elif name.lower() == make_image.IMAGE_NAME.lower() and name != make_image.IMAGE_NAME:
            os.remove(os.path.join(m, name))      # differently-cased leftover
    path = os.path.join(m, make_image.IMAGE_NAME)
    with open(path, "wb") as f:
        f.write(img)
        f.flush()
        os.fsync(f.fileno())
    log(f"wrote {path} ({len(img)} bytes)")
    if not fmt:
        log("note: card not formatted; if the updater says NO UPDATER, run again with Format ticked")
    job_test(card, ids, log, img)


def job_test(card, ids, log, img=None):
    if img is None:
        log("== building the expected image")
        img = make_image.build(ids, log=log)
    log("== testing the card")
    m = mount(card["part"], log)
    files = sorted(os.listdir(m))
    log("card contents: " + (", ".join(files) or "(empty)"))
    if make_image.IMAGE_NAME not in files:
        raise RuntimeError(f"{make_image.IMAGE_NAME} is not on the card")
    if APP0_NAME in files:
        log(f"warning: {APP0_NAME} is on the card")
    unmount_all(card, log)                 # flushes everything, drops cached pages
    log("checking the file system (read-only)")
    p = run(["pkexec", "fsck.fat", "-n", card["part"]], log, check=False)
    if p.returncode in (126, 127):
        raise RuntimeError("authorisation was cancelled or refused")
    if p.returncode != 0:
        raise RuntimeError("fsck.fat found problems: format the card (tick Format) and write again")
    m = mount(card["part"], log)
    with open(os.path.join(m, make_image.IMAGE_NAME), "rb") as f:
        back = f.read()
    unmount_all(card, log)
    if back != img:
        raise RuntimeError(f"read-back differs from the built image ({len(back)} vs {len(img)} bytes)")
    log(f"read-back matches the built image (sha256 {hashlib.sha256(back).hexdigest()[:16]}…)")
    log("== card OK and unmounted; you can take it out.")
    log("On the SP: hold SHIFT, power on, press VALUE, wait ~90 s, "
        "and confirm UPDATE COMPLETE! before doing anything else.")


class Worker(QObject):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.fn(*self.args, self.log.emit)
            self.done.emit(True, "done")
        except Exception as e:      # report anything to the window instead of dying silently
            self.done.emit(False, str(e))


# --- the window ---------------------------------------------------------------------------

class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Jam Tricks v{make_image.JAM_TRICKS_VERSION} to SD card")
        self.resize(760, 560)
        self.cards, self.thread = [], None

        cards = QGroupBox("SD card")
        self.card_box = QComboBox()
        self.card_box.currentIndexChanged.connect(self.update_buttons)
        row = QHBoxLayout(cards)
        row.addWidget(self.card_box, 1)

        # A patch on its own is a checkbox; patches sharing a group are one radio choice
        # (with "Off"), since make_image allows at most one per group. Diagnostic patches
        # (meters and the like) get a box of their own.
        patches = QGroupBox("Patches")
        col = QVBoxLayout(patches)
        diagnostics = QGroupBox("Diagnostics")
        col_diag = QVBoxLayout(diagnostics)
        self.patch_boxes = []
        groups = {}
        for p in make_image.PATCHES:
            label = f"{p.title}   [{p.id}]" + ("\n⚠ " + p.warning if p.warning else "")
            if p.group:
                if p.group not in groups:
                    box = QGroupBox(f"{p.group} (choose one)")
                    groups[p.group] = (QVBoxLayout(box), QButtonGroup(self))
                    off = QRadioButton("Off")
                    off.setChecked(True)
                    groups[p.group][0].addWidget(off)
                    groups[p.group][1].addButton(off)
                    col.addWidget(box)
                cb = QRadioButton(label)
                groups[p.group][0].addWidget(cb)
                groups[p.group][1].addButton(cb)
            else:
                cb = QCheckBox(label)
                cb.setChecked(not p.experimental)
                (col_diag if p.diagnostic else col).addWidget(cb)
            changes = "" if callable(p.changes) else "\n".join(
                f"{a:#010x}: {o.hex()} -> {n.hex()}  {w}" for a, o, n, w in p.changes) + "\n"
            cb.setToolTip(changes + f"Details: {p.doc}")
            self.patch_boxes.append((p, cb))

        options = QGroupBox("Options")
        col2 = QVBoxLayout(options)
        self.format_box = QCheckBox("Format the card as FAT32 first (erases everything on it)")
        self.format_box.setChecked(True)
        self.app0_box = QCheckBox(f"Remove {APP0_NAME} from the card")
        self.app0_box.setChecked(True)
        col2.addWidget(self.format_box)
        col2.addWidget(self.app0_box)

        self.write_btn = QPushButton("Write to card")
        self.test_btn = QPushButton("Test card")
        self.write_btn.clicked.connect(self.on_write)
        self.test_btn.clicked.connect(self.on_test)
        self.status = QLabel("")
        buttons = QHBoxLayout()
        buttons.addWidget(self.write_btn)
        buttons.addWidget(self.test_btn)
        buttons.addWidget(self.status, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("monospace", 9))

        layout = QVBoxLayout(self)
        if col_diag.count() == 0:
            diagnostics.hide()
        for w in (cards, patches, diagnostics, options):
            layout.addWidget(w)
        layout.addLayout(buttons)
        layout.addWidget(self.log_view, 1)

        self.timer = QTimer(self, interval=2000, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def log(self, text):
        self.log_view.appendPlainText(text)

    def refresh(self, force=False):
        if self.thread:
            return              # never change the target while a job runs
        try:
            cards = list_cards()
        except Exception as e:
            self.status.setText(f"lsblk failed: {e}")
            return
        if cards == self.cards and not force:
            return
        keep = self.selected()["dev"] if self.selected() else None
        self.cards = cards
        self.card_box.blockSignals(True)
        self.card_box.clear()
        for c in cards:
            self.card_box.addItem(describe(c))
        if not cards:
            self.card_box.addItem("(no SD card found: insert one)")
        devs = [c["dev"] for c in cards]
        if keep in devs:
            self.card_box.setCurrentIndex(devs.index(keep))
        self.card_box.blockSignals(False)
        self.update_buttons()

    def selected(self):
        i = self.card_box.currentIndex()
        return self.cards[i] if 0 <= i < len(self.cards) else None

    def selected_ids(self):
        return {p.id for p, cb in self.patch_boxes if cb.isChecked()}

    def update_buttons(self):
        ok = self.selected() is not None and self.thread is None
        self.write_btn.setEnabled(ok)
        self.test_btn.setEnabled(ok)

    def on_write(self):
        card = self.selected()
        for p, cb in self.patch_boxes:
            if cb.isChecked() and p.warning:
                answer = QMessageBox.warning(
                    self, "Patch with a warning", f"{p.title}\n\n{p.warning}\n\nUse it anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Yes:
                    return
        if self.format_box.isChecked():
            answer = QMessageBox.warning(
                self, "Format card?",
                f"Format {card['part']} as FAT32?\n\n{describe(card)}\n\n"
                "Everything on it will be erased.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start(job_write, card, self.selected_ids(), self.format_box.isChecked(),
                   self.app0_box.isChecked())

    def on_test(self):
        self.start(job_test, self.selected(), self.selected_ids())

    def start(self, fn, *args):
        self.log_view.clear()
        self.status.setText("working…")
        self.thread = QThread(self)
        self.worker = Worker(fn, *args)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log)
        self.worker.done.connect(self.finished)
        self.thread.start()
        self.update_buttons()

    def finished(self, ok, message):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.status.setText("✔ " + message if ok else "✘ " + message)
        if not ok:
            self.log("ERROR: " + message)
        self.update_buttons()
        self.refresh(force=True)    # labels/filesystems may have changed


def main():
    app = QApplication(sys.argv)
    w = Window()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
