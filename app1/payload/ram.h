#ifndef RAM_H
#define RAM_H

/* The payload has no writable globals. Its few words of state live at the end of the
   non-cacheable SDRAM region 0x83FF0000..0x84000000 (MPU region 9), which the firmware
   only uses up to 0x83FFBF58 and nothing refers to beyond (docs/app1/topics/cpu-meter.md).
   Every user gets its own block here. */
#define PAYLOAD_RAM_START 0x83FFBF60u
#define PITCH_RAM 0x83FFC000u           /* pitch.c: state + ring: 0x1100 bytes */
#define PAGE_RAM 0x83FFFD00u            /* page.c: 4 words + vtable copy (0x160): 0x180 bytes */
#define TUNER_RAM 0x83FFFE80u           /* tuner.c: 0x40 bytes */
#define CHROM_RAM 0x83FFFF80u           /* chrom.c: 3 words */
#define METER_RAM 0x83FFFFC0u           /* meter.c (and hooks.S hook_idle): 7 words */

#endif
