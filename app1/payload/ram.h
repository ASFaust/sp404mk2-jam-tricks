#ifndef RAM_H
#define RAM_H

/* The payload has no writable globals. Its few words of state live in the top 8 KB of its own
   64 KB of SDRAM (payload.ld asserts the code ends below PAYLOAD_RAM_START). Until 2026-10-28
   they were in the tail of MPU region 9 (0x83FFBF60..), 8 bytes above Roland's highest use
   there, which grows between releases (docs/app1/topics/web-patcher.md).
   Normal cacheable memory (MPU region 8); only the CPU touches it, so that is safe.
   Not cleared at boot: every user checks a magic word. Every user gets its own block here. */
#define PAYLOAD_RAM_START 0x83FEE000u
#define PITCH_RAM 0x83FEE000u           /* pitch.c: state + ring: 0x1100 bytes */
#define PAGE_RAM 0x83FEF100u            /* page.c: 4 words + vtable copy (0x160): 0x180 bytes */
#define TUNER_RAM 0x83FEF280u           /* tuner.c: 0x40 bytes */
#define CHROM_RAM 0x83FEF2C0u           /* chrom.c: 3 words */
#define METER_RAM 0x83FEF2D0u           /* meter.c (and hooks.S hook_idle): 7 words */
#define PAYLOAD_RAM_END 0x83FEF2F0u

#endif
