/* Instruction counts of tsx_run under QEMU (-icount shift=0: 1 instruction = 1 ns of
   virtual time; SysTick runs at 25 MHz, i.e. 40 instructions per tick). Emulates
   Voice_RenderSetup's ring refill like app1/proto/stretch/tsx_c.py. Output via semihosting. */
#include <stdint.h>
#include "tsx.h"
#include "clip.h"                       /* CLIP_LEN stereo int16 frames: clip[] */

static TsxStretcher s;
static TsxState st;
static float scratch[TSX_SCRATCH_FLOATS];
uint32_t tsx_prof[8];
static int16_t out_l[0x800], out_r[0x800];

#define SYST_CSR (*(volatile uint32_t *)0xE000E010)
#define SYST_RVR (*(volatile uint32_t *)0xE000E014)
#define SYST_CVR (*(volatile uint32_t *)0xE000E018)

static void sh_write0(const char *str)
{
    register int r0 __asm__("r0") = 4;
    register const char *r1 __asm__("r1") = str;
    __asm__ volatile("bkpt 0xAB" : : "r"(r0), "r"(r1) : "memory");
}

static void sh_exit(void)
{
    register int r0 __asm__("r0") = 0x18;
    register int r1 __asm__("r1") = 0x20026;
    __asm__ volatile("bkpt 0xAB" : : "r"(r0), "r"(r1) : "memory");
}

static char *put_u(char *p, uint32_t v)
{
    char tmp[12];
    int n = 0;
    do { tmp[n++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (n) *p++ = tmp[--n];
    return p;
}

static void run(const char *name, uint32_t rate_q14, float ratio)
{
    uint8_t *z = (uint8_t *)&st;
    for (unsigned i = 0; i < sizeof st; i++) z[i] = 0;
    st.marker = -1;
    s.mode = 3;
    s.rate_q14 = rate_q14;
    s.ratio = ratio;
    s.ring_l.write_idx = s.ring_l.read_idx = 0;
    s.ring_r.write_idx = s.ring_r.read_idx = 0;
    for (int i = 0; i < 8; i++) tsx_prof[i] = 0;
    uint32_t src = 0, produced = 0;
    uint64_t ticks = 0;
    for (int blocks = 0; blocks < 4000; blocks++) {
        while (src < CLIP_LEN) {        /* refill to 60 % in 0x800 chunks */
            uint32_t w = s.ring_l.write_idx, r = s.ring_l.read_idx;
            uint32_t fill = (w + TSX_RING - r) % TSX_RING;
            if (fill * 100 / TSX_RING >= 60) break;
            uint32_t n = CLIP_LEN - src < 0x800 ? CLIP_LEN - src : 0x800;
            if (fill + n >= TSX_RING) break;
            for (uint32_t i = 0; i < n; i++) {
                uint32_t k = (w + i) % TSX_RING;
                s.ring_l.data[k] = (int32_t)clip[2 * (src + i)] << 13;
                s.ring_r.data[k] = (int32_t)clip[2 * (src + i) + 1] << 13;
            }
            s.ring_l.block_pos[w >> 11] = s.ring_r.block_pos[w >> 11] = (int32_t)src;
            s.ring_l.write_idx = s.ring_r.write_idx = (w + n) % TSX_RING;
            src += n;
        }
        int32_t pos;
        uint32_t t0 = SYST_CVR;
        int got = tsx_run(&s, &st, out_l, out_r, 0x800, &pos, 2, scratch);
        uint32_t t1 = SYST_CVR;
        ticks += (t0 - t1) & 0xFFFFFF;
        produced += (uint32_t)got;
        if (got < 0x800 && src >= CLIP_LEN) break;
    }
    char line[256], *p = line;
    for (const char *q = name; *q; q++) *p++ = *q;
    *p++ = ' ';
    p = put_u(p, produced);
    for (const char *q = " samples, instructions/sample: "; *q; q++) *p++ = *q;
    p = put_u(p, (uint32_t)(ticks * 40 / (produced ? produced : 1)));
    const char *names[5] = {" analyse ", " table ", " output ", " search ", " fadecorr "};
    for (int i = 0; i < 5; i++) {
        for (const char *q = names[i]; *q; q++) *p++ = *q;
        p = put_u(p, (uint32_t)((uint64_t)tsx_prof[i] * 40 / (produced ? produced : 1)));
    }
    for (const char *q = " plan% "; *q; q++) *p++ = *q;
    p = put_u(p, (uint32_t)((uint64_t)tsx_prof[5] * 100 / (produced ? produced : 1)));
    for (const char *q = " deep% "; *q; q++) *p++ = *q;
    p = put_u(p, (uint32_t)((uint64_t)tsx_prof[6] * 100 / (produced ? produced : 1)));
    *p++ = '\n';
    *p = 0;
    sh_write0(line);
}

int main(void)
{
    SYST_RVR = 0xFFFFFF;
    SYST_CVR = 0;
    SYST_CSR = 5;                       /* processor clock, enabled */
    run("time 0.8 ", 0x4000, 0.8f);
    run("time 1.25", 0x4000, 1.25f);
    run("pitch +7 ", 24548, 1.0f);      /* 2^(7/12) * 16384 */
    run("pitch +12", 32768, 1.0f);
    run("pitch -5 ", 12274, 1.0f);
    run("+5, 0.8  ", 21870, 0.8f);
    sh_exit();
    return 0;
}
