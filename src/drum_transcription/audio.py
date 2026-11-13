"""Decode uploaded audio and optionally separate drums using only local models."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import soundfile as sf

from .domain import EngineUnavailable, JobOptions

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".aiff", ".aif", ".opus"}


def decode_clip(source: Path, destination: Path, options: JobOptions, ffmpeg: str | None) -> float:
    if ffmpeg is None:
        raise EngineUnavailable("Cần cài FFmpeg và thêm vào PATH trước khi xử lý audio.")
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        str(options.clip_start_seconds),
        "-i",
        str(source),
        "-t",
        str(options.clip_duration_seconds),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg vượt quá 180 giây. Hãy thử đoạn ngắn hơn.") from exc
    if result.returncode or not destination.is_file():
        raise ValueError(
            "Không đọc được audio hoặc điểm bắt đầu nằm ngoài bản nhạc. " + result.stderr[-800:]
        )
    info = sf.info(destination)
    duration = float(info.duration)
    if duration < 0.1:
        raise ValueError("Đoạn audio rỗng hoặc quá ngắn. Kiểm tra điểm bắt đầu.")
    if options.first_downbeat_seconds is not None and options.first_downbeat_seconds >= duration:
        raise ValueError("Phách đầu tiên nằm ngoài đoạn audio thực tế.")
    return duration


def separation_status(models_dir: Path) -> dict:
    repo = models_dir / "demucs"
    installed = importlib.util.find_spec("demucs") is not None
    ready = installed and (repo / "htdemucs.yaml").is_file() and any(repo.glob("*.th"))
    return {
        "ready": ready,
        "detail": "Demucs local đã sẵn sàng."
        if ready
        else "Tuỳ chọn: cài extra demucs và chuẩn bị htdemucs.yaml cùng checkpoint trong models/demucs. Xem docs/MODELS.md.",
    }


def separate_drums(source: Path, output_dir: Path, models_dir: Path, device: str) -> Path:
    status = separation_status(models_dir)
    if not status["ready"]:
        raise EngineUnavailable(status["detail"])
    # An explicit local --repo prevents Demucs's default remote model lookup.
    command = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--repo",
        str(models_dir / "demucs"),
        "-n",
        "htdemucs",
        "--two-stems",
        "drums",
        "--other-method",
        "none",
        "--segment",
        "7",
        "--shifts",
        "0",
        "-j",
        "0",
        "-o",
        str(output_dir / "separated"),
    ]
    if device != "auto":
        command += ["--device", device]
    command.append(str(source))
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    log_path = output_dir / "separation.log"
    with log_path.open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=1800, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Tách trống vượt quá 30 phút. Thử clip ngắn hoặc tắt tách trống."
            ) from exc
    separated = output_dir / "separated" / "htdemucs" / source.stem / "drums.wav"
    if result.returncode or not separated.is_file():
        raise RuntimeError(
            "Demucs không tách được trống. Kiểm tra dependency/checkpoint local và separation.log trong thư mục job."
        )
    destination = output_dir / "drums.wav"
    shutil.copyfile(separated, destination)
    return destination


def prepare_demucs(models_dir: Path) -> dict:
    """The ONLY Demucs download path, called explicitly from setup-models."""
    repo = models_dir / "demucs"
    repo.mkdir(parents=True, exist_ok=True)
    filename = "955717e8-8726e21a.th"
    url = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/" + filename
    path = repo / filename
    expected_prefix = "8726e21a"

    def digest(file: Path) -> str:
        with file.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    if not path.is_file() or not digest(path).startswith(expected_prefix):
        temporary = path.with_suffix(".download")
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "DrumTranscription-setup/0.1"}
            )
            with (
                urllib.request.urlopen(request, timeout=120) as source,
                temporary.open("wb") as destination,
            ):
                shutil.copyfileobj(source, destination)
            if not digest(temporary).startswith(expected_prefix):
                raise ValueError(
                    "Demucs checkpoint checksum does not match upstream. Download discarded."
                )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    (repo / "htdemucs.yaml").write_text("models: ['955717e8']\n", encoding="utf-8")
    manifest = {
        "engine": "demucs",
        "model": "htdemucs",
        "source_url": url,
        "sha256": digest(path),
        "upstream_commit": "829d21890023800713e5dd231c67e43ecc06069b",
        "note": "Code MIT; checkpoint license requires separate review before redistribution.",
    }
    (repo / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"models_dir": str(repo.resolve()), **manifest}
