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
    analysis = analysis_from_beats(beats, [0, 2, 6, 10], duration_seconds=12)
    assert analysis.meter == "4/4"
    assert analysis.metadata["missing_downbeat_markers"] == 2
    assert analysis.beat_positions_quarters[20] == 20
    assert analysis.downbeats_seconds == [0, 2, 6, 10]


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


def test_inconsistent_bar_groups_do_not_silently_shift_later_bars():
    beats = np.arange(0, 10, 0.5)
    with pytest.raises(ValueError, match="không nhất quán"):
        analysis_from_beats(beats, beats[[0, 4, 9, 13]], duration_seconds=10)


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
