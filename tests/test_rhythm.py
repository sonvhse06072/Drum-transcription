import contextlib
import io
import json
import socket
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from drum_transcription.domain import EngineUnavailable
from drum_transcription.rhythm import (
    analysis_from_beats,
    analyze_rhythm,
    prepare_rhythm_model,
    rhythm_status,
    write_rhythm_preview,
)


def test_fractional_first_downbeat_is_not_first_onset_or_zero():
    beats = np.arange(0, 8, 0.5)
    analysis = analysis_from_beats(beats, [1.5, 3.5, 5.5, 7.5], duration_seconds=8)
    assert analysis.bpm == pytest.approx(120)
    assert analysis.first_downbeat_seconds == 1.5
    assert analysis.beat_positions_quarters[:4] == [-3, -2, -1, 0]
    assert analysis.meter == "4/4"
    assert analysis.downbeats_seconds == [1.5, 3.5, 5.5, 7.5]


def test_arbitrary_subsecond_shift_preserves_same_musical_grid():
    beats = np.arange(0, 8, 0.5)
    first = analysis_from_beats(beats, [1.5, 3.5, 5.5, 7.5], duration_seconds=8)
    shifted = analysis_from_beats(
        beats + 0.371, np.array(first.downbeats_seconds) + 0.371, duration_seconds=8.5
    )
    assert shifted.first_downbeat_seconds == pytest.approx(1.871)
    assert shifted.bpm == pytest.approx(first.bpm)
    assert shifted.beat_positions_quarters == first.beat_positions_quarters
    assert shifted.beats_seconds == pytest.approx(np.array(first.beats_seconds) + 0.371)


def test_compound_meter_hint_converts_dotted_quarter_pulse_to_quarter_bpm():
    beats = np.arange(0, 8, 0.5)
    analysis = analysis_from_beats(
        beats, np.arange(0.5, 8, 1), duration_seconds=8, meter_hint="6/8"
    )
    assert analysis.bpm == pytest.approx(180)
    assert analysis.meter == "6/8"
    assert analysis.beat_unit_quarters == 1.5
    assert analysis.beat_positions_quarters[:4] == [-1.5, 0, 1.5, 3]
    assert analysis.metadata["meter_source"] == "user_hint"


def test_incompatible_meter_hint_does_not_invent_fractional_pulse_unit():
    beats = np.arange(0, 8, 0.5)
    with pytest.raises(ValueError, match="không khớp"):
        analysis_from_beats(beats, [0, 1.5, 3, 4.5, 6, 7.5], duration_seconds=8, meter_hint="4/4")


def test_padded_end_frame_is_trimmed_without_losing_valid_rhythm():
    analysis = analysis_from_beats(np.arange(0, 8.01, 0.5), [0, 2, 4, 6, 8], duration_seconds=8)
    assert analysis.bpm == 120
    assert analysis.beats_seconds[-1] == 7.5
    assert analysis.downbeats_seconds == [0, 2, 4, 6]
    assert analysis.metadata["discarded_tail_beats"] == 1
    assert analysis.metadata["discarded_tail_downbeats"] == 1
    assert any("frame đệm" in warning for warning in analysis.warnings)


def test_missing_downbeat_marker_preserves_full_beat_grid():
    beats = np.arange(0, 12, 0.5)
    analysis = analysis_from_beats(beats, [0, 2, 6, 8, 10], duration_seconds=12)
    assert analysis.meter == "4/4"
    assert analysis.metadata["missing_downbeat_markers"] == 1
    assert analysis.beat_positions_quarters[20] == 20
    assert analysis.downbeats_seconds == [0, 2, 6, 8, 10]
    assert [bar.source for bar in analysis.bars[:4]] == [
        "detected", "interpolated", "interpolated", "detected"
    ]
    assert all(bar.estimated for bar in analysis.bars)


def test_variable_beat_spacing_kept_in_grid_instead_of_integer_bpm_reconstruction():
    intervals = np.array([0.45, 0.47, 0.49, 0.51, 0.53, 0.55, 0.57, 0.59, 0.61, 0.63, 0.65, 0.67])
    beats = np.concatenate(([0.37], 0.37 + np.cumsum(intervals)))
    analysis = analysis_from_beats(beats, beats[[0, 4, 8, 12]], duration_seconds=8)
    assert analysis.beats_seconds == list(beats)
    assert analysis.beat_positions_quarters == list(range(13))
    assert analysis.bpm == pytest.approx(60 / np.median(intervals))
    assert any("thay đổi" in warning for warning in analysis.warnings)


