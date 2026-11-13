import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from drum_transcription.domain import EngineUnavailable
from drum_transcription.engines import get_engine_status, transcribe
from drum_transcription.engines.adt_str import normalize_custom_notes
from drum_transcription.engines.adtof import transcribe_adtof
from drum_transcription.engines.common import select_device
from drum_transcription.engines.prepare import _prepare_adtof


def test_missing_models_never_connect_or_return_random_output(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("Model inference and readiness must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    statuses = get_engine_status(tmp_path)
    assert {item["id"] for item in statuses} == {"adtof", "adt_str"}
    assert not any(item["ready"] for item in statuses)
    for engine in ("adtof", "adt_str"):
        with pytest.raises(EngineUnavailable, match="Missing"):
            transcribe(tmp_path / "not-read.wav", engine, models_dir=tmp_path)


def test_adtof_missing_weights_fails_before_model_import(tmp_path, monkeypatch):
    # Even direct adapter calls cannot reach upstream's unsafe random-init path.
    monkeypatch.setitem(sys.modules, "adtof_pytorch", None)
    with pytest.raises(EngineUnavailable, match="missing or empty"):
        transcribe_adtof(tmp_path / "unused.wav", device="cpu", threshold=0.25, models_dir=tmp_path)


def test_adt_custom_ids_are_not_interpreted_as_general_midi():
    rows = [
        [0.123456789, 0.223456789, 44, 70],
        [0.5, 0.6, 46, 95],
        [0.75, 0.85, 47, 81],
        [1.0, 1.1, 43, 67],
        [1.25, 1.35, 48, 88],
    ]
    hits, warnings = normalize_custom_notes(rows, duration=2)
    assert [h.instrument for h in hits] == [
        "hihat_open",
        "crash",
        "tom_high",
        "hihat_pedal",
        "ride",
    ]
    assert hits[0].time_seconds == 0.123456789
    assert hits[0].velocity == 70
    assert hits[0].confidence is None
    assert warnings == []


def test_adt_malformed_padding_and_unknown_classes_are_explicit():
    hits, warnings = normalize_custom_notes(
        [
            [-0.1, 0.0, 35, 90],
            [1.5, 1.6, 35, 90],  # padded tail beyond the actual one-second clip
            [float("nan"), 0.2, 38, 80],
            [0.5, 0.6, 99, 85],
            [0.2, 0.3, 38, 0],
        ],
        duration=1,
    )
    assert len(hits) == 2
    assert hits[0].velocity is None
    assert hits[1].instrument == "unknown_adt_99"
    assert any("Discarded 3" in item for item in warnings)
    assert any("99" in item for item in warnings)


def test_velocity_absent_is_not_invented():
    hits, _ = normalize_custom_notes([[0.2, 0.3, 38, 100]], duration=1, velocity_available=False)
    assert hits[0].velocity is None


def test_requested_accelerator_is_not_silently_replaced():
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False), backends=SimpleNamespace()
    )
    assert select_device(torch, "auto") == "cpu"
    with pytest.raises(EngineUnavailable, match="CUDA"):
        select_device(torch, "cuda")
    with pytest.raises(EngineUnavailable, match="MPS"):
        select_device(torch, "mps")


def test_prepare_adtof_copies_local_bundled_weights_with_manifest(tmp_path, monkeypatch):
    source = tmp_path / "installed" / "adtof_pytorch"
    (source / "data").mkdir(parents=True)
    data = b"fixture-weights-not-an-actual-model"
    (source / "data" / "adtof_frame_rnn_pytorch_weights.pth").write_bytes(data)
    monkeypatch.setattr(
        "drum_transcription.engines.prepare.find_spec",
        lambda name: SimpleNamespace(origin=str(source / "__init__.py")),
    )
    target = tmp_path / "models"
    manifest = _prepare_adtof(target)
    assert (target / "adtof" / "weights.pth").read_bytes() == data
    assert len(manifest["weights_sha256"]) == 64
    assert (target / "adtof" / "manifest.json").is_file()


def test_empty_weight_file_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.engines._has_module", lambda name: True)
    weight = tmp_path / "adtof" / "weights.pth"
    weight.parent.mkdir()
    weight.touch()
    status = get_engine_status(tmp_path)[0]
    assert status["ready"] is False
    assert "weights.pth" in status["detail"]


def test_adtof_weight_loading_is_strict(tmp_path, monkeypatch):
    weight = tmp_path / "adtof" / "weights.pth"
    weight.parent.mkdir()
    weight.write_bytes(b"fixture")
    calls = []

    def fail_strict(model, path, strict):
        calls.append(strict)
        raise RuntimeError("deliberately incomplete checkpoint")

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        SimpleNamespace(info=lambda path: SimpleNamespace(frames=24000, samplerate=24000)),
    )
    monkeypatch.setitem(
        sys.modules,
        "adtof_pytorch",
        SimpleNamespace(
            LABELS_5=[35, 38, 47, 42, 49],
            PeakPicker=object,
            calculate_n_bins=lambda: 168,
            create_frame_rnn_model=lambda n: object(),
            load_audio_for_model=lambda path: None,
            load_pytorch_weights=fail_strict,
        ),
    )
    with pytest.raises(EngineUnavailable, match="do not match"):
        transcribe_adtof(Path("unused.wav"), device="cpu", threshold=0.25, models_dir=tmp_path)
    assert calls == [True]
