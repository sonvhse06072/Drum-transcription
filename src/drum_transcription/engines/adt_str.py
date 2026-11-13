"""ADT_STR adapter; its custom output vocabulary is not General MIDI."""

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from drum_transcription.domain import DrumHit, EngineUnavailable, Transcription

from . import ADT_STR_MODEL_REVISION, ADT_STR_REVISION, ADT_STR_VARIANT
from .common import require_file

CUSTOM_TO_INSTRUMENT = {
    35: "kick",
    36: "kick",
    37: "side_stick",
    38: "snare",
    39: "clap",
    40: "snare",
    41: "tom_low",
    42: "hihat_closed",
    43: "hihat_pedal",
    44: "hihat_open",
    45: "tom_mid",
    46: "crash",
    47: "tom_high",
    48: "ride",
    49: "china",
    50: "tambourine",
    51: "splash",
    52: "cowbell",
    53: "vibraslap",
    54: "conga",
    55: "shaker",
    56: "whistle",
    57: "guiro",
    58: "claves",
    59: "cuica",
    60: "triangle",
}


def normalize_custom_notes(
    notes: list, duration: float, *, velocity_available: bool = True
) -> tuple[list[DrumHit], list[str]]:
    """Preserve onsets; retain unknown classes in raw data for explicit export warnings."""
    hits = []
    discarded = 0
    unknown = set()
    for row in notes:
        if len(row) != 4:
            discarded += 1
            continue
        onset, _offset, pitch, velocity = row
        if not all(math.isfinite(float(value)) for value in row):
            discarded += 1
            continue
        if not 0 <= float(onset) < duration or float(pitch) != int(pitch):
            discarded += 1
            continue
        pitch = int(pitch)
        instrument = CUSTOM_TO_INSTRUMENT.get(pitch, f"unknown_adt_{pitch}")
        if pitch not in CUSTOM_TO_INSTRUMENT:
            unknown.add(pitch)
        hit_velocity = int(velocity) if velocity_available and 1 <= velocity <= 127 else None
        hits.append(
            DrumHit(time_seconds=float(onset), instrument=instrument, velocity=hit_velocity)
        )
    warnings = []
    if discarded:
        warnings.append(f"Discarded {discarded} malformed or padded-tail ADT_STR events.")
    if unknown:
        warnings.append(f"Unknown ADT_STR class IDs retained in raw JSON: {sorted(unknown)}.")
    return sorted(hits, key=lambda item: (item.time_seconds, item.instrument)), warnings


def transcribe_adt_str(audio_path: Path, *, device: str, models_dir: Path) -> Transcription:
    source = models_dir / "adt_str" / "source"
    checkpoint = models_dir / "adt_str" / "checkpoint" / ADT_STR_VARIANT
    require_file(source / "adt_transcriber.py")
    for name in ("adt_config.yaml", "config.json", "model.safetensors"):
        require_file(checkpoint / name)
    env = dict(os.environ)
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "WANDB_DISABLED": "true",
        }
    )
    with tempfile.TemporaryDirectory(prefix="drum-adt-str-") as temp:
        output = Path(temp) / "notes.json"
        command = [
            sys.executable,
            "-m",
            "drum_transcription.engines.adt_str_worker",
            "--audio",
            str(audio_path.resolve()),
            "--source",
            str(source.resolve()),
            "--checkpoint",
            str(checkpoint.resolve()),
            "--device",
            device,
            "--output",
            str(output),
        ]
        try:
            process = subprocess.run(
                command, env=env, capture_output=True, text=True, timeout=3600, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ADT_STR exceeded one hour. Try a shorter clip or a GPU.") from exc
        if process.returncode:
            detail = (process.stderr or process.stdout).strip()[-2500:]
            raise EngineUnavailable(f"ADT_STR local worker failed. {detail}")
        if not output.is_file():
            raise RuntimeError("ADT_STR worker did not produce event data.")
        data = json.loads(output.read_text(encoding="utf-8"))
    duration = float(data["duration_seconds"])
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("ADT_STR returned an invalid audio duration.")
    hits, warnings = normalize_custom_notes(
        data["notes"], duration, velocity_available=data["velocity_available"]
    )
    warnings.extend(
        [
            "ADT_STR groups ride bell with ride, bass-drum variants with kick, snare variants with snare, and bongos/congas/timbales with conga.",
            "Velocity is a model estimate. Ghost notes, accents, flams, rolls and sticking are not inferred by this adapter.",
            "ADT_STR uses non-overlapping 2.56-second chunks; inspect events near chunk boundaries. The ADTOF threshold control does not apply to this engine.",
        ]
    )
    return Transcription(
        engine="adt_str",
        duration_seconds=duration,
        hits=hits,
        warnings=warnings,
        metadata={
            "upstream_revision": ADT_STR_REVISION,
            "model_revision": ADT_STR_MODEL_REVISION,
            "variant": ADT_STR_VARIANT,
            "device": data["device"],
            "batch_size": 1,
            "time_resolution_seconds": 0.01,
            "confidence_kind": "unavailable",
            "velocity_source": "model_token" if data["velocity_available"] else "unavailable",
            "raw_custom_notes": data["notes"],
        },
    )
