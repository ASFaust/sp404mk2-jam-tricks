/* Custom pages (page.h; docs/app1/topics/custom-pages.md).
 *
 * How a firmware page works: UI_SwitchPage calls the handler of the page record for the
 * page id with messages {type, a, b} (1 build, 2 destroy, 3 draw, 4 update, 5/6 key
 * down/up, 7 encoder, 8 tick, ...). Roland's handlers create an XPage object on 1 and send
 * everything to its vtable slot 8, XPage's message handler (0x800A80A8), which calls the
 * page's virtual methods (slots 0xC8 draw ... 0x11C close).
 *
 * The host page id's record points at cpage_host_slot (patch "custom-pages"), so
 * cpage_dispatch sees every message for that id. While one of our pages is requested or
 * open it builds an XPage with our vtable; otherwise it passes the message on to Roland's
 * handler for the id. The vtable is XPage's, copied into RAM with our methods in the
 * slots pages normally override.
 */
#include <stdint.h>

#include "imports.h"
#include "page.h"
#include "ram.h"

#define REG(a) (*(volatile uint32_t *)(a))

/* Firmware functions (imports.h) */
#define op_new ((void *(*)(uint32_t))IMP(operator_new))
#define XPage_ctor ((void *(*)(void *))IMP(XPage_ctor))
#define UI_RequestPage ((void (*)(int))IMP(UI_RequestPage))
#define UI_RequestBack ((int (*)(void))IMP(UI_RequestBack))
#define UI_RequestRebuild ((void (*)(void))IMP(UI_RequestRebuild))
#define UI_SetTickInterval ((void (*)(int))IMP(UI_SetTickInterval))
#define HOST_HANDLER ((int (*)(int16_t *))IMP(Page93_Handle))  /* Roland's handler for page 93 */
#define UI_UserActivity ((int (*)(void))IMP(UI_UserActivity))  /* saver countdown reset, saver off */
#define Ctx_Begin ((void (*)(cpage_ctx *))IMP(Ctx_Begin))      /* saves the surface state */
#define Ctx_End ((void (*)(cpage_ctx *))IMP(Ctx_End))
#define Ctx_SetInk ((void (*)(cpage_ctx *, uint32_t))IMP(Ctx_SetInk))         /* surface slot 0x0C */
#define Ctx_SetFont ((void (*)(cpage_ctx *, int))IMP(Ctx_SetFont))            /* surface slot 0x58 */
#define Ctx_FillRect ((void (*)(cpage_ctx *, int32_t *))IMP(Ctx_FillRect))    /* slot 0xC0 */
#define Ctx_FrameRect ((void (*)(cpage_ctx *, int32_t *))IMP(Ctx_FrameRect))  /* slot 0xBC */
#define Ctx_DrawText ((int (*)(cpage_ctx *, int, int, const char *))IMP(DrawText))
#define Ctx_DrawTextCentred ((void (*)(cpage_ctx *, int, int, const char *))IMP(DrawTextCentred))
#define Ctx_DrawTextRight ((void (*)(cpage_ctx *, int, int, const char *))IMP(Ctx_DrawTextRight))

#define PAGE_REQUEST_SEEN (*(volatile uint16_t *)IMP(g_page_request_seen))  /* request UI_SwitchPage acts on */
#define REQUEST_REBUILD 0xFFFD

#define XPAGE_VTABLE IMP(vt_XPage)      /* slot 0; header (offset, RTTI) 8 bytes before */
#define XPAGE_VTABLE_SIZE 0x158u
#define PAGE_SIZE 0x1A6Cu               /* as the effect page (XPage uses up to 0x1A6A) */
#define PAGE_CTX(obj) (((cpage_ctx **)(obj))[0x34 / 4])  /* built by XPage on message 1 */

/* State (RAM, ram.h). Message 0 (sent to every page handler at boot) resets it. */
#define MAGIC_REG REG(PAGE_RAM + 0x00)
#define ACTIVE REG(PAGE_RAM + 0x04)     /* const struct cpage *: requested or open */
#define OBJ REG(PAGE_RAM + 0x08)        /* our XPage object while it exists */
#define DIRTY REG(PAGE_RAM + 0x0C)      /* draws still owed: cpage_redraw() 1, open 2 */
#define VTABLE ((uint32_t *)(PAGE_RAM + 0x20 + 8))      /* 8-byte header, then the slots */
#define MAGIC 0x50414745u               /* "PAGE" */