@pytest.mark.parametrize(
    "beats,downbeats",
    [
        ([], []),
        ([0.25, 0.75, 1.25], [0.25]),
        ([0.25, 0.75, 1.25, 1.75], []),
        ([0.25, 0.75, 1.25, 1.75], [0.25, float("nan")]),
        ([0.25, 0.75, 0.75, 1.75], [0.25, 1.75]),
        ([0.25, 0.75, 1.25, 1.75], [0.35, 1.75]),
        ([-0.1, 0.75, 1.25, 1.75], [0.75, 1.75]),
        ([0.25, 0.75, 1.25, 20], [0.25, 20]),
    ],
)
def test_unusable_predictions_fail_without_zero_or_120_fallback(beats, downbeats):
    with pytest.raises(ValueError):
        analysis_from_beats(beats, downbeats, duration_seconds=8)


def test_single_odd_bar_preserves_local_boundaries_and_warns_instead_of_aborting():
    beats = np.arange(0, 10, 0.5)
    analysis = analysis_from_beats(beats, beats[[0, 4, 9, 13]], duration_seconds=10)
    assert [bar.meter for bar in analysis.bars[:3]] == ["4/4", "5/4", "4/4"]
    assert [bar.start_quarter for bar in analysis.bars[:4]] == [0, 4, 9, 13]
    assert analysis.metadata["meter_change_count"] == 2
    assert any("đổi số chỉ nhịp" in warning for warning in analysis.warnings)


def test_missing_weights_and_health_never_attempt_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Readiness or missing-weights inference attempted networking")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert rhythm_status(tmp_path)["ready"] is False
    with pytest.raises(EngineUnavailable, match="missing or empty"):
        analyze_rhythm(tmp_path / "audio.wav", tmp_path)


def _fake_torch():
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))
    torch.inference_mode = contextlib.nullcontext
    return torch


def test_adapter_uses_open_local_checkpoint_and_no_dbn(tmp_path, monkeypatch):
    checkpoint = tmp_path / "beat_this" / "final0.ckpt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"local checkpoint fixture")
    audio = tmp_path / "clip.wav"
    sf.write(audio, np.ones((8000 * 8, 2), dtype=np.float32) * 0.1, 8000)
    calls = []

    class Tracker:
        def __init__(self, checkpoint_path, device, dbn):
            assert hasattr(checkpoint_path, "read") and not checkpoint_path.closed
            assert checkpoint_path.read() == b"local checkpoint fixture"
            assert device == "cpu" and dbn is False
            calls.append("loaded")

        def __call__(self, waveform, sample_rate):
            assert waveform.shape == (64000, 2)
            assert sample_rate == 8000
            return np.arange(0, 8, 0.5), np.array([1.5, 3.5, 5.5, 7.5])

    module = types.ModuleType("beat_this.inference")
    module.Audio2Beats = Tracker
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "beat_this.inference", module)
    monkeypatch.setattr("drum_transcription.rhythm.rhythm_status", lambda _: {"ready": True})
    result = analyze_rhythm(audio, tmp_path, device="cpu")
    assert calls == ["loaded"]
    assert result.first_downbeat_seconds == 1.5
    assert result.metadata["device"] == "cpu"


def test_checkpoint_disappearing_before_open_cannot_activate_remote_fallback(tmp_path, monkeypatch):
    checkpoint = tmp_path / "beat_this" / "final0.ckpt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"local checkpoint fixture")
    audio = tmp_path / "clip.wav"
    sf.write(audio, np.full(8000, 0.1, dtype=np.float32), 8000)
    module = types.ModuleType("beat_this.inference")
    module.Audio2Beats = lambda **kwargs: pytest.fail("Should fail before calling upstream loader")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "beat_this.inference", module)

    def status(_):
        checkpoint.unlink()
        return {"ready": True}

    monkeypatch.setattr("drum_transcription.rhythm.rhythm_status", status)
    with pytest.raises(FileNotFoundError):
        analyze_rhythm(audio, tmp_path)


def test_click_preview_uses_fractional_samples_and_preserves_duration(tmp_path):
    audio = tmp_path / "silence.wav"
    sf.write(audio, np.zeros((8000 * 8, 2), dtype=np.float32), 8000)
    beats = np.arange(0.371, 8, 0.5)
    rhythm = analysis_from_beats(beats, beats[[0, 4, 8, 12]], duration_seconds=8)
    output = tmp_path / "clicks.wav"
    write_rhythm_preview(audio, rhythm, output)
    waveform, rate = sf.read(output)
    first_nonzero = np.flatnonzero(waveform[:, 0])[0] / rate
    assert first_nonzero == pytest.approx(0.371, abs=2 / rate)
    assert len(waveform) == 64000
    assert waveform.shape[1] == 2
    assert np.max(np.abs(waveform)) < 1


