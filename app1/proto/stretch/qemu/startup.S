/* Minimal Cortex-M7 start for QEMU mps2-an500: vector table, enable the FPU, run main. */
        .syntax unified
        .thumb
        .section .vectors, "a"
        .word   __stack_top
        .word   reset + 1
        .rept   14
        .word   hang + 1
        .endr

        .text
        .thumb_func
reset:
        ldr     r0, =0xE000ED88             @ CPACR: full access to CP10/CP11 (FPU)
        ldr     r1, [r0]
        orr     r1, r1, #(0xF << 20)
        str     r1, [r0]
        dsb
        isb
        bl      main
        .thumb_func
hang:   b       hang
