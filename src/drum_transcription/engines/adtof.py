"""Adapter around the pinned ADTOF PyTorch package, with strict weight loading."""

import math
from pathlib import Path

from drum_transcription.domain import DrumHit, EngineUnavailable, Transcription

from . import ADTOF_REVISION
from .common import require_file, select_device

LABEL_TO_INSTRUMENT = {35: "kick", 38: "snare", 47: "tom_mid", 42: "hihat_closed", 49: "crash"}


def transcribe_adtof(
    audio_path: Path, *, device: str, threshold: float, models_dir: Path
) -> Transcription:
    # Check before importing or constructing any model. Upstream's public helper
    # silently leaves randomly initialized parameters when its weights are absent.
    weights = require_file(models_dir / "adtof" / "weights.pth")
    if not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("ADTOF threshold must be finite and between zero and one.")
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from adtof_pytorch import (
            LABELS_5,
            PeakPicker,
            calculate_n_bins,
            create_frame_rnn_model,
            load_audio_for_model,
            load_pytorch_weights,
        )
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(
            "Install the 'adtof' extra in the app's Python environment."
        ) from exc

    selected = select_device(torch, device, allow_mps=False)
    info = sf.info(str(audio_path))
    duration = info.frames / info.samplerate
    if duration <= 0 or not math.isfinite(duration):
        raise ValueError("Audio must contain at least one sample.")
    model = create_frame_rnn_model(calculate_n_bins())
    try:
        model = load_pytorch_weights(model, str(weights), strict=True)
    except (RuntimeError, ValueError, KeyError) as exc:
        raise EngineUnavailable(
            "ADTOF weights do not match the pinned model; rerun setup-models adtof."
        ) from exc
    model = model.to(selected).eval()
    try:
        with torch.inference_mode():
            audio = load_audio_for_model(str(audio_path)).to(selected)
            activations = model(audio).detach().cpu().numpy()
        if activations.ndim != 3 or activations.shape[0] != 1 or activations.shape[2] != 5:
            raise RuntimeError(f"Unexpected ADTOF prediction shape: {activations.shape}")
        if not np.isfinite(activations).all():
            raise RuntimeError("ADTOF produced non-finite predictions; no score was generated.")
        peaks = PeakPicker(thresholds=float(threshold), fps=100).pick(activations, labels=LABELS_5)[
            0
        ]
        hits = []
        discarded = 0
        for class_index, label in enumerate(LABELS_5):
            for onset in peaks.get(label, []):
                onset = float(onset)
                if not math.isfinite(onset) or not 0 <= onset < duration:
                    discarded += 1
                    continue
                frame = min(int(round(onset * 100)), activations.shape[1] - 1)
                activation = float(activations[0, frame, class_index])
                hits.append(
                    DrumHit(
                        time_seconds=onset,
                        instrument=LABEL_TO_INSTRUMENT[label],
                        confidence=min(1.0, max(0.0, activation)),
                        velocity=None,
                    )
                )
        warnings = [
            "ADTOF recognizes five groups only: all toms use tom_mid, all hi-hats use hihat_closed, and all cymbals use crash. These are display representatives, not detected subtypes.",
            "ADTOF does not estimate velocity or articulations. The activation score is not a calibrated probability of correctness.",
        ]
        if discarded:
            warnings.append(f"Discarded {discarded} predictions outside the audio duration.")
        return Transcription(
            engine="adtof",
            duration_seconds=duration,
            hits=sorted(hits, key=lambda item: (item.time_seconds, item.instrument)),
            warnings=warnings,
            metadata={
                "upstream_revision": ADTOF_REVISION,
                "device": selected,
                "threshold": threshold,
                "time_resolution_seconds": 0.01,
                "confidence_kind": "uncalibrated_frame_activation",
                "velocity_source": "unavailable",
                "weights_path": str(weights.resolve()),
            },
        )
    finally:
        del model
        if selected == "cuda":
            torch.cuda.empty_cache()
