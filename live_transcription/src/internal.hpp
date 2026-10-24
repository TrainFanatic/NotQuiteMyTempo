#pragma once
#include "drum_transcriber.h"
#include "kiss_fft.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <deque>
#include <vector>
constexpr int FFT_N = 1024, HOP = 240, BANDS = 32, LOOKAHEAD = 7680;
using Bands = std::array<float, BANDS>;
struct Model {
  uint32_t classes = 0;
  std::array<float, DT_FEATURES> mean{}, scale{};
  std::vector<int32_t> midi;
  std::vector<float> weights, bias, threshold;
};
bool read_model(const void *, size_t, Model &);
void classify(const Model &, dt_candidate &);
struct dt_handle {
  Model model;
  kiss_fft_cfg fft = nullptr;
  std::array<float, FFT_N> hann{};
  std::array<int, FFT_N / 2 + 1> band{};
  std::array<kiss_fft_cpx, FFT_N> fft_in{}, fft_out{};
  std::deque<float> audio;
  std::deque<int64_t> pending;
  std::deque<dt_candidate> ready;
  int64_t base = 0, next = 0, frame = 0, last_onset = -100000, segment = 0;
  int64_t previous_frame = 0;
  bool started = false, finished = false;
  double origin = 0;
  Bands previous{};
  float prev_flux = 0, prev_prev_flux = 0, mean_flux = 0, prev_gate = 0;
  dt_stats stats{};
  dt_handle();
  ~dt_handle();
  float sample(int64_t i) const;
  Bands spectrum(int64_t start);
  void clear_acoustics(int64_t start);
  void analyze_frame();
  void collect(bool eof = false);
};
