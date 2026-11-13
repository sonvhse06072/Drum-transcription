"""Real audio test for optional meter-aware model, with offline inference.

This does not treat source metadata as model inputs. Composer sequence is
reported ONLY after inference; no reference bar timestamps are available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

from drum_transcription.rhythm_sheetsage2 import analyze_meter_rhythm

EXPECTED_SHA = "93b965383f397943d7526c7a150f3beff80ca42b544432659889a99f4defa4eb"
REFERENCE = ["6/8", "7/8", "2/4", "2/4", "7/8", "9/8", "5/16"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    sha = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    if sha != EXPECTED_SHA:
        raise SystemExit("Room 34 input SHA-256 does not match published benchmark.")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    attempts = []
    def deny(*args, **kwargs):
        attempts.append("network")
        raise RuntimeError("External network attempted during inference.")

    error = None
    result = None
    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket, "create_connection", deny),
        patch.object(socket, "getaddrinfo", deny),
    ):
        try:
            import torch
            torch.set_num_threads(2)
            result = analyze_meter_rhythm(
                args.audio, args.models_dir, device=args.device,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    report = {
        "input": "Room34 CC0 Micro-Prog (original published OGG)",
        "audio_sha256": sha,
        "reference_meters": REFERENCE,
        "ground_truth_downbeat_timestamps": None,
        "status": "failed" if error else "completed",
        "error": error,
        "network_attempts": len(attempts),
        "rhythm_engine": "sheetsage2_refined",
        "prediction": None if result is None else {
            "engine": result.engine,
            "bar_count": len(result.bars),
            "meters": [bar.meter for bar in result.bars],
            "start_times_seconds": [bar.start_seconds for bar in result.bars],
            "first_downbeat_seconds": result.first_downbeat_seconds,
            "bpm": result.bpm,
            "weights_revision": result.metadata.get("model_weights_revision"),
            "weights_sha256": result.metadata.get("weights_sha256"),
            "head_sha256": result.metadata.get("head_sha256"),
            "warnings": result.warnings,
        },
        "reference_sequence_exact_match": (
            None if result is None
            else [bar.meter for bar in result.bars] == REFERENCE
        ),
        "note": (
            "Bar-count/meter sequence can be compared to the composer, "
            "but exact downbeat timing cannot be scored without reference timestamps. "
            "A model inference success is not a recognition accuracy PASS."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
