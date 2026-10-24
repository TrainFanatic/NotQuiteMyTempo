#ifndef DRUM_TRANSCRIBER_H
#define DRUM_TRANSCRIBER_H
#include <stddef.h>
#include <stdint.h>
#ifdef _WIN32
#define DT_API __declspec(dllexport)
#else
#define DT_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
#define DT_SAMPLE_RATE 48000
#define DT_FEATURES 256
#define DT_MAX_CLASSES 16
#define DT_MAX_BLOCK 4800
/* Return codes: OK, discontinuity accepted (pending context lost), or error.
 * Every handle is single-thread owned. Process on a worker, not the audio
 * callback. Poll after every block. No filesystem access, clock, or audio
 * device in the core. */
enum {
  DT_OK = 0,
  DT_GAP = 1,
  DT_INVALID = -1,
  DT_OVERFLOW = -2,
  DT_FINISHED = -3
};
typedef struct dt_handle dt_handle;
typedef struct {
  int64_t sample; /* absolute capture sample index */
  double time_s;  /* song_origin_s + sample / 48000 */
  int64_t
      available_sample; /* sample count received when candidate became ready */
  uint32_t class_count;
  uint32_t family_mask; /* independent thresholds; zero means rejected */
  int32_t midi[DT_MAX_CLASSES];
  float probability[DT_MAX_CLASSES];
  float features[DT_FEATURES];
} dt_candidate;
typedef struct {
  uint64_t candidates, rejected, gaps, discarded_pending;
} dt_stats;
/* NULL/0 model enables onset/features only, for annotation and training.
 * Model bytes are copied/validated. NULL is returned for an invalid model. */
DT_API dt_handle *dt_create(const void *model, size_t bytes);
DT_API void dt_destroy(dt_handle *h);
/* origin is the song time of capture sample zero (can be negative for
 * count-in). */
DT_API int dt_reset(dt_handle *h, double song_origin_s);
/* Rejects nonfinite/out-of-range PCM, backwards samples, and >DT_MAX_BLOCK.
 * A forward gap resets acoustic state and retains the absolute capture clock.
 */
DT_API int dt_process(dt_handle *h, const float *mono, size_t count,
                      int64_t first_sample);
/* Zero-pad pending context at EOF; no new candidates from padding. Idempotent.
 */
DT_API int dt_finish(dt_handle *h);
/* 1 candidate, 0 empty, negative error. Drain before the next process call. */
DT_API int dt_poll_candidate(dt_handle *h, dt_candidate *out);
DT_API int dt_get_stats(const dt_handle *h, dt_stats *out);
#ifdef __cplusplus
}
#endif
#endif
