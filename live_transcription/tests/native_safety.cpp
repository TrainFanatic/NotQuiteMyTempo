#include "drum_transcriber.h"
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <vector>

static void require(bool condition) {
  if (!condition)
    std::abort();
}
int main() {
  // Exercise byte-wise model parsing with short/truncated/hostile headers.
  for (size_t n = 0; n < 12000; n += 7) {
    std::vector<unsigned char> bytes(n, 255);
    if (n >= 8)
      std::memcpy(bytes.data(), "DTRN0001", 8);
    auto bad = dt_create(bytes.data(), bytes.size());
    require(bad == nullptr || n == 0);
    dt_destroy(bad);
  }
  auto h = dt_create(nullptr, 0);
  require(h != nullptr);
  require(dt_reset(h, 0) == DT_OK);
  std::vector<float> block(DT_MAX_BLOCK);
  int64_t position = 0;
  unsigned count = 0;
  for (int pass = 0; pass < 120; ++pass) {
    for (size_t i = 0; i < block.size(); ++i) {
      int phase = int((position + i) % 3600);
      block[i] =
          phase < 1200
              ? float(.2 * std::sin(phase * .3) * std::exp(-phase / 200.0))
              : 0.f;
    }
    require(dt_process(h, block.data(), block.size(), position) == DT_OK);
    position += block.size();
    dt_candidate c{};
    int status;
    while ((status = dt_poll_candidate(h, &c)) == 1) {
      require(c.sample >= 0 && c.sample < position);
      for (auto value : c.features)
        require(std::isfinite(value));
      ++count;
    }
    require(status == 0);
  }
  require(count > 100);
  require(dt_finish(h) == DT_OK);
  require(dt_finish(h) == DT_OK);
  require(dt_process(h, block.data(), block.size(), position) == DT_FINISHED);
  require(dt_reset(h, -2) == DT_OK);
  block[0] = std::numeric_limits<float>::quiet_NaN();
  require(dt_process(h, block.data(), block.size(), 0) == DT_INVALID);
  dt_destroy(h);
}