def test_setup_download_is_explicit_atomic_and_records_hash(tmp_path, monkeypatch):
    torch = _fake_torch()
    loads = []

    def load(path, *, map_location, weights_only):
        assert weights_only is True and map_location == "cpu"
        loads.append(Path(path))
        return {"hyper_parameters": {}, "state_dict": {"weight": 1}}

    torch.load = load
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(b"checkpoint test data")
    )
    manifest = prepare_rhythm_model(tmp_path)
    assert manifest["engine"] == "beat_this"
    assert len(manifest["weights_sha256"]) == 64
    assert loads[0].suffix == ".download"
    assert (tmp_path / "beat_this/final0.ckpt").read_bytes() == b"checkpoint test data"
    assert not (tmp_path / "beat_this/final0.download").exists()
    assert (
        json.loads((tmp_path / "beat_this/manifest.json").read_text())["weights_sha256"]
        == manifest["weights_sha256"]
    )



def _rhythm_fixture(groups, *, eighth_seconds=0.25, offset=0.371, meter_hint=None):
    """Each group list describes one known bar's pulse durations in eighths."""
    beats = [offset]
    indices = [0]
    for groups_in_bar in groups:
        for value in groups_in_bar:
            beats.append(beats[-1] + value * eighth_seconds)
        indices.append(len(beats) - 1)
    beats = np.asarray(beats)
    return analysis_from_beats(
        beats, beats[indices], duration_seconds=float(beats[-1] + 0.2), meter_hint=meter_hint
    )


def test_mixed_four_three_keeps_new_barlines_instead_of_a_global_meter():
    result = _rhythm_fixture([[2] * 4] * 2 + [[2] * 3] * 3)
    assert result.bpm == pytest.approx(120)
    assert [bar.meter for bar in result.bars[:5]] == ["4/4", "4/4", "3/4", "3/4", "3/4"]
    assert [bar.start_quarter for bar in result.bars[:6]] == [0, 4, 8, 11, 14, 17]
    assert result.bars[2].start_seconds == pytest.approx(4.371)
    assert result.bars[-1].source == "extrapolated"
    assert result.metadata["meter_change_count"] == 1


@pytest.mark.parametrize("grouping", [[2, 3], [2, 2, 3], [2, 3, 2], [3, 2, 2], [2, 3, 3, 3]])
def test_repeated_unequal_pulses_suggest_additive_odd_meter(grouping):
    result = _rhythm_fixture([grouping] * 4)
    expected_meter = f"{sum(grouping)}/8"
    assert result.meter == expected_meter
    assert all(bar.meter == expected_meter for bar in result.bars)
    assert all(bar.grouping == grouping for bar in result.bars)
    assert result.bpm == pytest.approx(120)
    assert result.beat_positions_quarters[: len(grouping) + 1] == pytest.approx(
        np.concatenate(([0], np.cumsum(grouping) / 2))
    )
    assert not any("Tempo thay đổi" in warning for warning in result.warnings)


def test_repeated_grouping_with_model_frame_jitter_does_not_create_tempo_changes():
    durations = np.tile([0.52, 0.48, 0.76], 4)
    beats = np.concatenate(([0.371], 0.371 + np.cumsum(durations)))
    result = analysis_from_beats(beats, beats[::3], duration_seconds=beats[-1] + 0.2)
    assert result.meter == "7/8"
    assert result.bpm == pytest.approx(60 / np.median([0.52, 0.48, 0.76 / 1.5]))
    assert result.bars[0].grouping == [2, 2, 3]


def test_mixed_four_to_grouped_seven_and_back_preserves_quarter_tempo():
    result = _rhythm_fixture([[2] * 4] * 2 + [[2, 2, 3]] * 2 + [[2] * 4] * 2)
    assert [bar.meter for bar in result.bars[:6]] == [
        "4/4", "4/4", "7/8", "7/8", "4/4", "4/4"
    ]
    assert [bar.start_quarter for bar in result.bars[:7]] == [0, 4, 8, 11.5, 15, 19, 23]
    assert result.bpm == pytest.approx(120)
    assert result.metadata["meter_change_count"] == 2


def test_hint_is_initial_meter_and_does_not_force_later_bars_to_same_length():
    result = _rhythm_fixture([[1] * 7] * 2 + [[1] * 5] * 2, meter_hint="7/8")
    assert [bar.meter for bar in result.bars[:4]] == ["7/8", "7/8", "5/8", "5/8"]
    assert result.bpm == pytest.approx(120)
    assert result.beat_positions_quarters[14] == 7
    assert result.metadata["meter_hint_scope"] == "initial_section"


def test_compound_meter_hint_preserves_pulse_unit_when_local_count_changes():
    result = _rhythm_fixture([[3, 3]] * 2 + [[3, 3, 3]] * 2, meter_hint="6/8")
    assert [bar.meter for bar in result.bars[:4]] == ["6/8", "6/8", "9/8", "9/8"]
    assert result.bpm == pytest.approx(120)


