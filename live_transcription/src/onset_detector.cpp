#include "internal.hpp"
void dt_handle::clear_acoustics(int64_t start) {
  audio.clear();
  pending.clear();
  base = next = segment = start;
  frame = start;
  previous = {};
  prev_flux = prev_prev_flux = mean_flux = prev_gate = 0;
  last_onset = start - 100000;
  previous_frame = start;
}
void dt_handle::analyze_frame() {
  Bands now = spectrum(frame);
  float flux = 0;
  for (int b = 0; b < BANDS; ++b)
    flux += std::max(0.f, now[b] - previous[b]);
  flux /= BANDS;
  // A causal local peak with an adaptive threshold. Parameters are a baseline,
  // evaluated on held-out recordings, not claimed universal drum thresholds.
  if (prev_flux > prev_prev_flux && prev_flux >= flux &&
      prev_flux > prev_gate) {
    int64_t lo = std::max(segment, previous_frame), hi = previous_frame + FFT_N;
    // Refine the coarse FFT candidate using the first strong amplitude rise.
    float peak = 0;
    for (int64_t i = lo; i < hi; ++i)
      peak = std::max(peak, std::abs(sample(i) - sample(i - 1)));
    int64_t onset = lo;
    for (int64_t i = lo; i < hi; ++i)
      if (std::abs(sample(i) - sample(i - 1)) >= peak * 0.2f) {
        onset = i;
        break;
      }
    if (onset - last_onset >= 960) {
      pending.push_back(onset);
      last_onset = onset;
    }
  }
  prev_prev_flux = prev_flux;
  prev_flux = flux;
  prev_gate = std::max(0.003f, mean_flux * 2.0f);
  mean_flux = 0.95f * mean_flux + 0.05f * flux;
  previous = now;
  previous_frame = frame;
  frame += HOP;
}
void dt_handle::collect(bool eof) {
  constexpr int offsets[8] = {-480, 0, 480, 960, 1920, 3360, 4800, 6720};
  while (!pending.empty() && (eof || pending.front() + LOOKAHEAD <= next)) {
    dt_candidate c{};
    c.sample = pending.front();
    pending.pop_front();
    c.time_s = origin + double(c.sample) / DT_SAMPLE_RATE;
    c.available_sample = next;
    for (int j = 0; j < 8; ++j) {
      auto f = spectrum(c.sample + offsets[j] - FFT_N / 2);
      std::copy(f.begin(), f.end(), c.features + j * BANDS);
    }
    classify(model, c);
    ++stats.candidates;
    if (!c.family_mask)
      ++stats.rejected;
    ready.push_back(c);
  }
}