static const struct cpage *active(void)
{
    return (const struct cpage *)ACTIVE;
}

static void draw_now(void *obj)
{
    const struct cpage *p = active();
    cpage_ctx *ctx = PAGE_CTX(obj);
    if (!ctx)
        return;
    if (DIRTY)
        DIRTY--;
    gfx_clear(ctx);
    if (p && p->draw)
        p->draw(ctx);
}

/* --- our XPage methods (self = the page object) --- */

static void m_draw(void *self)          /* 0xC8: message 3, inside Ctx_Begin/End */
{
    draw_now(self);
}

static void m_update(void *self)        /* 0xCC: message 4 */
{
    if (DIRTY)
        draw_now(self);
}

static void m_tick(void *self, int ms)  /* 0xD0: message 8 */
{
    const struct cpage *p = active();
    if (p && p->tick)
        p->tick(ms);
    if (DIRTY && PAGE_CTX(self)) {
        Ctx_Begin(PAGE_CTX(self));
        draw_now(self);
        Ctx_End(PAGE_CTX(self));
    }
}

static int m_key_down(void *self, int key)     /* 0xD8 */
{
    (void)self;
    const struct cpage *p = active();
    if (p && p->key_down && p->key_down(key))
        return 1;
    if (key == KEY_EXIT) {
        cpage_close();
        return 1;
    }
    return 0;
}

static int m_key_up(void *self, int key)       /* 0xDC */
{
    (void)self;
    const struct cpage *p = active();
    return p && p->key_up ? p->key_up(key) : 0;
}

static int m_encoder(void *self, int index, int delta)     /* 0xE0 */
{
    (void)self;
    const struct cpage *p = active();
    return p && p->encoder ? p->encoder(index, delta) : 0;
}

static void m_open(void *self)          /* 0x114: from XPage's open, after its own setup */
{
    (void)self;
    const struct cpage *p = active();
    if (p && p->tick_ms > 0)
        UI_SetTickInterval(p->tick_ms);
    if (p && p->open)
        p->open();
    /* Two draws: the one of message 3 is wiped after the page switch (seen on the device:
       black until the first redraw), so the first tick draws again. */
    DIRTY = 2;
}

static void m_close(void *self)         /* 0x11C: from XPage's destroy */
{
    (void)self;
    const struct cpage *p = active();
    if (p && p->close)
        p->close();
}

static void set_slot(uint32_t offset, void *fn)
{
    VTABLE[offset / 4] = (uint32_t)fn;
}

static void *new_page(void)
{
    uint32_t *obj = op_new(PAGE_SIZE);
    if (!obj)
        return 0;
    for (uint32_t i = 0; i < PAGE_SIZE / 4; i++)
        obj[i] = 0;
    XPage_ctor(obj);

    const uint32_t *xpage = (const uint32_t *)XPAGE_VTABLE;
    VTABLE[-2] = VTABLE[-1] = 0;                /* offset to top, RTTI */
    for (uint32_t i = 0; i < XPAGE_VTABLE_SIZE / 4; i++)
        VTABLE[i] = xpage[i];
    set_slot(0xC8, m_draw);
    set_slot(0xCC, m_update);
    set_slot(0xD0, m_tick);
    set_slot(0xD8, m_key_down);
    set_slot(0xDC, m_key_up);
    set_slot(0xE0, m_encoder);
    set_slot(0x114, m_open);
    set_slot(0x11C, m_close);
    obj[0] = (uint32_t)VTABLE;
    return obj;
}

typedef int (*method_msg)(void *, int16_t *);
typedef void (*method_void)(void *);

static int send(void *obj, int16_t *msg)        /* vtable slot 8: XPage's message handler */
{
    return ((method_msg)(*(uint32_t **)obj)[8 / 4])(obj, msg);
}

/* The page record of CPAGE_HOST_ID points here (a "slot" holding the handler address). */
static int cpage_dispatch(int16_t *msg);
const void *const cpage_host_slot = (const void *)cpage_dispatch;

