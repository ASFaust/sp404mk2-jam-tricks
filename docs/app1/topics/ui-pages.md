# UI pages

## Summary

Every screen is a *page*. The UI task keeps the current **page id** in the halfword at
`0x80245880` (read by `UI_GetCurrentPage`, `0x800D6380`) and a back stack at `0x805968EC`.
A page is driven by its handler function, which receives messages whose first halfword is a type:
1 = build (the handler creates the page object and its layout), 2 = destroy, others
(about 20 per second) are passed on to the page object's vtable.

**Page ids are not table slots.** The table at `0x8023B4FC` (94 handler pointers, Tallfree's
"page table") is reached indirectly: page id `n` has a 16-byte record at
`0x801F8F74 + 16*n`, whose first word points at a slot in that table. Tallfree numbers
pages by **slot**. We number them by **page id**, the value the firmware itself compares
against. Example: the effect display's handler `0x80061760` is in slot 18, and it's page id 74.

Print the mapping with:

```sh
python3 - <<'EOF'
import sys; sys.path.insert(0, 'tools')
from fwmem import Memory
m = Memory()
for pid in range(94):
    slot_addr = m.word(0x801F8F74 + 16 * pid)
    print(pid, (slot_addr - 0x8023B4FC) // 4, hex(m.word(slot_addr)))
EOF
```

## Key functions and data

| Address | Name | What it does |
|---|---|---|
| `0x800D0040` | `UI_SwitchPage` | handles a page-change request in `0x8024587C`: `0xFFFE` = back, `0xFFFD` = rebuild, otherwise a page id |
| `0x800D6380` | `UI_GetCurrentPage` | returns `*(u16 *)0x80245880` |
| `0x801F8F74` | `g_page_records` | 16 bytes per page id: slot pointer, flags, … |
| `0x8023B4FC` | `g_page_factories` | 94 handler pointers, indexed by slot (Tallfree's name; they're handlers, not factories) |
| `0x802C9DC0` | `g_xitem_factories` | `{name, factory}` pairs for layout items (`XITEM_STRING`, `XITEM_EFXEDIT`, …) |

## Details

- Page handlers all have the same shape (see `0x80061760`): on type 1 allocate the page object,
  set its vtable, store it in a global (e.g. `0x80CFD438`) and call `0x800A80A8` to build it.
  On type 2 call its slot `+8`, then its destructor. Otherwise forward to slot `+8`.
- A page's layout is made of text lines such as `XITEM_EFXEDIT(0, 0, 127, 63)`, generated in
  a vtable method (slot `0x118` for page 74). The item name selects a factory in
  `g_xitem_factories`.
- Items post messages to their page through `Item_NotifyOwner` (`0x800BB180`).

- Page objects, the messages they get and how they draw: [custom-pages.md](custom-pages.md).
  Message 0 goes to all 94 handlers at boot (`UI_Init`), 1 build, 2 destroy, 3 draw, 4 update,
  5/6 key down/up, 7 encoder, 8 tick, 9 knob.
- Record fields: `+0` slot pointer, `+4` (byte) back-stack level (top screen 1, DJ mode
  `0x0A`, most pages `0x19`), `+8` and `+0xC` copied to `0x80592460` / `0x80592464` (the
  latter ORed with `0xD000`) when the page becomes current. Id `0x5E` (94) means "no page",
  so new ids can't simply be appended (see custom-pages.md).
- Known ids: 1 top screen, 31 DJ mode (BANK D + BANK E), 74 effect display, 93 placeholder
  (host of our custom pages).

## Where UI text is

Searching for on-screen text often fails. Text reaches the screen in four forms:

1. **Normal C strings** in the string pools (`fwmem.py find TEXT`). Pools are tail-merged:
   "Type" or "Level" can be the end of "Reverse Type" or "Click Level", with no own copy.
   Value lists are often one string separated by `\x01` (`"TRI\x01SQR\x01SIN…"`).
2. **Built from immediates.** Short `strcpy`s become `movw/movt` + `str` of the bytes, e.g.
   `movw r0,#0x6153; movt r0,#0x2B77` = `"Saw+"`. They are invisible to a string search; Ghidra
   shows them as `*out = 0x2b776153` or `builtin_strncpy(buf, "SHIF", 4)`. Search instead
   for a format string or a longer string nearby, or for the 4-byte little-endian word.
3. **Baked into pictures.** Icon menus and backgrounds are bitmaps named
   `G:/xpage/work1/...`. A table of 87 embedded ones (16-byte entries
   `{id, name, data, size}`, data mostly `SBMP`) is at `0x801AE5B0`; the rest are elsewhere.
4. Strings kept *inside code* next to a function and reached with `adr` (PC-relative), so
   `fwmem.py refs` finds no pointer to them. Use `xrefs.sh` (address without `0x`) or read
   the function just before the string.

Example: [sound-generator.md](sound-generator.md).

## Open questions

- A page id → title map. Tallfree's `MapPages.java` finds title-setting functions, which
  would need to be mapped to vtables → handlers → slots → page ids.
- What do record bytes `+5` (`0xFF` on most pages) and the flag words mean exactly?
