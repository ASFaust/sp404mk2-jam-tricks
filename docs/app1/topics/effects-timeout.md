# Effects screen timeout (goal #3)

## Summary

When you touch an effect knob or button, the SP draws the effect's parameters over the main
screen, then removes them again after 2 seconds without input. That display is a screen item
of Roland's class **`XITEM_EFXEDIT`**, which sits on UI page **74**. The item keeps a
countdown in milliseconds at `this + 0x1B10`. It's set to **2000** (4000 on page 31) whenever
something happens, and each 50 ms tick subtracts **50**. When it runs out, the item tells its
page to close, and the UI goes back to the previous screen.

**Patch (done, tested on the device 2026-10-25):** one byte at `0x801467D2`
(APP1 file offset `0x168292`) changes from `0x32` to `0x00`. That turns `sub.w r1, r0, #0x32` into
`sub.w r1, r0, #0`, so the countdown never goes down and the timeout never fires. EXIT still
closes the display, which is the behaviour we wanted. Built by `app1/tools/make_image.py`.

## How it was found (the reasoning)

1. Tallfree names `Effect_DrawValues` (`0x80146260`). Its only caller is `EfxEdit_DrawEffect`
   (`0x80146238`), which draws the three parameter values, the bus label and "MFX LIST".
2. `fwmem.py refs 80146238` finds one data word pointing at it, `0x80226A20`. That's a
   vtable slot. Walking back to the two zero words of the header gives the start of the
   vtable at `0x802269C0` (`fwmem.py words 80226800 200`).
3. `xrefs.sh 802269C0` finds the constructor that stores the vtable, `EfxEdit_ctor`
   (`0x80147BA0`). It contains `this[0x6C4] = 2000`, i.e. offset `0x1B10` = 2000, which looks like a
   time in milliseconds.
4. The constructor's only caller is the factory `0x80055128`. A table at `0x802C9DC0` pairs
   item names with factories, and this one is listed under `"XITEM_EFXEDIT"`. (Pages build
   their layout from script lines like `XITEM_EFXEDIT(0, 0, 127, 63)`, and the name selects
   the factory.)
5. A scan of all code for `#0x1b10` leads to vtable slot `0x28` of the class,
   `EfxEdit_Update` (`0x80146598`), the method that runs on every tick. It contains the countdown.
6. `XITEM_EFXEDIT(...)` is generated in only one place, `EfxPage_BuildLayout` (`0x80101570`),
   slot `0x118` of the vtable at `0x8021F868`. That vtable is set by the handler in
   page-table slot 18 (`0x80061760`), which belongs to **UI page id 74** (see
   [ui-pages.md](ui-pages.md) for the difference between page ids and table slots).

## Key functions and data

| Address | Name (as in our symbols) | What it does |
|---|---|---|
| `0x80055128` | `XItem_EfxEdit_Factory` | allocates `0x1B78` bytes, calls the constructor |
| `0x80147BA0` | `EfxEdit_ctor` | sets vtable `0x802269C0`, countdown = 2000 |
| `0x80146598` | `EfxEdit_Update` | vtable slot `0x28`, every tick: countdown, closing |
| `0x80146238` | `EfxEdit_DrawEffect` | vtable slot `0x60`, draws the effect display |
| `0x80146E78` | `EfxEdit_Open` | vtable slot `0x18`, reads the bus/effect state when shown |
| `0x80146FC0` | `EfxEdit_OnValueTurn` | vtable slot `0x48`, VALUE knob changes the effect |
| `0x800BB180` | `Item_NotifyOwner` | `(item, code)`: posts message `{2, code}` to the item's owner (`item+0x6C`) |
| `0x800D6380` | `UI_GetCurrentPage` | returns the current page id (halfword at `0x80245880`) |
| `0x800D9F20` | `Key_IsHeld` | `(code)`: 1 while that button is held (bitmap at `0x80592448`) |
| `0x80101570` | `EfxPage_BuildLayout` | page 74's layout: one `XITEM_EFXEDIT` over the whole screen |
| `0x801015E8` | `EfxPage_OnKeyDown` | page 74: keys `0x13`, `0x2B` ignored, `0x31` special, rest → `0x800C9788` |
| `0x800C9788` | `TopScreen_HandleKey` | the main screen's shared key handler |

