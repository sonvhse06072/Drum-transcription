"""Explicit bar boundaries repair wrong model bar counts without changing raw hits."""

import numpy as np
import pytest

from drum_transcription.domain import RhythmRescoreOptions
from drum_transcription.rhythm import analysis_from_beats
from drum_transcription.rhythm_edit import apply_bar_overrides


def _analysis():
    beats = np.arange(0.37, 8.37, 0.5)
    return analysis_from_beats(
        beats, beats[[0, 4, 8, 12]], duration_seconds=8.4,
    )


def test_user_can_replace_four_wrong_bars_with_mixed_and_odd_meter():
    original = _analysis()
    before = original.model_dump_json()
    options = RhythmRescoreOptions(
        barline_seconds=[0.371, 2.371, 3.871, 5.121, 8.121],
        barline_meters=["4/4", "3/4", "5/8", "7/8"],
    )
    corrected = apply_bar_overrides(original, options)
    assert original.model_dump_json() == before
    assert [bar.meter for bar in corrected.bars] == options.barline_meters
    assert corrected.first_downbeat_seconds == pytest.approx(0.371)
    assert corrected.downbeats_seconds == options.barline_seconds[:-1]
    assert corrected.bars[-1].end_quarter == pytest.approx(13.125)
    assert corrected.metadata["bar_boundaries_edited"] is True
    assert corrected.metadata["model_downbeats_before_user_review"] == original.downbeats_seconds
    assert all(not bar.estimated for bar in corrected.bars)


@pytest.mark.parametrize("times,meters", [
    ([0.1, 2.1], ["4/4", "3/4"]),
    ([0.1, 0.1, 2.1], ["4/4", "3/4"]),
    ([-0.1, 2.1, 4.1], ["4/4", "3/4"]),
    ([0.1, 2.1, float("nan")], ["4/4", "3/4"]),
    ([0.1, 2.1, 4.1], ["99/4", "3/4"]),
])
def test_bad_user_barlines_are_rejected(times, meters):
    with pytest.raises(ValueError):
        RhythmRescoreOptions(barline_seconds=times, barline_meters=meters)


def test_out_of_audio_boundaries_are_rejected():
    with pytest.raises(ValueError, match="kết thúc"):
        apply_bar_overrides(
            _analysis(),
            RhythmRescoreOptions(
                barline_seconds=[0.37, 1.37, 2.37], barline_meters=["2/4", "2/4"]
            ),
        )


def test_cannot_mix_label_only_and_explicit_boundary_edit():
    from drum_transcription.domain import BarOverride

    with pytest.raises(ValueError):
        RhythmRescoreOptions(
            barline_seconds=[0, 2], barline_meters=["4/4"],
            bar_overrides=[BarOverride(bar_index=0, meter="3/4")],
        )
