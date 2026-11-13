"""Barline evidence should reduce fragmentation, not force a constant 4/4."""

import numpy as np
import pytest

from drum_transcription.meter_decoder import decode_barlines
from drum_transcription.rhythm import analysis_from_beats


def fixture(lengths, extra=(), shift=0.371):
    end = sum(lengths)
    beats = [shift + 0.26 * i for i in range(end + 1)]
    true = [0]
    for length in lengths:
        true.append(true[-1] + length)
    raw = sorted(set(true) | set(extra))
    frames = np.full(round((beats[-1] + 1) * 50), -3.0, dtype=float)
    for index in true:
        frames[round(beats[index] * 50)] = 2.4
    for index in extra:
        frames[round(beats[index] * 50)] = 0.22
    return beats, true, raw, frames


@pytest.mark.parametrize(
    "lengths,extra",
    [
        ([4] * 8, [5, 13]),
        ([4, 4, 3, 3, 4, 5, 5, 4], [5, 25]),
        ([7] * 6, [2, 3, 8, 9, 17, 29]),
    ],
)
def test_global_decoder_keeps_true_changes_and_rejects_weak_spurious_peaks(lengths, extra):
    beats, true, raw, frames = fixture(lengths, extra)
    result = decode_barlines(beats, raw, frames)
    assert result.indices == tuple(true)
    assert result.skipped_raw_markers == len(extra)
    assert result.inserted_markers == 0
    assert result.mode == "decoded"


def test_no_logits_keeps_existing_behavior_and_fractional_offsets():
    beats, true, raw, frames = fixture([4, 4, 3, 3], [5])
    original = analysis_from_beats(
        beats, [beats[i] for i in raw], duration_seconds=beats[-1] + 0.1
    )
    assert len(original.downbeats_seconds) == len(raw)
    refined = analysis_from_beats(
        beats, [beats[i] for i in raw], duration_seconds=beats[-1] + 0.1,
        downbeat_logits=frames,
    )
    assert refined.downbeats_seconds == [beats[i] for i in true]
    assert refined.first_downbeat_seconds == pytest.approx(0.371)
    assert refined.metadata["barline_filtered_count"] == 1
    assert refined.metadata["barline_raw_marker_count"] == len(raw)
    assert refined.metadata["raw_downbeats_seconds"] == [beats[i] for i in raw]
    assert any("nghi thừa" in warning for warning in refined.warnings)


def test_flat_evidence_cannot_override_raw_downbeats():
    beats, true, raw, _frames = fixture([4, 3, 4], [5])
    result = decode_barlines(beats, raw, np.zeros(300))
    assert result.indices == tuple(raw)
    assert result.mode == "raw_flat_evidence"


def test_bad_logits_fail_instead_of_guessing():
    beats, true, raw, _ = fixture([4, 4, 4])
    with pytest.raises(ValueError, match="Invalid"):
        decode_barlines(beats, raw, [])
    data = np.full(400, -2.0)
    data[round(beats[4] * 50)] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        decode_barlines(beats, raw, data)
