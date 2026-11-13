import subprocess

import numpy as np
import pytest
import soundfile as sf

from drum_transcription.audio import decode_clip
from drum_transcription.config import Settings
from drum_transcription.domain import JobOptions


def test_mp3_fractional_seek_preserves_clip_timeline(tmp_path):
    settings = Settings(tmp_path / "data", tmp_path / "models")
    if settings.ffmpeg is None:
        pytest.skip("FFmpeg required for MP3 regression")
    rate = 44100
    audio = np.zeros(4 * rate, dtype=np.float32)
    # A short tone centered at2.105s in the upload should be around0.605s
    # after seeking to1.5s, including MP3 encoder-delay compensation.
    length = round(0.01 * rate)
    tone = 0.8 * np.sin(2 * np.pi * 1200 * np.arange(length) / rate) * np.hanning(length)
    first = round(2.1 * rate)
    audio[first : first + length] = tone
    source, encoded, clipped = (
        tmp_path / "source.wav",
        tmp_path / "source.mp3",
        tmp_path / "clip.wav",
    )
    sf.write(source, audio, rate)
    subprocess.run(
        [
            settings.ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(encoded),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    duration = decode_clip(
        encoded,
        clipped,
        JobOptions(clip_start_seconds=1.5, clip_duration_seconds=2),
        settings.ffmpeg,
    )
    samples, decoded_rate = sf.read(clipped, always_2d=True)
    peak_time = np.argmax(np.max(abs(samples), axis=1)) / decoded_rate
    assert duration == pytest.approx(2, abs=1 / rate)
    assert peak_time == pytest.approx(0.605, abs=0.02)
