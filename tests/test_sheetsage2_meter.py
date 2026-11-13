"""SheetSage2 beat/bar-head adapter: offline, precise timing and meter provenance."""

import json

import pytest
from pydantic import ValidationError

from drum_transcription.domain import EngineUnavailable, JobOptions
from drum_transcription.rhythm_sheetsage2 import (
    MODEL_REPO,
    SOURCE_REVISION,
    _relative_model_path,
    analyze_meter_rhythm,
    meter_status,
    prediction_to_rhythm,
)


def mixed_payload(offset=0.371):
    # 4/4: eight eighth pulses; 7/8: seven; 5/16: five sixteenths.
    first = [offset + 0.25 * i for i in range(8)]
    middle = [offset + 2 + 0.25 * i for i in range(7)]
    last = [offset + 3.75 + 0.125 * i for i in range(5)]
    starts = [offset, offset + 2, offset + 3.75]
    return {
        "beats": first + middle + last,
        "downbeats": starts,
        "bars": [
            {"start": starts[0], "beats": 8, "unit": 4, "meter": "4/4"},
            {"start": starts[1], "beats": 7, "unit": 8, "meter": "7/8"},
            {"start": starts[2], "beats": 5, "unit": 16, "meter": "5/16"},
        ],
    }


def test_fractional_mixed_meter_is_mapped_to_correct_quarter_lengths():
    payload = mixed_payload()
    result = prediction_to_rhythm(
        payload, 4.71, manifest={"weights_revision": "a" * 40}, meter_hint="6/8"
    )
    assert result.engine == "sheetsage2_refined"
    assert [bar.meter for bar in result.bars] == ["4/4", "7/8", "5/16"]
    assert [bar.start_quarter for bar in result.bars] == pytest.approx([0, 4, 7.5])
    assert [bar.end_quarter for bar in result.bars] == pytest.approx([4, 7.5, 8.75])
    assert result.bpm == pytest.approx(120)
    assert result.first_downbeat_seconds == pytest.approx(0.371)
    assert result.metadata["meter_source"] == "model_bar_head_estimate"
    assert result.metadata["raw_bars"] == payload["bars"]
    assert result.metadata["raw_beats_seconds"] == payload["beats"]
    assert result.metadata["bar_map_requires_review"] is True
    assert any("khác kết quả" in warning for warning in result.warnings)


def test_subsecond_shift_preserves_score_positions_and_no_user_bpm():
    before = prediction_to_rhythm(mixed_payload(0.371), 4.71)
    after = prediction_to_rhythm(mixed_payload(1.537), 5.88)
    assert after.first_downbeat_seconds == pytest.approx(1.537)
    assert after.beat_positions_quarters == pytest.approx(before.beat_positions_quarters)
    assert [bar.meter for bar in after.bars] == [bar.meter for bar in before.bars]
    defaults = JobOptions(rhythm_engine="sheetsage2_refined")
    assert defaults.rhythm_mode == "auto"
    assert defaults.bpm is None and defaults.first_downbeat_seconds is None
    with pytest.raises(ValidationError):
        JobOptions(rhythm_engine="unknown_engine")


@pytest.mark.parametrize("mutator", [
    lambda p: p["bars"][1].update(meter="7/3"),
    lambda p: p["bars"][1].update(beats=0),
    lambda p: p["bars"][1].update(start=-1),
    lambda p: p["downbeats"].pop(),
    lambda p: p["beats"].insert(2, float("nan")),
])
def test_corrupt_predictions_are_rejected(mutator):
    prediction = mixed_payload()
    mutator(prediction)
    with pytest.raises((ValueError, TypeError)):
        prediction_to_rhythm(prediction, 4.71)


@pytest.mark.parametrize("path", ["../escape.safetensors", "/tmp/model.safetensors", "./bad.safetensors", "weights.pt"])
def test_only_safe_safetensors_model_paths(path):
    with pytest.raises(ValueError):
        _relative_model_path(path)


def test_missing_model_does_not_download_or_assume_ready(tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("Readiness attempted networking")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert meter_status(tmp_path)["ready"] is False
    with pytest.raises(EngineUnavailable):
        analyze_meter_rhythm(tmp_path / "audio.wav", tmp_path)


def test_local_model_artifacts_are_required_not_replaced_by_random_weights(tmp_path):

    target = tmp_path / "sheetsage2_refined"
    target.mkdir()
    (target / "config.json").write_text(json.dumps({
        "base_weights": "weights/encoder.safetensors",
        "beat_tracker": {"head": "heads/beat.safetensors"},
    }))
    (target / "weights").mkdir()
    (target / "weights" / "encoder.safetensors").write_bytes(b"model")
    (target / "manifest.json").write_text(json.dumps({
        "engine": "sheetsage2_refined",
        "source_revision": SOURCE_REVISION,
        "model_repo": MODEL_REPO,
        "weights_revision": "a" * 40,
        "artifacts": ["weights/encoder.safetensors", "heads/beat.safetensors"],
    }))
    assert meter_status(tmp_path)["ready"] is False
    assert not (target / "heads" / "beat.safetensors").exists()
