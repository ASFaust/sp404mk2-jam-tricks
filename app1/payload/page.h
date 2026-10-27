/* Custom pages: full screens of our own, built like Roland's (docs/app1/topics/custom-pages.md).
 *
 * A feature describes its page with a const struct cpage and opens it with cpage_open().
 * The page is a real firmware XPage object, so the back stack, EXIT, pop-ups and redraws
 * behave as on any other screen. It borrows the page id CPAGE_HOST_ID: that id's record
 * points at our dispatcher (patch "custom-pages"), which forwards to Roland's page there
 * whenever none of ours is open.
 *
 * Every callback is optional (0). The ctx passed to draw/update is the page's drawing
 * context (full screen, origin 0,0); use the gfx_* functions below on it.
 */
#ifndef PAGE_H
#define PAGE_H

#include <stdint.h>

#define CPAGE_HOST_ID 93        /* a placeholder page nothing compares against */

/* Key codes (message types 5/6). Pads are 0x00..0x0F. */
#define KEY_EXIT 0x22
#define KEY_BANK_A 0x25         /* .. BANK E 0x29 */
#define KEY_BANK_B 0x26
#define KEY_SHIFT 0x2A
#define KEY_VALUE_PRESS 0x31
#define KEY_WHEEL_DOWN 0x3F     /* VALUE wheel, as keys (as the chromatic page reads it) */
#define KEY_WHEEL_UP 0x40

typedef struct cpage_ctx cpage_ctx;     /* Roland's drawing context, opaque */

struct cpage {
    void (*open)(void);                 /* the page was built (before the first draw) */
    void (*close)(void);                /* the page is going away */
    void (*draw)(cpage_ctx *ctx);       /* draw everything; the screen is cleared first */
    void (*tick)(int ms);               /* every tick_ms (default 50) */
    int (*key_down)(int key);           /* return 1 if handled; unhandled EXIT goes back */
    int (*key_up)(int key);
    int (*encoder)(int index, int delta);   /* turn of an encoder; index 0 = VALUE */
    int tick_ms;                        /* 0 = Roland's 50 ms */
};

/* Open a page (from a key handler or tick; the switch happens on the next UI pass). */
void cpage_open(const struct cpage *page);
/* Close the open page: back to the screen it was opened from. */
void cpage_close(void);
/* Ask for a draw() of the open page; it runs on the UI task, at most once per request. */
void cpage_redraw(void);
/* The page that is open and showing, or 0. */
const struct cpage *cpage_current(void);
/* Count as user activity, as a key press does: restarts the screen saver's countdown and
   ends the saver if it's showing. For pages with live content and no keys pressed (the saver
   draws over the page and the two would fight). */
void cpage_user_activity(void);

/* Drawing. The display is 128 x 64, one bit per pixel; rectangles are inclusive. */
#define GFX_W 128
#define GFX_H 64
#define GFX_ON 0xFFFFFFu        /* RGB: the surfaces map colours to on/off by brightness */
#define GFX_OFF 0x000000u

/* Roland's fonts (table at 0x80249A68), with cell width x height. */
enum {
    FONT_S = 0,         /*  6 x 7  */
    FONT_SS1 = 2,       /* 11 x 12 */
    FONT_BB1 = 3,       /* 12 x 14 */
    FONT_L1 = 5,        /* 21 x 21 */
    FONT_L3 = 7,        /* 18 x 22 */
    FONT_PBOLD = 15,    /*  9 x 9  */
    FONT_S2 = 19,       /* 12 x 13 */
    FONT_7SEG = 25,     /* 34 x 48, characters 0..58: digits and a few signs */
    FONT_3X5 = 29,      /* tiny */
};

void gfx_color(cpage_ctx *ctx, uint32_t rgb);
void gfx_font(cpage_ctx *ctx, int font);
void gfx_fill(cpage_ctx *ctx, int x0, int y0, int x1, int y1);
void gfx_frame(cpage_ctx *ctx, int x0, int y0, int x1, int y1);
void gfx_line(cpage_ctx *ctx, int x0, int y0, int x1, int y1);
void gfx_clear(cpage_ctx *ctx);                 /* all off, colour back to GFX_ON */
int gfx_text(cpage_ctx *ctx, int x, int y, const char *s);     /* returns the x after it */
void gfx_text_centred(cpage_ctx *ctx, int cx, int y, const char *s);
void gfx_text_right(cpage_ctx *ctx, int x, int y, const char *s);

#endif
