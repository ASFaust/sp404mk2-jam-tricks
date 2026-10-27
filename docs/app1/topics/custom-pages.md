# Custom pages: screens of our own

## Summary

Every screen on the SP is a **page**: an object of Roland's C++ class **XPage**, driven by
messages from the UI task (see [ui-pages.md](ui-pages.md) for page ids and records). We add
screens by building real XPage objects whose virtual methods are our C functions. The module
is `app1/payload/page.c` (interface `app1/payload/page.h`). A feature describes its page with a
`struct cpage` (callbacks for open, close, draw, tick, keys, encoder) and calls
`cpage_open()`. The tuner ([tuner.md](tuner.md)) is the first user.

**Why we borrow a page id instead of adding one.** Page ids index the page records
(`g_page_records`, 94 records of 16 bytes) directly, with no range check. Id 94 (`0x5E`) is
the firmware's "no page" value, and the bytes after the table are another table. A new id
would need the table copied somewhere larger, which means patching every place that loads its
address: `UI_SwitchPage`, `UI_DispatchMessage`, `UI_Init` (whose loop over all handlers is
hard-coded to 94), `UI_ForwardToPage`, `UI_Tick`, `Page_GetLevel` and `FUN_8000DBD0`. That's
possible, and nothing else seems to be sized per page id (`FUN_8003B398(page)` is empty), but
it's a lot of patches. Borrowing one id is a single data word, and it serves any number of our
pages, one at a time.

**The host id is 93**: a 0x38-byte placeholder page (`Page93_Handle`, slot 36), and no code
compares the current page against any id from 67 to 93. Patch `custom-pages` (part of every
patch that has a page) points record 93 at `cpage_host_slot`, a word in our payload holding
`cpage_dispatch`:

- message 0 (boot, `UI_Init`): reset our RAM state, pass on to Roland's handler;
- message 1 (build) while one of our pages was requested: build our XPage; otherwise
  Roland's handler builds his;
- message 2 (destroy): destroy and free ours. If the UI is leaving (not rebuilding, i.e.
  `g_page_request_seen` ≠ `0xFFFD`), the page is no longer requested;
- anything else goes to our object's `XPage_HandleMsg` (or Roland's handler if his page is up).

## Key functions and data

| Address | Name | What it does |
|---|---|---|
| `0x8003B530` | `UI_DispatchMessage` | sends key, encoder, knob, … messages to the current page; if it returns 0, defaults run (`UI_DefaultKeyDown`: EXIT → back) |
| `0x800D0040` | `UI_SwitchPage` | destroy the old page (message 2), build the new one (1), draw it (3) |
| `0x8003A968` | `UI_Tick` | sends message 8 every `g_tick_interval` ms (`UI_SetTickInterval`, XPage sets 50) |
| `0x800DA598` | `UI_RequestPage` | `(id)`: switch on the next UI pass |
| `0x800D1388` | `UI_RequestBack` | pop the back stack (`0xFFFE`) |
| `0x800C15B8` | `UI_RequestRebuild` | destroy and build the current page again (`0xFFFD`) |
| `0x800DD9F8` | `UI_RequestRefresh` | redraw the current page (message 3) |
| `0x800CE1D0` | `XPage_ctor` | base class: vtable `vt_XPage` (`0x80223AE8`), item container at `+0x38` |
| `0x800A80A8` | `XPage_HandleMsg` | vtable slot 8: message → virtual method (table below) |
| `0x80136028` | `XPage_DeletingDtor` | slot 4: destructs and frees any XPage |
| `0x80183338` | `DrawCtx_ctor` | a drawing context on the display surface `g_display_surface` |
| `0x800D08C0` | `Font_Get` | `g_fonts[id]`, 31 fonts |

## Details

### Messages and methods

A message is `UIMsg {short type, a, b}`. `XPage_HandleMsg` turns it into virtual calls. The
"raw" slots are XPage's own; each lets the page's items (the `XITEM_*` widgets of its
layout, at `+0x38`) see the event first and then calls the slot a screen overrides:

| msg | raw slot (XPage) | then | our callback |
|---|---|---|---|
| 1 build | rect 0,0,127,63; context at `+0x34`; `0x10` open → `0x114`, `0x108` → `0x118` layout | | `open` |
| 2 destroy | `0x14` → `0x11C` | | `close` |
| 3 draw | `Ctx_Begin`; `0x54`: items, `0xC8` | `Ctx_End` | `draw` (screen cleared first) |
| 4 update | `0x58`: items, `0xCC` | | `draw` if `cpage_redraw()` was called |
| 5 key down | `0x18`: SHIFT → `0xD4`, items, `0xD8` | defaults for EXIT, VALUE, cursor keys | `key_down` (EXIT → back if unhandled) |
| 6 key up | `0x1C`: items, `0xDC` | | `key_up` |
| 7 encoder | `0x20`: items, `0xE0` | unhandled VALUE turn scrolls the items | `encoder` |
| 8 tick | `0x5C`: `0xD0(50)`, items | | `tick`, then the draw `cpage_redraw()` asked for |
| 9 knob | `0x60` → `0xE4` | | — |

