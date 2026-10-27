/* VARI changes on a playing pad, without the restart (patch "vari4-stretch",
 * docs/app1/topics/time-stretch.md, "Changing VARI while a pad plays").
 *
 * When pad param 0x70's VARI bits (1-2) change, PadStore_Set (0x800DBDC4) stops the pad's
 * voices and triggers them again from the start (FUN_800D6390 + FUN_800D7668); other changes
 * of the word go to Voice_PushParam, and Voice_SetParam case 0x70 already switches a voice's
 * Stretcher.mode, period and crossfade tables in place. The patch calls hook_vari_changed
 * instead of the restart, which does that for each voice of the pad. Ensemble is left out:
 * its voices hold a second slot (Pad_Trigger, Voice_Start), so a voice that plays Ensemble,
 * or would, keeps its mode until the next hit.
 *
 * tsx_resampler_* re-initialise Roland's stretch state when a voice comes back from Plus
 * (vari_leave_tsx below), since our TsxState lies over it.
 */
#include <stdint.h>

#include "imports.h"
#include "tsx.h"

#define Voice_SetParam ((void (*)(void *, int, uint32_t))IMP(Voice_SetParam))
#define Stretch_Init ((void (*)(void *, void *, int))IMP(Stretch_Init))

#define VOICE_SLOTS IMP(g_voice_slots)  /* 32 slots of 0x58 bytes */
#define N_SLOTS 32
#define SLOT_SIZE 0x58
#define SLOT_VOICE 0x00                 /* SmplCharge* (the voice), 0 = none */
#define SLOT_STATE 0x04                 /* -> state word: 0 free, 1 playing, 2 Ensemble's second */
#define SLOT_SECOND 0x08                /* the Ensemble second slot, or 0 */
#define SLOT_TYPE 0x2C                  /* trigger type; 0xC chromatic note */
#define SLOT_CHROM_PAD 0x48             /* chromatic notes: the chromatic pad */
#define VOICE_PAD 0x14274
#define VOICE_MODE (0x140 + 0x1C)       /* Stretcher.mode */
#define TYPE_CHROM 0xC
#define VARI_ENSEMBLE 2
#define VARI(word) (((word) & 7) >> 1)

#define U32(a) (*(volatile uint32_t *)(a))

/* PadStore_Set, pad param 0x70 with new VARI bits: value is the new word (already stored). */
void hook_vari_changed(int pad, uint32_t value)
{
    if (VARI(value) == VARI_ENSEMBLE)
        return;
    for (int i = 0; i < N_SLOTS; i++) {
        uint32_t slot = VOICE_SLOTS + i * SLOT_SIZE;
        uint32_t voice = U32(slot + SLOT_VOICE);
        if (!voice || U32(U32(slot + SLOT_STATE)) != 1)
            continue;
        /* the voices Voice_PushParam would reach: the pad's, and its chromatic notes */
        if ((int)U32(voice + VOICE_PAD) != pad
            && !((int)U32(slot + SLOT_CHROM_PAD) == pad && U32(slot + SLOT_TYPE) == TYPE_CHROM))
            continue;
        if (U32(slot + SLOT_SECOND) || U32(voice + VOICE_MODE) == VARI_ENSEMBLE)
            continue;
        Voice_SetParam((void *)slot, 0x70, value);
    }
}

/* From tsx_resampler_*, for a voice not in mode 3: if the state still holds ours (the voice
   was switched away from Plus), set it up for Roland's resampler as a voice start would
   (Stretch_Init, then the period SmplCharge_Init gives the mode). */
void vari_leave_tsx(TsxStretcher *s)
{
    TsxState *st = (TsxState *)(uintptr_t)s->state;
    if (!st || st->marker != TSX_MARKER)
        return;
    int mode = s->mode;
    Stretch_Init(s, st, mode);          /* also sets the marker to -1 */
    st->_period = mode == 0 ? 600 : mode == 1 ? 400 : 800;
}
