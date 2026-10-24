#include "internal.hpp"
#include <limits>
extern "C" {
dt_handle *dt_create(const void *model, size_t bytes) {
  try {
    Model m;
    if ((model || bytes) && !read_model(model, bytes, m))
      return nullptr;
    auto h = new dt_handle;
    h->model = std::move(m);
    return h;
  } catch (...) {
    return nullptr;
  }
}
void dt_destroy(dt_handle *h) { delete h; }
int dt_reset(dt_handle *h, double origin) {
  if (!h || !std::isfinite(origin))
    return DT_INVALID;
  h->clear_acoustics(0);
  h->ready.clear();
  h->stats = {};
  h->origin = origin;
  h->started = h->finished = false;
  return DT_OK;
}
int dt_process(dt_handle *h, const float *mono, size_t count, int64_t first) {
  if (!h || (!mono && count) || count > DT_MAX_BLOCK || first < 0 ||
      first > std::numeric_limits<int64_t>::max() - 100000)
    return DT_INVALID;
  if (h->finished)
    return DT_FINISHED;
  if (!h->ready.empty())
    return DT_OVERFLOW;
  if (h->started && first < h->next)
    return DT_INVALID;
  for (size_t i = 0; i < count; ++i)
    if (!std::isfinite(mono[i]) || std::abs(mono[i]) > 1)
      return DT_INVALID;
  if (!count)
    return DT_OK;
  try {
    int status = DT_OK;
    if (!h->started) {
      h->clear_acoustics(first);
      h->started = true;
    } else if (first != h->next) {
      ++h->stats.gaps;
      h->stats.discarded_pending += h->pending.size();
      h->clear_acoustics(first);
      status = DT_GAP;
    }
    for (size_t i = 0; i < count; ++i) {
      h->audio.push_back(mono[i]);
      ++h->next;
      if (h->next >= h->frame + FFT_N)
        h->analyze_frame();
      h->collect();
      while (h->audio.size() > 24000) {
        h->audio.pop_front();
        ++h->base;
      }
    }
    return status;
  } catch (...) {
    return DT_OVERFLOW;
  }
}
int dt_finish(dt_handle *h) {
  if (!h)
    return DT_INVALID;
  if (h->finished)
    return DT_OK;
  try {
    // Analyze partial final windows with implicit zeros, without manufacturing
    // candidates at/after EOF. This preserves short recordings and final hits.
    while (h->frame < h->next)
      h->analyze_frame();
    while (!h->pending.empty() && h->pending.back() >= h->next)
      h->pending.pop_back();
    h->collect(true);
    h->finished = true;
    return DT_OK;
  } catch (...) {
    return DT_OVERFLOW;
  }
}
int dt_poll_candidate(dt_handle *h, dt_candidate *out) {
  if (!h || !out)
    return DT_INVALID;
  if (h->ready.empty())
    return 0;
  *out = h->ready.front();
  h->ready.pop_front();
  return 1;
}
int dt_get_stats(const dt_handle *h, dt_stats *out) {
  if (!h || !out)
    return DT_INVALID;
  *out = h->stats;
  return DT_OK;
}
}