static int cpage_dispatch(int16_t *msg)
{
    int type = msg[0];
    if (type == 0 || MAGIC_REG != MAGIC) {      /* boot: RAM holds garbage until now */
        MAGIC_REG = MAGIC;
        ACTIVE = OBJ = DIRTY = 0;
        return HOST_HANDLER(msg);
    }
    if (type == 1) {
        if (!ACTIVE)
            return HOST_HANDLER(msg);
        void *obj = new_page();
        if (!obj) {
            ACTIVE = 0;
            return HOST_HANDLER(msg);
        }
        OBJ = (uint32_t)obj;
        return send(obj, msg);
    }
    void *obj = (void *)OBJ;
    if (!obj)
        return HOST_HANDLER(msg);
    if (type == 2) {
        int r = send(obj, msg);
        ((method_void)(*(uint32_t **)obj)[4 / 4])(obj);     /* deleting destructor */
        OBJ = 0;
        if (PAGE_REQUEST_SEEN != REQUEST_REBUILD)          /* leaving, not rebuilding */
            ACTIVE = 0;
        return r;
    }
    return send(obj, msg);
}

/* --- API --- */

void cpage_open(const struct cpage *page)
{
    if (MAGIC_REG != MAGIC)
        return;
    ACTIVE = (uint32_t)page;
    if (OBJ)
        UI_RequestRebuild();            /* the host id is showing: build it again as page */
    else
        UI_RequestPage(CPAGE_HOST_ID);
}

void cpage_close(void)
{
    if (OBJ)
        UI_RequestBack();
}

void cpage_redraw(void)
{
    if (!DIRTY)
        DIRTY = 1;
}

const struct cpage *cpage_current(void)
{
    return MAGIC_REG == MAGIC && OBJ ? active() : 0;
}

void cpage_user_activity(void)
{
    UI_UserActivity();
}

/* --- drawing --- */

void gfx_color(cpage_ctx *ctx, uint32_t rgb)
{
    Ctx_SetInk(ctx, rgb);
}

void gfx_font(cpage_ctx *ctx, int font)
{
    Ctx_SetFont(ctx, font);
}

void gfx_fill(cpage_ctx *ctx, int x0, int y0, int x1, int y1)
{
    int32_t r[4] = {x0, y0, x1, y1};
    Ctx_FillRect(ctx, r);
}

void gfx_frame(cpage_ctx *ctx, int x0, int y0, int x1, int y1)
{
    int32_t r[4] = {x0, y0, x1, y1};
    Ctx_FrameRect(ctx, r);
}

/* Bresenham, one rectangle per straight run. */
void gfx_line(cpage_ctx *ctx, int x0, int y0, int x1, int y1)
{
    int dx = x1 > x0 ? x1 - x0 : x0 - x1, sx = x0 < x1 ? 1 : -1;
    int dy = y1 > y0 ? y0 - y1 : y1 - y0, sy = y0 < y1 ? 1 : -1;
    int err = dx + dy, rx = x0, ry = y0;
    for (;;) {
        int last = x0 == x1 && y0 == y1;
        int e2 = 2 * err, nx = x0, ny = y0;
        if (!last) {
            if (e2 >= dy) {
                err += dy;
                nx += sx;
            }
            if (e2 <= dx) {
                err += dx;
                ny += sy;
            }
        }
        /* flush the run when the next point leaves its row and column */
        if (last || (nx != rx && ny != ry)) {
            gfx_fill(ctx, rx < x0 ? rx : x0, ry < y0 ? ry : y0, rx > x0 ? rx : x0, ry > y0 ? ry : y0);
            rx = nx;
            ry = ny;
        }
        if (last)
            return;
        x0 = nx;
        y0 = ny;
    }
}

void gfx_clear(cpage_ctx *ctx)
{
    gfx_color(ctx, GFX_OFF);
    gfx_fill(ctx, 0, 0, GFX_W - 1, GFX_H - 1);
    gfx_color(ctx, GFX_ON);
}

int gfx_text(cpage_ctx *ctx, int x, int y, const char *s)
{
    return Ctx_DrawText(ctx, x, y, s);
}

void gfx_text_centred(cpage_ctx *ctx, int cx, int y, const char *s)
{
    Ctx_DrawTextCentred(ctx, cx, y, s);
}

void gfx_text_right(cpage_ctx *ctx, int x, int y, const char *s)
{
    Ctx_DrawTextRight(ctx, x, y, s);
}