def test_one_unequal_bar_is_not_enough_to_assert_odd_meter_without_hint():
    result = _rhythm_fixture([[2, 2, 3]])
    assert result.meter == "3/4"
    hinted = _rhythm_fixture([[2, 2, 3]], meter_hint="7/8")
    assert hinted.meter == "7/8"
    assert hinted.bars[0].grouping == [2, 2, 3]
    assert hinted.bpm == pytest.approx(120)


def test_tempo_change_between_bars_keeps_meter_and_all_detected_beats():
    result = _rhythm_fixture([[2] * 4] * 2 + [[10] * 4] * 2)
    assert all(bar.meter == "4/4" for bar in result.bars)
    assert result.beat_positions_quarters == list(range(len(result.beats_seconds)))
    assert np.diff(result.beats_seconds)[:8] == pytest.approx([0.5] * 8)
    assert np.diff(result.beats_seconds)[8:] == pytest.approx([2.5] * 8)
    assert any("Tempo thay đổi" in warning for warning in result.warnings)


def test_sustained_two_to_four_is_not_silently_flattened_as_missing_markers():
    result = _rhythm_fixture([[2, 2]] * 3 + [[2] * 4] * 3)
    assert [bar.meter for bar in result.bars[:6]] == ["2/4"] * 3 + ["4/4"] * 3
    assert result.metadata["missing_downbeat_markers"] == 0
    assert result.metadata["meter_change_count"] == 1
    assert any("model đổi cách đếm" in warning for warning in result.warnings)


def test_single_one_beat_bar_remains_reviewable_without_aborting_whole_clip():
    result = _rhythm_fixture([[2] * 4, [2], [2] * 4])
    assert [bar.meter for bar in result.bars[:3]] == ["4/4", "1/4", "4/4"]
    assert all(bar.estimated for bar in result.bars)


def test_grouped_pickup_keeps_negative_musical_positions_and_fractional_anchor():
    beats = np.array([0.121, 0.871, 1.371, 1.871, 2.621, 3.121, 3.621, 4.371, 4.871])
    result = analysis_from_beats(beats, beats[[1, 4, 7]], duration_seconds=5.1)
    assert result.first_downbeat_seconds == 0.871
    assert result.meter == "7/8"
    assert result.beat_positions_quarters[:5] == [-1.5, 0, 1, 2, 3.5]
    assert result.bars[0].start_seconds == 0.871
    assert result.bars[-1].end_seconds > 5.1



def test_large_additive_meter_hint_preserves_valid_denominator_on_following_bars():
    result = _rhythm_fixture([[2, 3]] * 2 + [[2, 2, 3]] * 2, meter_hint="15/8")
    assert [bar.meter for bar in result.bars[:4]] == ["15/8", "15/8", "21/8", "21/8"]
    assert result.bars[0].grouping == [6, 9]
    assert result.bars[2].grouping == [6, 6, 9]
    assert result.bpm == pytest.approx(360)


def test_initial_uniform_hint_preserves_notation_denominator_across_count_changes():
    result = _rhythm_fixture([[2, 2]] * 2 + [[2, 2, 2]] * 2, meter_hint="4/8")
    assert [bar.meter for bar in result.bars[:4]] == ["4/8", "4/8", "6/8", "6/8"]
    assert result.bpm == pytest.approx(120)



def test_alternating_five_and_seven_can_use_repeated_local_grouping_evidence():
    result = _rhythm_fixture([[2, 3], [2, 2, 3]] * 3)
    assert [bar.meter for bar in result.bars[:6]] == ["5/8", "7/8"] * 3
    assert [bar.start_quarter for bar in result.bars[:6]] == [0, 2.5, 6, 8.5, 12, 14.5]
    assert result.bpm == pytest.approx(120)
    assert "frequent_meter_or_group_changes" in result.metadata["bar_map_review_reasons"]


def test_many_one_pulse_intervals_are_flagged_without_inventing_confidence():
    result = _rhythm_fixture([[2], [2], [2], [2, 2], [2]])
    assert "many_single_beat_intervals" in result.metadata["bar_map_review_reasons"]
    assert result.metadata["rhythm_review_status"] == "unstable"
    assert "4 khoảng" in result.metadata["rhythm_review_reasons"][0]
    assert result.metadata["rhythm_review_criteria"]["single_beat_bar_count_threshold"] == 3
    assert result.metadata["single_beat_bar_intervals"] == 4
    assert result.metadata["confidence_kind"] == "not_calibrated_or_reported"
    assert any("không coi bản đồ" in warning for warning in result.warnings)
