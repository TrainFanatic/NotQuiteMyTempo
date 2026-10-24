import numpy as np
import pytest
from common import Native, extract, events, FEATURES, export_model


@pytest.mark.parametrize("chunk", [1, 127, 480, 1024, 4800])
def test_chunk_independence_and_fast_hits(audio, chunk):
    reference = extract(audio, chunk=480)
    actual = extract(audio, chunk=chunk)
    assert len(actual) == 6
    assert [c.sample for c in actual] == [c.sample for c in reference]
    assert (
        np.max(
            np.abs(
                np.array([c.time_s for c in actual])
                - [0.1, 0.175, 0.25, 0.5, 0.575, 0.65]
            )
        )
        < 0.001
    )
    np.testing.assert_array_equal(
        [list(c.features) for c in actual], [list(c.features) for c in reference]
    )
    assert all(c.available_sample - c.sample == 7680 for c in actual)


def test_simultaneous_rejected_and_origin(audio, all_classes_model):
    classified = extract(audio, all_classes_model, origin=-0.2)
    notes = events(classified)
    assert all(c.family_mask == 63 for c in classified)
    assert len(notes) == 24 and min(t for t, _ in notes) >= 0
    assert notes == sorted(notes)
    assert not events(extract(audio))


def test_gap_reset_validation_and_finish(audio, all_classes_model):
    with Native(all_classes_model) as core:
        for i in range(0, 9600, 480):
            core.process(audio[i : i + 480], i)
        result, status = core.process(np.zeros(480), 24000)
        assert status == 1 and not result
        assert core.stats()["gaps"] == 1 and core.stats()["discarded_pending"] > 0
        with pytest.raises(ValueError):
            core.process(np.zeros(1), 0)
        with pytest.raises(ValueError):
            core.process(np.array([np.nan]), 24480)
        with pytest.raises(ValueError):
            core.process(np.ones(4801), 24480)
        core.finish()
        assert not core.finish()
        with pytest.raises(ValueError):
            core.process(np.zeros(1), 24480)
        core.reset()
        assert core.stats()["gaps"] == 0
        assert not core.process(np.zeros(480), 0)[0]


def test_silence_and_ringing():
    assert extract(np.zeros(48000, dtype=np.float32)) == []
    t = np.arange(24000) / 48000
    ring = (0.2 * np.sin(2 * np.pi * 4000 * t) * np.exp(-t * 3)).astype(np.float32)
    a = np.concatenate(
        [np.zeros(4800, dtype=np.float32), ring, np.zeros(4800, dtype=np.float32)]
    )
    assert len(extract(a)) == 1


def test_final_hit_and_reset(audio):
    short = audio[: round(0.69 * 48000)]
    assert len(extract(short)) == 6
    with Native() as core:
        first = []
        for i in range(0, len(short), 480):
            first.extend(core.process(short[i : i + 480], i)[0])
        first.extend(core.finish())
        core.reset()
        second = []
        for i in range(0, len(short), 480):
            second.extend(core.process(short[i : i + 480], i)[0])
        second.extend(core.finish())
        assert [c.sample for c in first] == [c.sample for c in second]


@pytest.mark.parametrize("blob", [b"", b"garbage", b"DTRN0001" + b"\0" * 20])
def test_bad_models(blob):
    with pytest.raises(ValueError):
        Native(blob)


def test_truncated_nan_duplicate_models(all_classes_model):
    with pytest.raises(ValueError):
        Native(all_classes_model[:-1])
    with pytest.raises(ValueError):
        Native(all_classes_model + b"\0")
    mean = np.zeros(FEATURES)
    mean[0] = np.nan
    with pytest.raises(ValueError):
        Native(
            export_model(
                mean,
                np.ones(FEATURES),
                np.zeros((6, FEATURES)),
                np.zeros(6),
                np.ones(6) * 0.5,
            )
        )
    with pytest.raises(ValueError):
        Native(
            export_model(
                np.zeros(FEATURES),
                np.ones(FEATURES),
                np.zeros((2, FEATURES)),
                np.zeros(2),
                np.ones(2) * 0.5,
                midis=[36, 36],
            )
        )


def test_float_inference_parity(audio):
    rng = np.random.default_rng(8)
    mean = rng.normal(size=FEATURES).astype(np.float32)
    scale = rng.uniform(0.5, 2, FEATURES).astype(np.float32)
    weights = rng.normal(0, 0.1, (6, FEATURES)).astype(np.float32)
    bias = rng.normal(size=6).astype(np.float32)
    model = export_model(mean, scale, weights, bias, np.ones(6) * 0.5)
    for c in extract(audio, model):
        x = np.array(c.features)
        expected = 1 / (1 + np.exp(-(weights @ ((x - mean) / scale) + bias)))
        np.testing.assert_allclose(
            list(c.probability)[:6], expected, rtol=1e-5, atol=1e-6
        )


def test_extreme_finite_model_values_do_not_overflow(audio):
    blob = export_model(
        np.full(FEATURES, 1e38),
        np.full(FEATURES, 1e-38),
        np.zeros((6, FEATURES)),
        np.zeros(6),
        np.full(6, 0.4),
    )
    for c in extract(audio, blob):
        assert list(c.probability)[:6] == [0.5] * 6
        assert c.family_mask == 63
