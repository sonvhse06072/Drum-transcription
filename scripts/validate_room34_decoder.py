"""Reproduce the CC0 Room 34 mixed-meter case with actual offline model inference.

Download audio and weights explicitly before running. Never claim an accuracy
pass from a successful export or an improvement in bar count alone.
"""

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from drum_transcription.rhythm import analysis_from_beats

EXPECTED_SHA256 = "93b965383f397943d7526c7a150f3beff80ca42b544432659889a99f4defa4eb"
EXPECTED_METERS = ["6/8", "7/8", "2/4", "2/4", "7/8", "9/8", "5/16"]


def summarize(rhythm):
    return {
        "bars": len(rhythm.bars),
        "meters": [bar.meter for bar in rhythm.bars],
        "first_downbeat_seconds": rhythm.first_downbeat_seconds,
        "bpm": rhythm.bpm,
        "review_status": rhythm.metadata.get("rhythm_review_status"),
        "decoder_mode": rhythm.metadata.get("barline_decoder_mode"),
        "filtered_markers": rhythm.metadata.get("barline_filtered_count", 0),
        "inserted_markers": rhythm.metadata.get("barline_inserted_count", 0),
        "raw_marker_count": rhythm.metadata.get("barline_raw_marker_count"),
        "warnings": rhythm.warnings,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()

    sha = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    if sha != EXPECTED_SHA256:
        raise SystemExit(f"Room 34 audio SHA-256 mismatch: {sha}")
    if not args.checkpoint.is_file():
        raise SystemExit(f"Missing checkpoint: {args.checkpoint}")
    import torch
    from beat_this.inference import Audio2Beats

    audio, sample_rate = sf.read(args.audio, dtype="float32", always_2d=True)
    duration = len(audio) / sample_rate

    def block_network(*args, **kwargs):
        raise RuntimeError("Inference attempted external network access.")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    with (
        patch.object(socket.socket, "connect", block_network),
        patch.object(socket, "create_connection", block_network),
        patch.object(socket, "getaddrinfo", block_network),
    ):
        with args.checkpoint.open("rb") as checkpoint:
            tracker = Audio2Beats(checkpoint_path=checkpoint, device=args.device, dbn=False)
        with torch.inference_mode():
            spectrogram = tracker.signal2spect(audio, sample_rate)
            beat_logits, downbeat_logits = tracker.spect2frames(spectrogram)
            beats, downbeats = tracker.frames2beats(beat_logits, downbeat_logits)
            frame_scores = downbeat_logits.detach().float().cpu().numpy()
        before = analysis_from_beats(beats, downbeats, duration_seconds=duration)
        after = analysis_from_beats(
            beats, downbeats, duration_seconds=duration,
            downbeat_logits=frame_scores,
        )

    report = {
        "source": "Room 34 CC0 Micro-Prog original OGG",
        "audio_sha256": sha,
        "duration_seconds": duration,
        "expected_meters_from_composer": EXPECTED_METERS,
        "ground_truth_bar_timestamps_available": False,
        "raw_model_beat_count": len(beats),
        "raw_model_downbeat_count": len(downbeats),
        "before": summarize(before),
        "after": summarize(after),
        "composer_sequence_recovered": [bar.meter for bar in after.bars] == EXPECTED_METERS,
        "note": "Model-based estimate. Accurate barline timing cannot be scored without reference timestamps.",
        "network_attempts": 0,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "before_bars": report["before"]["bars"],
        "after_bars": report["after"]["bars"],
        "before_meters": report["before"]["meters"],
        "after_meters": report["after"]["meters"],
        "composer_sequence_recovered": report["composer_sequence_recovered"],
        "filtered_markers": report["after"]["filtered_markers"],
        "mode": report["after"]["decoder_mode"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
