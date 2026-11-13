"""Reproduce local meter diagnostics on deterministic synthetic drum fixtures."""

from __future__ import annotations

import argparse
import json
import socket
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

RATE = 22050
SEED = 2704
MODE = "fixture_synthetic_not_accuracy_benchmark"
FIXTURES = {
    "mixed": ["4/4"] * 8 + ["3/4"] * 8 + ["7/8"] * 8,
    "seven": ["7/8"] * 12,
    "five": ["5/8"] * 16,
}
EIGHTH_GROUPS = {"4/4": [2, 2, 2, 2], "3/4": [2, 2, 2], "5/8": [2, 3], "7/8": [2, 2, 3]}


def synthesize(meters: list[str], rng: np.random.Generator) -> tuple[np.ndarray, list[dict], float]:
    bars = []
    start = 0.371
    for signature in meters:
        groups = EIGHTH_GROUPS[signature]
        bars.append({"start_seconds": start, "meter": signature, "eighth_groups": groups})
        start += sum(groups) * 0.25
    audio = np.zeros(round((start + 0.2) * RATE), np.float32)

    def add(when: float, instrument: str, gain: float = 1) -> None:
        length = 0.2 if instrument == "kick" else 0.13 if instrument == "snare" else 0.055
        times = np.arange(round(length * RATE)) / RATE
        if instrument == "kick":
            wave = np.sin(2 * np.pi * (45 * times + 2.5 * (1 - np.exp(-30 * times)))) * np.exp(-24 * times)
        elif instrument == "snare":
            wave = (rng.normal(size=len(times)) * 0.7 + np.sin(2 * np.pi * 185 * times) * 0.3) * np.exp(-40 * times)
        else:
            wave = np.diff(rng.normal(size=len(times) + 1)) * np.exp(-100 * times) * 0.16
        first = round(when * RATE)
        count = min(len(wave), len(audio) - first)
        audio[first:first + count] += gain * wave[:count]

    for bar in bars:
        group_starts = np.concatenate(([0], np.cumsum(bar["eighth_groups"])[:-1])).tolist()
        for index in range(sum(bar["eighth_groups"])):
            when = bar["start_seconds"] + index * 0.25
            add(when, "hat", 1.1 if index in group_starts else 0.7)
            if index == 0 or (bar["meter"] in ("4/4", "7/8") and index == 4):
                add(when, "kick", 0.75 if index else 1)
            if (bar["meter"] == "4/4" and index in (2, 6)) or (bar["meter"] != "4/4" and index == 2):
                add(when, "snare", 0.65)
    audio *= 0.85 / max(abs(audio))
    return audio, bars, start + 0.2


def positive_integer(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("threads must be at least 1")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/meter-check"))
    parser.add_argument("--fixture", choices=[*FIXTURES, "all"], default="all")
    parser.add_argument("--device", choices=["cpu", "auto", "cuda", "mps"], default="cpu")
    parser.add_argument("--threads", type=positive_integer, default=2, help="PyTorch CPU threads (default: 2)")
    parser.add_argument("--generate-only", action="store_true", help="write fixtures/truth without importing or running the AI model")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    reports = []
    failed = False
    for name, signatures in FIXTURES.items():
        # Always consume the same RNG sequence. Selecting one fixture produces
        # identical audio to selecting it as part of --fixture all.
        audio, truth, duration_seconds = synthesize(signatures, rng)
        if args.fixture not in (name, "all"):
            continue
        path = args.output_dir / f"{name}.wav"
        sf.write(path, audio, RATE, subtype="PCM_16")
        report = {
            "fixture": name, "audio_file": str(path), "mode": MODE,
            "duration_seconds": duration_seconds, "quarter_bpm_truth": 120,
            "first_downbeat_truth_seconds": 0.371, "truth_bars": truth,
            "inference_attempted": False, "inference_ran": False, "network_attempts": 0,
        }
        attempts = []

        def no_network(*_args, **_kwargs):
            attempts.append("blocked Python socket call")
            raise RuntimeError("Network access is forbidden during this local model diagnostic.")

        began = time.monotonic()
        if args.generate_only:
            report["status"] = "generated_only"
        else:
            try:
                with ExitStack() as stack:
                    for owner, method in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
                        stack.enter_context(patch.object(owner, method, no_network))
                    import torch

                    from drum_transcription.rhythm import analyze_rhythm

                    torch.set_num_threads(args.threads)
                    report["inference_attempted"] = True
                    result = analyze_rhythm(path, args.models_dir, device=args.device)
                    report["inference_ran"] = True
                segments = []
                for index, bar in enumerate(result.bars):
                    if not segments or (bar.meter, bar.grouping) != (segments[-1]["meter"], segments[-1]["grouping"]):
                        segments.append({"bar_index": index, "start_seconds": bar.start_seconds, "meter": bar.meter, "grouping": bar.grouping})
                analysis_path = args.output_dir / f"{name}-analysis.json"
                analysis_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
                report.update({
                    "status": "completed", "analysis_file": str(analysis_path),
                    "detected_bpm": result.bpm,
                    "detected_first_downbeat_seconds": result.first_downbeat_seconds,
                    "detected_segments": segments,
                    "observed_counts": result.metadata.get("bar_interval_beat_counts"),
                    "metadata": result.metadata, "warnings": result.warnings,
                })
            except Exception as exc:
                failed = True
                report.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})
        report["elapsed_seconds"] = round(time.monotonic() - began, 3)
        report["network_attempts"] = len(attempts)
        reports.append(report)
        print(json.dumps({key: value for key, value in report.items() if key not in ("truth_bars", "metadata")}, ensure_ascii=False), flush=True)
    output = {"mode": MODE, "seed": SEED, "sample_rate": RATE, "models_dir": str(args.models_dir), "device": args.device, "threads": args.threads, "fixtures": reports}
    (args.output_dir / "report.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
