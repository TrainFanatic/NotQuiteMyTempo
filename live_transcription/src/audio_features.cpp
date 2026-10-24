#include "internal.hpp"
#include <cstdlib>
dt_handle::dt_handle() {
  fft = kiss_fft_alloc(FFT_N, 0, nullptr, nullptr);
  if (!fft)
    throw std::bad_alloc();
  for (int i = 0; i < FFT_N; ++i)
    hann[i] = 0.5f - 0.5f * std::cos(2 * 3.141592653589793 * i / (FFT_N - 1));
  for (int k = 0; k <= FFT_N / 2; ++k) {
    double hz = double(k) * DT_SAMPLE_RATE / FFT_N;
    band[k] = hz < 30
                  ? -1
                  : std::min(BANDS - 1, int(std::log(hz / 30) /
                                            std::log(24000.0 / 30) * BANDS));
  }
}
dt_handle::~dt_handle() { free(fft); }
float dt_handle::sample(int64_t i) const {
  return i < base || i >= next ? 0.f : audio[size_t(i - base)];
}
Bands dt_handle::spectrum(int64_t start) {
  for (int i = 0; i < FFT_N; ++i)
    fft_in[i] = {sample(start + i) * hann[i], 0};
  kiss_fft(fft, fft_in.data(), fft_out.data());
  Bands bands{};
  for (int k = 1; k <= FFT_N / 2; ++k)
    if (band[k] >= 0) {
      auto z = fft_out[k];
      bands[band[k]] += (z.r * z.r + z.i * z.i) / (FFT_N * FFT_N);
    }
  for (auto &v : bands)
    v = std::log1p(1000.f * v);
  return bands;
}
