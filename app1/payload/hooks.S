/* Entry points Roland's code jumps to. make_image.py points vtable words and one switch
   entry at these symbols (see the PATCHES list there). */
#include "imports.h"
#include "ram.h"

        .syntax unified
        .thumb
        .text

/* The chromatic page's key and pad-down hooks are in chrom.c. */

/* Rebuild the tables first, then tail-call Roland's method (its import, imports.h) with all
   registers (and any stack arguments) untouched. Used for the layout build (slot 0x118). */
        .macro  update_then name, original
        .global \name
        .thumb_func
\name:
        push    {r0-r3, r12, lr}            @ six words: stack stays 8-byte aligned
        bl      inkey_update
        pop     {r0-r3, r12, lr}
        ldr     r12, =\original
        ldr.w   pc, [r12]
        .endm

        update_then hook_chrom_build, imp_ChromPage_BuildLayout  @ the page's layout build

/* Param_GetRange case for 0x9B (chromatic pitch range), reached through the switch entry
   and the jump at 0x800ED544. Same registers as Roland's case: r5 = &min, r4 = &max, and
   {r4, r5, r6, lr} are on the stack. */
        .global range_chrom_24
        .thumb_func
range_chrom_24:
        mvn     r0, #23                     @ -24
        str     r0, [r5]
        movs    r0, #24
        str     r0, [r4]
        pop     {r4, r5, r6, pc}

        .ltorg

/* Param_GetRange case for 0xF6 (VARI: Off/Backing/Ensemble + our mode 3), reached through
   the switch entry and the jump at 0x800D5784. Registers as in Roland's cases: r5 = &min,
   r4 = &max, {r4, r5, r6, lr} on the stack. */
        .global range_vari4
        .thumb_func
range_vari4:
        movs    r0, #0
        str     r0, [r5]
        movs    r0, #3
        str     r0, [r4]
        pop     {r4, r5, r6, pc}

/* The kernel's idle function (ITCM 0xE2D0, called by the dispatcher in PendSV while no task
   is ready), redirected here by the cpu-meter patch. Does what Roland's does (BASEPRI 0xF0,
   dsb, wfi, BASEPRI back to 0x60) and adds the time asleep to IDLE_ACC (meter.c).
   Handler mode: integer registers only. */
        .global hook_idle
        .thumb_func
hook_idle:
        push    {r4, lr}
        bl      meter_now
        mov     r4, r0
        movs    r0, #0xF0
        msr     basepri, r0
        dsb
        wfi
        movs    r0, #0x60
        msr     basepri, r0
        bl      meter_now
        subs    r0, r0, r4
        ldr     r1, =METER_RAM              @ IDLE_ACC (meter.c)
        ldr     r2, [r1]
        add     r2, r2, r0
        str     r2, [r1]
        pop     {r4, pc}

        .ltorg

/* Roland's Voice_SetParam (0x800A2C98), whose first 8 bytes the chrom-voices patch replaced
   with a jump to hook_voice_set_param: run those four instructions here, then continue at
   0x800A2CA0 ('it eq; popeq', so the flags of the cmp must survive: ldr doesn't change them). */
        .global voice_set_param_orig
        .thumb_func
voice_set_param_orig:
        push    {r4, r5, r7, lr}
        mov     r5, r0
        ldr     r0, [r0]
        cmp     r0, #0
        ldr     r12, =imp_Voice_SetParam_cont   @ r12 is free here (AAPCS: may change at a call)
        ldr.w   pc, [r12]

        .ltorg

/* Roland's Audio_RxHandler (ITCM 0x1CA80), whose first 10 bytes the tuner patch replaced with
   a jump to hook_audio_rx (pitch.c): run those four instructions here, then continue at
   0x1CA8A (the movt that completes r0). */
        .global audio_rx_orig
        .thumb_func
audio_rx_orig:
        push    {r4, r5, r6, r7, lr}
        sub     sp, #4
        mov     r4, r0
        movw    r0, #0x9d0
        ldr     r12, =imp_Audio_RxHandler_cont  @ r12 is free here, as above
        ldr.w   pc, [r12]

        .ltorg
