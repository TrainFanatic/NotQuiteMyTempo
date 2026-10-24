import numpy as np
import pytest
from common import export_model, FEATURES


@pytest.fixture
def audio():
    a = np.zeros(48000, dtype=np.float32)
    rng = np.random.default_rng(18)
    for t in (0.1, 0.175, 0.25, 0.5, 0.575, 0.65):
        i = round(t * 48000)
        n = 2400
        a[i : i + n] += (
            rng.normal(size=n) * np.exp(-np.arange(n) / 300) * 0.12
        ).astype(np.float32)
    return a


@pytest.fixture
def all_classes_model():
    # Explicitly a fixture, never a distributable drum recognizer.
    return export_model(
        np.zeros(FEATURES),
        np.ones(FEATURES),
        np.zeros((6, FEATURES)),
        np.ones(6) * 5,
        np.ones(6) * 0.5,
    )
