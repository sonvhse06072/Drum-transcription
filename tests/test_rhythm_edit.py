import pytest

from drum_transcription.domain import (
    BarOverride,
    RhythmAnalysis,
    RhythmBar,
    RhythmRescoreOptions,
    ScoreOptions,
)
from drum_transcription.rhythm_edit import apply_bar_overrides


def mixed_analysis():
    return RhythmAnalysis(
        bpm=120,
        meter="4/4",
        first_downbeat_seconds=0.37,
        beats_seconds=[0.12, 0.37, 0.87, 1.37, 1.87, 2.37, 2.87, 3.37],
        downbeats_seconds=[0.37, 2.37],
        beat_positions_quarters=[-0.5, 0, 1, 2, 3, 4, 5, 6],
        bars=[
            RhythmBar(
                start_seconds=0.37,
                end_seconds=2.37,
                start_quarter=0,
                end_quarter=4,
                meter="4/4",
            ),
            RhythmBar(
                start_seconds=2.37,
                end_seconds=3.87,
                start_quarter=4,
                end_quarter=7,
                meter="3/4",
                source="extrapolated",
            ),
        ],
    )


def test_review_rescales_only_musical_positions_keeps_fractional_times_and_source():
    original = mixed_analysis()
    saved = original.model_dump()
    reviewed = apply_bar_overrides(
        original,
        RhythmRescoreOptions(
            bar_overrides=[
                BarOverride(bar_index=0, meter="7/8", grouping=[2, 2, 3]),
            ]
        ),
    )
    assert original.model_dump() == saved
    assert reviewed.beats_seconds == original.beats_seconds
    assert reviewed.downbeats_seconds == original.downbeats_seconds
    assert reviewed.first_downbeat_seconds == 0.37
    assert [(bar.start_seconds, bar.end_seconds) for bar in reviewed.bars] == [
        (bar.start_seconds, bar.end_seconds) for bar in original.bars
    ]
    assert reviewed.meter == "7/8"
    assert reviewed.beat_positions_quarters == pytest.approx(
        [-0.4375, 0, 0.875, 1.75, 2.625, 3.5, 4.5, 5.5]
    )
    assert reviewed.bars[1].start_quarter == 3.5
    assert reviewed.bars[1].end_quarter == 6.5
    assert reviewed.bars[0].grouping == [2, 2, 3]
    assert reviewed.bars[0].source == "user"
    assert reviewed.bars[0].estimated is False
    assert reviewed.metadata["bar_boundaries_edited"] is False


def test_grouping_review_only_changes_beaming_not_beat_timing():
    original = mixed_analysis()
    reviewed = apply_bar_overrides(
        original,
        RhythmRescoreOptions(
            bar_overrides=[
                BarOverride(bar_index=0, meter="4/4", grouping=[2, 2]),
            ]
        ),
    )
    assert reviewed.beat_positions_quarters == original.beat_positions_quarters
    assert reviewed.bpm == original.bpm
    assert reviewed.bars[0].grouping == [2, 2]


def test_repeated_review_keeps_previous_edits_and_history():
    first = apply_bar_overrides(
        mixed_analysis(),
        RhythmRescoreOptions(bar_overrides=[BarOverride(bar_index=0, meter="5/4")]),
    )
    second = apply_bar_overrides(
        first,
        RhythmRescoreOptions(
            bar_overrides=[
                BarOverride(bar_index=1, meter="5/8", grouping=[2, 3]),
            ]
        ),
    )
    assert [bar.meter for bar in second.bars] == ["5/4", "5/8"]
    assert second.bars[1].end_quarter == 7.5
    assert len(second.metadata["meter_edit_history"]) == 2


def test_review_without_edits_keeps_grid_exactly():
    original = mixed_analysis()
    reviewed = apply_bar_overrides(original, RhythmRescoreOptions())
    assert reviewed.model_dump() == original.model_dump()
    assert reviewed is not original


def test_review_recomputes_active_metadata_and_keeps_initial_warning_provenance():
    original = mixed_analysis()
    original.metadata.update(meter_change_count=1, meter_changes=[{"meter": "3/4"}])
    original.warnings = ["Initial detector warning"]
    options = RhythmRescoreOptions(bar_overrides=[BarOverride(bar_index=1, meter="4/4")])
    reviewed = apply_bar_overrides(original, options)
    assert reviewed.metadata["meter_change_count"] == 0
    assert reviewed.metadata["meter_changes"] == []
    assert reviewed.metadata["quarter_interval_p10_seconds"] < 0.5
    assert reviewed.metadata["detector_warnings"] == original.warnings
    again = apply_bar_overrides(reviewed, options)
    assert again.warnings == reviewed.warnings


def test_invalid_review_is_rejected_before_job_creation():
    with pytest.raises(ValueError, match="tổng"):
        BarOverride(bar_index=0, meter="7/8", grouping=[2, 2])
    with pytest.raises(ValueError, match="một lần"):
        RhythmRescoreOptions(
            bar_overrides=[
                BarOverride(bar_index=1, meter="7/8"),
                BarOverride(bar_index=1, meter="5/8"),
            ]
        )
    with pytest.raises(ValueError, match="ngoài"):
        apply_bar_overrides(
            mixed_analysis(),
            RhythmRescoreOptions(
                bar_overrides=[
                    BarOverride(bar_index=2, meter="7/8"),
                ]
            ),
        )
    legacy = mixed_analysis().model_copy(update={"bars": []})
    with pytest.raises(ValueError, match="phiên bản mới"):
        apply_bar_overrides(legacy, RhythmRescoreOptions())


def test_bar_contract_rejects_bad_lengths_or_discontinuous_map():
    with pytest.raises(ValueError, match="length"):
        RhythmBar(start_seconds=0, end_seconds=2, start_quarter=0, end_quarter=4, meter="7/8")
    data = mixed_analysis().model_dump()
    data["bars"][1]["start_seconds"] += 0.1
    with pytest.raises(ValueError, match="continuous"):
        RhythmAnalysis.model_validate(data)


def test_grid_and_bar_boundaries_must_agree():
    data = mixed_analysis().model_dump()
    data["beat_positions_quarters"] = [value + 1 for value in data["beat_positions_quarters"]]
    with pytest.raises(ValueError, match="quarter zero"):
        RhythmAnalysis.model_validate(data)
    data = mixed_analysis().model_dump()
    data["bars"][0]["end_seconds"] += 0.1
    data["bars"][1]["start_seconds"] += 0.1
    with pytest.raises(ValueError, match="boundaries"):
        RhythmAnalysis.model_validate(data)


@pytest.mark.parametrize("signature", ["5/4", "5/8", "7/8", "11/8", "13/16", "1/4", "32/16"])
def test_extended_meters_are_accepted(signature):
    assert ScoreOptions(meter=signature).meter == signature


@pytest.mark.parametrize("signature", ["0/4", "33/8", "7/3", "5/0", "7/8+1/4"])
def test_unsupported_meter_strings_are_rejected(signature):
    with pytest.raises(ValueError):
        ScoreOptions(meter=signature)
