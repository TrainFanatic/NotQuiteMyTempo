#include "internal.hpp"
#include <cstring>
#include <set>
bool read_model(const void *data, size_t size, Model &m) {
  if (!data || size < 28)
    return false;
  auto p = static_cast<const uint8_t *>(data);
  if (std::memcmp(p, "DTRN0001", 8))
    return false;
  size_t pos = 8;
  auto u32 = [&]() {
    uint32_t v = uint32_t(p[pos]) | (uint32_t(p[pos + 1]) << 8) |
                 (uint32_t(p[pos + 2]) << 16) | (uint32_t(p[pos + 3]) << 24);
    pos += 4;
    return v;
  };
  auto f32 = [&]() {
    uint32_t u = u32();
    float f;
    std::memcpy(&f, &u, 4);
    return f;
  };
  if (u32() != 1 || u32() != DT_SAMPLE_RATE || u32() != DT_FEATURES)
    return false;
  m.classes = u32();
  if (!m.classes || m.classes > DT_MAX_CLASSES || u32() != LOOKAHEAD)
    return false;
  size_t expected = 28 + 4 * (2 * DT_FEATURES + m.classes * (DT_FEATURES + 3));
  if (size != expected)
    return false;
  for (auto &v : m.mean) {
    v = f32();
    if (!std::isfinite(v))
      return false;
  }
  for (auto &v : m.scale) {
    v = f32();
    if (!std::isfinite(v) || v <= 0)
      return false;
  }
  std::set<int> seen;
  for (uint32_t k = 0; k < m.classes; ++k) {
    int midi = int(u32());
    if (midi < 0 || midi > 127 || !seen.insert(midi).second)
      return false;
    m.midi.push_back(midi);
    float bias = f32(), threshold = f32();
    if (!std::isfinite(bias) || !std::isfinite(threshold) || threshold <= 0 ||
        threshold >= 1)
      return false;
    m.bias.push_back(bias);
    m.threshold.push_back(threshold);
    for (int j = 0; j < DT_FEATURES; ++j) {
      float v = f32();
      if (!std::isfinite(v))
        return false;
      m.weights.push_back(v);
    }
  }
  return true;
}
