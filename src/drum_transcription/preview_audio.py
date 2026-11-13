"""Small, offline percussion synthesizer for checking detected onset timing.

This is deliberately an audition aid, not a realistic drum sample library and
not an audio transcription model. It downloads no assets or SoundFonts.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .domain import DrumHit


def _sound(instrument: str, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    length = 0.65 if instrument in {"hihat_open", "crash", "ride", "ride_bell", "china", "splash"} else 0.24
    t = np.arange(round(length * sample_rate), dtype=np.float32) / sample_rate
    noise = rng.standard_normal(len(t)).astype(np.float32)
    bright_noise = noise - np.concatenate((np.zeros(1, dtype=np.float32), noise[:-1]))
    if instrument == "kick":
        # Analytic phase integral of a falling pitch; no external synthesizer.
        phase = 2 * np.pi * (48 * t + 50 * (1 - np.exp(-30 * t)) / 30)
        sound = np.sin(phase) * np.exp(-18 * t) + 0.05 * noise * np.exp(-160 * t)
    elif instrument.startswith("tom_") or instrument == "conga":
        frequency = {"tom_low": 90, "tom_mid": 140, "tom_high": 200, "conga": 240}.get(instrument, 140)
        sound = np.sin(2 * np.pi * frequency * t) * np.exp(-15 * t) + 0.06 * noise * np.exp(-70 * t)
    elif instrument in {"snare", "clap"}:
        sound = 0.5 * noise * np.exp(-25 * t) + 0.3 * np.sin(2 * np.pi * 185 * t) * np.exp(-35 * t)
    elif instrument in {"side_stick", "claves", "cowbell", "ride_bell"}:
        sound = (np.sin(2 * np.pi * 700 * t) + 0.3 * np.sin(2 * np.pi * 1120 * t)) * np.exp(-35 * t)
    elif instrument in {"hihat_closed", "hihat_pedal"}:
        sound = 0.25 * bright_noise * np.exp(-70 * t)
    else:
        decay = 9 if length > 0.5 else 30
        sound = 0.2 * bright_noise * np.exp(-decay * t)
    # One fixed gain keeps model-provided velocities audible relative to others.
    return sound.astype(np.float32)


def write_preview_audio(
    hits: list[DrumHit], path: Path, *, duration_seconds: float, sample_rate: int = 22050
) -> None:
    tail = 0.7
    end = max(duration_seconds, max((hit.time_seconds for hit in hits), default=0) + tail)
    output = np.zeros(max(1, math_ceil_samples(end, sample_rate)), dtype=np.float32)
    rng = np.random.default_rng(42)
    sounds: dict[str, np.ndarray] = {}
    for hit in hits:
        if hit.instrument not in sounds:
            sounds[hit.instrument] = _sound(hit.instrument, sample_rate, rng)
        sound = sounds[hit.instrument]
        onset = round(hit.time_seconds * sample_rate)
        count = min(len(sound), len(output) - onset)
        if count > 0:
            output[onset:onset + count] += sound[:count] * (hit.velocity or 100) / 127
    peak = float(np.max(np.abs(output)))
    if peak > 0.95:
        output *= 0.95 / peak
    sf.write(path, output, sample_rate, subtype="PCM_16")


def math_ceil_samples(seconds: float, sample_rate: int) -> int:
    # Avoid a separate DSP dependency for a single conversion.
    return int(np.ceil(seconds * sample_rate))
