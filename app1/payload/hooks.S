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

/* Roland's Audio_RxHandler (ITCM 0x1CA80), whose first 14 bytes the tuner patch replaced with
   a jump to hook_audio_rx (pitch.c): do what they did here, then continue at 0x1CA8E. The last
   two built r0 = g_param_store with movw/movt; that address differs between releases, so it
   comes from the import table. */
        .global audio_rx_orig
        .thumb_func
audio_rx_orig:
        push    {r4, r5, r6, r7, lr}
        sub     sp, #4
        mov     r4, r0
        ldr     r0, =imp_g_param_store
        ldr     r0, [r0]
        ldr     r12, =imp_Audio_RxHandler_cont  @ r12 is free here, as above
        ldr.w   pc, [r12]

        .ltorg

/* The Jam Tricks version after Roland's wherever the firmware shows it, as in
   "Version: 5.52+jam-tricks-v1.0". Roland's own string stays as it is: the first four
   characters also go out over MIDI and USB (SysEx reply, FUN_80115db8 and FUN_800f6f80).
   Both formats were loaded with 'adr', which can't reach us, so each site jumps here instead
   and continues after the replaced instructions with the same registers. JAM_TRICKS_VERSION
   comes from make_image.py. */

/* FUN_8014b120 draws "Version: %s": replaces 'movw r0; mov r5, sp; movt r0; adr r1, fmt;
   ldr r2, [r0]' at 0x8014B13E and continues at 0x8014B14C (mov r0, r5; bl sprintf). */
        .global hook_version_text
        .thumb_func
hook_version_text:
        mov     r5, sp
        ldr     r1, =version_text_fmt
        ldr     r2, =imp_g_app1_version
        ldr     r2, [r2]                    @ &g_app1_version
        ldr     r2, [r2]                    @ "5.52"
        ldr     r12, =imp_VersionText_cont
        ldr.w   pc, [r12]

/* FUN_800f72d8's info screen line "APP1: %s (%s)": replaces the instructions from 0x800F7416
   that load both strings, the format and the two arguments; continues at 0x800F7436 (bl). */
        .global hook_app1_line
        .thumb_func
hook_app1_line:
        ldr     r1, =imp_g_app1_version
        ldr     r1, [r1]
        ldr     r1, [r1]                    @ "5.52"
        ldr     r7, =imp_g_app1_build
        ldr     r7, [r7]
        ldr     r7, [r7]                    @ the build hash
        ldr     r0, [r6]
        ldr     r3, =app1_line_fmt
        str     r1, [sp]
        movs    r1, #1
        movs    r2, #3
        str     r7, [sp, #4]
        ldr     r12, =imp_App1Line_cont
        ldr.w   pc, [r12]

        .ltorg

        .section .rodata.str.version, "a"
        .global version_text_fmt, app1_line_fmt
version_text_fmt:
        .ascii  "Version: %s+jam-tricks-v", JAM_TRICKS_VERSION
        .byte   0
app1_line_fmt:
        .ascii  "APP1: %s+jam-tricks-v", JAM_TRICKS_VERSION, " (%s)"
        .byte   0
