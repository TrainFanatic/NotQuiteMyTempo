#include "internal.hpp"
void classify(const Model &m, dt_candidate &c) {
  c.class_count = m.classes;
  for (uint32_t k = 0; k < m.classes; ++k) {
    double z = m.bias[k];
    for (int j = 0; j < DT_FEATURES; ++j)
      z += double(m.weights[k * DT_FEATURES + j]) *
           ((double(c.features[j]) - m.mean[j]) / double(m.scale[j]));
    double p =
        z >= 0 ? 1 / (1 + std::exp(-z)) : std::exp(z) / (1 + std::exp(z));
    c.probability[k] = float(p);
    c.midi[k] = m.midi[k];
    if (p >= m.threshold[k])
      c.family_mask |= 1u << k;
  }
}