Our vtable is XPage's 0x158 bytes copied into RAM (`PAGE_RAM`, `app1/payload/ram.h`) with our
functions in `0xC8`, `0xCC`, `0xD0`, `0xD8`, `0xDC`, `0xE0`, `0x114`, `0x11C`. The layout slot
`0x118` stays XPage's empty one, so the page has no items and we draw everything ourselves.
Roland's screens override the same range (compare the vtables of the effect page,
`0x8021F868`, DJ mode, `0x80224A04`, the chromatic page, `0x802213F4`). The top screen
also overrides the raw key slots `0x18`/`0x1C`.

Key codes (message 5/6 `a`): pads `0x00`–`0x0F`, EXIT `0x22`, BANK A–E `0x25`–`0x29`,
SHIFT `0x2A`, effect buttons `0x2B`–`0x30`, VALUE press `0x31`, VALUE wheel as keys `0x3F`
(down) / `0x40` (up). Encoder 0 (message 7) is VALUE.

### Drawing

A drawing context (`DrawCtx`, 0xB4 bytes) holds the surface (`+0`) and an origin (`+0x14`,
`+0x18`). The `Ctx_*` functions add the origin and call the surface's vtable:

| function | surface slot | |
|---|---|---|
| `Ctx_SetInk(ctx, rgb)` | `0x0C` | colour; the 1-bit display maps RGB to on/off by brightness (`0xFFFFFF` on, `0` off) |
| `Ctx_SetColors(ctx, fg, bg)` | `0x20` | |
| `Ctx_SetFont(ctx, id)` | `0x58` | font 0..30 |
| `Ctx_FrameRect` / `Ctx_FillRect` / `Ctx_EraseRect` | `0xBC` / `0xC0` / `0xC4` | rect `{x0, y0, x1, y1}`, inclusive |
| `DrawText(ctx, x, y, s)` | `0x12C` | returns the x after the text |
| `DrawTextCentred(ctx, cx, y, s)` | `0x13C`, `0x12C` | |
| `Ctx_DrawTextRight(ctx, x, y, s)` | `0x130` | right-aligned at x, `\n` starts a new line |
| `Ctx_Begin` / `Ctx_End` | `0x24` / `0x1C` | save/restore the surface state (ink, font, …) |

Drawing marks the bitmap rows dirty and the display task sends them to the LCD
(`Bitmap_MarkDirty`). That also works from a tick, which `app1/payload/meter.c` already relies on.
`page.c` wraps it all as `gfx_*` (with a line function of our own; the surfaces have none).

Fonts (`g_fonts`, name, cell width × height):

| id | name | size | id | name | size |
|---|---|---|---|---|---|
| 0 | S | 6×7 | 15 | PBold | 9×9 |
| 1 | SS | 4×6 | 16 | RBioS | 22×12 |
| 2 | SS1 | 11×12 | 17 | S0 | 4×6 |
| 3 | BB1 | 12×14 | 18 | S1 | 6×6 |
| 4 | PP1 | 8×10 | 19 | S2 | 12×13 |
| 5 | L1 | 21×21 | 20 | S3 | 9×13 |
| 6 | L2 | 6×7 | 21–23 | P0, P1, P2 | 8×7, 6×7, 7×7 |
| 7 | L3 | 18×22 | 24 | LL2 | 11×13 |
| 8 | L4 | 15×16 | 25 | 7SEG | 34×48, characters 0..58 |
| 9 | L5 | 14×14 | 26–28 | 7SEG404SX, _I, 2 | 60×48, 34×48, 35×48 |
| 10–13 | L6–L9 | 15×21, 12×16, 15×16, 15×21 | 29, 30 | 3x5_P, 4x6_P | tiny |
| 14 | P | 6×7 | | | |

### Screen saver

The saver isn't a page: it draws over whatever page is current. Its countdown
(`g_saver_timer`, `0x80591B24`: `+4` time in ms, `+8` remaining = the `SAVER_REMAIN`
setting, `+0xC` enabled) is restarted by `UI_UserActivity` (`0x800D4960`), which
`UI_DispatchMessage` calls on every key, encoder and knob event and `UI_RequestPage` on
every page change. That call also ends a running saver (`Saver_Stop`, object in
`g_saver + 4`) and returns 1, which is why the waking key does nothing else. Roland's
screens don't change while nobody touches the SP, so they never fight with the saver. A
page with live content (the tuner) must call `cpage_user_activity()` while it shows
something new, or the saver starts and both draw.

## Open questions

- To check on the device: is `y` in the text calls the top of the text (assumed) or the
  baseline? Does text draw its background, or only the ink?
- Which characters do the 7SEG fonts have between `'0'` and 58 (`':'`)? Letters are
  missing, so note names need another font.
- Pads still play while one of our pages is open (XPage passes them on). Is that what we want
  for every page?
- Relocating `g_page_records` for real new ids, if one shared host ever isn't enough.
