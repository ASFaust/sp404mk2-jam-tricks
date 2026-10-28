/* Every address in Roland's firmware that the payload uses, with its value in v5.52.

   imports.S puts them in a table at the start of the payload (0x83FE0000), each word with its
   own symbol imp_<name>. The payload reads addresses only from there, so the web patcher can
   write the addresses it finds in another firmware version into the table and leave the rest
   of the payload as built (docs/app1/topics/web-patcher.md). make_image.py checks that no other
   payload code holds a Roland address.

   Names as in app1/tools/symbols.txt (and symbols_tallfree.txt); <global>_<field> for a field,
   <function>_cont for where a trampoline in hooks.S continues. Code addresses keep the Thumb
   bit. The order is the table's: new entries go at the end. */
#ifndef IMPORTS_H
#define IMPORTS_H

#define ROLAND_IMPORTS(X) \
    /* memory */ \
    X(operator_new,                 0x800D8B15) \
    X(operator_delete,              0x800E2CA9) \
    /* pages and the screen (page.c) */ \
    X(XPage_ctor,                   0x800CE1D1) \
    X(vt_XPage,                     0x80223AE8) /* slot 0; header (offset, RTTI) 8 bytes before */ \
    X(Page93_Handle,                0x80064751) \
    X(UI_RequestPage,               0x800DA599) \
    X(UI_RequestBack,               0x800D1389) \
    X(UI_RequestRebuild,            0x800C15B9) \
    X(UI_SetTickInterval,           0x800C7DA1) \
    X(UI_UserActivity,              0x800D4961) \
    X(UI_GetCurrentPage,            0x800D6381) \
    X(g_page_request_seen,          0x8024587E) \
    X(g_redraw_flags,               0x802496F0) \
    X(Ctx_Begin,                    0x800CE789) \
    X(Ctx_End,                      0x800CE759) \
    X(Ctx_SetInk,                   0x800CE781) \
    X(Ctx_SetFont,                  0x800D80D9) \
    X(Ctx_FillRect,                 0x800CE511) \
    X(Ctx_FrameRect,                0x800CE549) \
    X(DrawText,                     0x800D80E1) \
    X(DrawTextCentred,              0x800D4FA1) \
    X(Ctx_DrawTextRight,            0x800CE5B9) \
    X(Popup_Show,                   0x800D3FF1) \
    X(Page_PopupTime,               0x800C22C1) \
    X(Key_IsHeld,                   0x800D9F21) \
    /* parameters and pads */ \
    X(g_param_store,                0x82E009D0) \
    X(g_param_store_chrom_offset,   0x82E009FC) \
    X(g_param_store_chrom_scale,    0x82E00AE8) \
    X(g_param_store_chrom_root,     0x82E00AEC) \
    X(Param_Get,                    0x800DDA39) \
    X(Param_Set,                    0x800DED19) \
    X(g_pad_store,                  0x82E2CD08) /* 0xAC bytes per pad */ \
    X(PadStore_Get,                 0x800DC6B9) \
    X(PadStore_Set,                 0x800DA721) \
    X(g_pick_store,                 0x82E01144) \
    X(PickStore_Get,                0x800D94D9) \
    X(SampleInfo_ForPad,            0x800D8999) \
    /* chromatic mode (chrom.c, inkey.c, hooks.S) */ \
    X(ChromPage_OnKey,              0x801120D9) \
    X(ChromPage_OnPadDown,          0x80111B39) \
    X(ChromPage_BuildLayout,        0x80111DF9) \
    X(g_chrom_semis_trigger,        0x801AA0A0) \
    X(g_chrom_semis_pitch,          0x801AA0E0) \
    X(g_chrom_semis_page,           0x801AA120) \
    X(g_chrom_semis_lights,         0x80028F64) \
    X(Pad_Trigger_pitch_calls_lo,   0x800D73D0) /* return addresses of its Voice_SetParam calls */ \
    X(Pad_Trigger_pitch_calls_hi,   0x800D745B) \
    /* voices and time stretch (chrom.c, vari.c, tsx.c, hooks.S) */ \
    X(g_voice_slots,                0x202AE1A0) /* 32 slots of 0x58 bytes */ \
    X(Voice_Release,                0x800D5E79) \
    X(Voice_SetParam,               0x800A2C99) \
    X(Voice_SetParam_cont,          0x800A2CA1) /* after the 8 bytes chrom-voices replaces */ \
    X(Stretch_Init,                 0x800259E1) \
    X(Resampler_Stereo,             0x000140C5) \
    X(Resampler_Mono,               0x0001409B) \
    X(g_render_out_0,               0x20245500) /* Voice_RenderSetup's two output buffer sets */ \
    X(g_render_scratch_0,           0x20249500) /* and the conversion buffers of each */ \
    X(g_render_out_1,               0x2024D500) \
    X(g_render_scratch_1,           0x20251500) \
    /* tuner (tuner.c, pitch.c, hooks.S) */ \
    X(TopScreen_KeyDown,            0x801839B9) \
    X(g_bank_key_index,             0x8059259A) \
    X(g_bank_key_last,              0x8059259B) \
    X(g_bank_f_to_j,                0x805925B8) \
    X(ExtSource_Toggle,             0x80134A99) \
    X(Audio_RxHandler_cont,         0x0001CA8F) /* after the 14 bytes tuner replaces */ \
    X(g_audio_rx_buf,               0x20008000) \
    X(g_rx_force_word1,             0x82E011A8) \
    X(g_rec_ctrl_source,            0x82E0E974) \
    X(g_sys_time_ms,                0x202BCF20) \
    /* CPU meter (meter.c) */ \
    X(PanelTask_OnEvent,            0x80072CF1) \
    X(g_icon_draw_ctx,              0x80591B48)

#define IMPORTS_MAGIC 0x31504D49        /* "IMP1": then the entry count, then the entries */

#ifndef __ASSEMBLER__
#include <stdint.h>

#define IMPORT_DECLARE(name, addr) extern const uint32_t imp_##name;
ROLAND_IMPORTS(IMPORT_DECLARE)
#undef IMPORT_DECLARE

/* A Roland function or variable, e.g. ((int (*)(int))IMP(Key_IsHeld)) */
#define IMP(name) (imp_##name)
#endif

#endif
