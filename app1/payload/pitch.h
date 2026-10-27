/* Pitch of the SP's mix (receive word 0), for the tuner (docs/app1/topics/tuner.md). */
#ifndef PITCH_H
#define PITCH_H

enum {
    PITCH_NONE,         /* silence */
    PITCH_UNSURE,       /* sound, but no clear pitch (an attack, noise): keep the last one */
    PITCH_OK,
};

/* Start/stop copying receive word 0 into the ring (24 kHz mono) on every receive block.
   Takes ~11 KB from Roland's heap while on. UI task only. */
void pitch_capture(int on);
/* Analyse the newest ~68 ms; with PITCH_OK, *hz is the frequency. UI task only. */
int pitch_detect(float *hz);

#endif