Fields of the `XITEM_EFXEDIT` object (offsets from `this`):

| Offset | Meaning |
|---|---|
| `0x1B10` | countdown in ms (int, signed) |
| `0x1B15` | "redraw needed" flag |
| `0x1B16` | "something changed": reloads the countdown on the next tick |
| `0x1B18` | MFX list mode: 0 = off, 1/2/3 = list 1–16 / 17–32 / 33–48 |
| `0x1B20` | current effect (value from table `0x80245D54`) |
| `0x1B3F` | key `0x24` was held when the display opened (alternative closing rules) |

## The countdown in detail

From `EfxEdit_Update`, with `bus = PickStore_Get(&g_pick_store, 0x3A, 0)`:

```c
old = this->countdown;
this->countdown = old - 50;                    // 0x801467D0: sub.w r1, r0, #0x32
if (bus == 1 || bus == 2) {                    // only buses 1 and 2 time out
    if (!this->flag_1B3F) {
        if (old < 50 && !(Key_IsHeld(0x30) && page != 36))
            close();                           // 0x8014682C
    } else if (old < 50 || effect_index == 0)
        close();
} else if (bus == 0) {
    this->countdown = (page == 31) ? 4000 : 2000;   // bus 0 never times out
}
// close(): clears a redraw flag, Item_NotifyOwner(this, 1), PickStore 0x38 = 0, ...
```

The countdown is reloaded to 2000/4000 in several places: when the effect state changes,
when one of the keys `0x2E`–`0x31` is held, and while a global flag at `0x80592320` is set.

What `bus` means is a guess from the drawing code: it picks the image
`efx_bus_input` for 0 and a per-bus image for 1–4. So 0 is probably INPUT FX and 1–4 are
BUS FX 1–4. On that reading, only bus 1 and 2 time out.

## The patch

| Address | File offset | Original | New | Effect |
|---|---|---|---|---|
| `0x801467D2` | `0x168292` | `32` | `00` | countdown is never decremented |

Bytes `a0 f1 32 01` become `a0 f1 00 01` (checked with capstone: `sub.w r1, r0, #0`). The
code is copied from flash to SDRAM at boot, so patching the image needs no cache work.

Other ways to get the same result, if this one turns out to have side effects:
- Change the reloads (`mov.w r1, #0x7d0` at `0x80146666`, `…6F8`, `…71C`, `…738`, `…802`)
  to a larger value. That gives a longer timeout instead of none. `mov.w` only encodes
  "modified immediates" (an 8-bit value shifted, or a repeated byte pattern). For example,
  `0x000A0000` = 655,360 ms ≈ 11 minutes is encodable; 600,000 is not.
- Skip the call to `close()` at `0x8014682C`. That's riskier: it's also the path
  for "effect switched off".

## Open questions

- ~~What closes the display once the timeout is gone?~~ Answered on the device: EXIT closes
  it (page 74 passes EXIT, `0x22`, on to `TopScreen_HandleKey`). The code path for that
  hasn't been traced.
- Which pages are 10, 11, 31 and 36? The item checks them, but if page 74 is the current page
  while it's shown, those checks can't be true. Maybe the item also appears elsewhere, or the
  page id isn't updated for this overlay. A page-id → title map would answer it.
- Which physical buttons are key codes `0x24`, `0x2E`, `0x2F`, `0x30`, `0x31`? Tallfree
  measured `0x31` = pressing VALUE, `0x22` = EXIT, `0x2A` = SHIFT.
- Does the INPUT FX Setting screen (class with vtable `0x80226A68`, constructor
  `0x80148750`, Tallfree's `EfxEdit_Draw`) have its own timeout? It also stores 2000 at
  `+0x1B10`, but its update method has no `-50`.
