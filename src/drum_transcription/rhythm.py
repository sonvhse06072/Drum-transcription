"""Local beat/downbeat analysis. Setup is the only network-capable operation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import shutil
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from .domain import EngineUnavailable, RhythmAnalysis
from .engines.common import require_file, select_device

BEAT_THIS_REVISION = "b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c"
CHECKPOINT_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"
METER_PATTERN = re.compile(r"^(2|3|4|5|6|7|9|12)/(4|8)$")


def _checkpoint(models_dir: Path) -> Path:
    return Path(models_dir) / "beat_this" / "final0.ckpt"


def rhythm_status(models_dir: Path) -> dict:
    """No imports of heavy models, file downloads, or online readiness checks."""
    missing = []
    for name in ("torch", "torchaudio", "beat_this", "soxr", "einops", "rotary_embedding_torch"):
        try:
            present = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            present = False
        if not present:
            missing.append(name)
    checkpoint = _checkpoint(models_dir)
    detail = []
    if missing:
        detail.append("Thiếu thư viện: " + ", ".join(missing))
    if not checkpoint.is_file() or checkpoint.stat().st_size == 0:
        detail.append("Thiếu checkpoint Beat This! local")
    return {
        "engine": "beat_this",
        "ready": not detail,
        "detail": "; ".join(detail)
        + ". Cài extra beat và chạy drum-transcription setup-models beat_this."
        if detail
        else "Beat This! local sẵn sàng phân tích tempo và phách 1.",
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare_rhythm_model(models_dir: Path) -> dict:
    """Explicit setup: download the official checkpoint, validate it, record provenance."""
    try:
        import torch
    except ImportError as exc:
        raise EngineUnavailable("Cài extra beat trước: python -m pip install -e '.[beat]'") from exc
    path = _checkpoint(models_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path
    temporary = path.with_suffix(".download")
    if not path.is_file() or path.stat().st_size == 0:
        request = urllib.request.Request(
            CHECKPOINT_URL, headers={"User-Agent": "DrumTranscription-setup/0.2"}
        )
        try:
            with (
                urllib.request.urlopen(request, timeout=120) as source,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(source, output)
            candidate = temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        # No unrestricted pickle loading; a malformed download never becomes the active checkpoint.
        saved = torch.load(candidate, map_location="cpu", weights_only=True)
        if (
            not isinstance(saved, dict)
            or not isinstance(saved.get("state_dict"), dict)
            or not saved["state_dict"]
            or not isinstance(saved.get("hyper_parameters"), dict)
        ):
            raise ValueError("Checkpoint Beat This! không đúng định dạng.")
        del saved
        if candidate == temporary:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "engine": "beat_this",
        "model": "final0",
        "source_revision": BEAT_THIS_REVISION,
        "source_url": CHECKPOINT_URL,
        "weights_sha256": _sha256(path),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "license": "MIT (code and published weights)",
    }
    (path.parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def analysis_from_beats(
    beats,
    downbeats,
    *,
    duration_seconds: float,
    meter_hint: str | None = None,
) -> RhythmAnalysis:
    """Convert model positions to an auditable musical grid without inventing a first beat."""
    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Audio không có độ dài hợp lệ để phân tích nhịp.")
    for values in (beats, downbeats):
        if values.ndim != 1 or not np.isfinite(values).all() or np.any(np.diff(values) <= 0):
            raise ValueError(
                "Model trả về lưới nhịp không hợp lệ; không tạo tempo hoặc phách 1 giả."
            )
        if np.any(values < 0) or np.any(values > duration_seconds + 0.021):
            raise ValueError("Model trả về nhịp nằm ngoài đoạn audio.")
    # Centered spectral frames can include the exact audio end or one padded
    # 20ms frame. Those contain no playable samples and must not invalidate an
    # otherwise usable grid. Larger out-of-range predictions still fail above.
    discarded_tail_beats = int(np.sum(beats >= duration_seconds))
    discarded_tail_downbeats = int(np.sum(downbeats >= duration_seconds))
    beats = beats[beats < duration_seconds]
    downbeats = downbeats[downbeats < duration_seconds]
    if len(beats) < 4 or len(downbeats) < 2:
        raise ValueError(
            "Chưa phát hiện đủ beat và ít nhất hai phách 1 để xác định tempo/ô nhịp. Thử đoạn 20–60 giây có nhịp rõ, hoặc dùng chế độ chỉnh thủ công."
        )
    if meter_hint is not None and not METER_PATTERN.fullmatch(meter_hint):
        raise ValueError("Số chỉ nhịp gợi ý không được hỗ trợ.")
    downbeat_indices = np.array([int(np.argmin(abs(beats - value))) for value in downbeats])
    if np.any(abs(beats[downbeat_indices] - downbeats) > 0.06) or np.any(
        np.diff(downbeat_indices) <= 0
    ):
        raise ValueError("Beat và phách 1 không khớp; không tự lấy cú đánh đầu làm phách 1.")
    aligned_downbeats = beats[downbeat_indices]
    counts = np.diff(downbeat_indices)
    frequencies = Counter(int(value) for value in counts)
    candidates = sorted(frequencies)
    # If downbeats are missed, an interval can span multiple complete bars. Only
    # choose a shorter recurring count when every observed interval is its multiple.
    compatible = [
        candidate
        for candidate in candidates
        if candidate >= 2 and all(count % candidate == 0 for count in counts)
    ]
    if len(compatible) != 1:
        raise ValueError(
            "Số beat giữa các phách 1 không nhất quán (có thể đổi nhịp hoặc thiếu beat). Thử clip ngắn hơn có nhịp ổn định, hoặc chỉnh thủ công."
        )
    beats_per_bar = compatible[0]
    warnings = []
    if discarded_tail_beats or discarded_tail_downbeats:
        warnings.append(
            "Đã bỏ marker beat/downbeat trong frame đệm ở mép cuối clip; không có mẫu audio tương ứng để phát."
        )
    if meter_hint is None:
        if beats_per_bar not in (2, 3, 4, 5, 6, 7, 9, 12):
            raise ValueError(
                "Chưa suy ra được số chỉ nhịp từ nhóm beat. Chọn số chỉ nhịp gợi ý hoặc thử đoạn khác."
            )
        chosen_meter = f"{beats_per_bar}/4"
        warnings.append(
            "Số chỉ nhịp được gợi ý từ nhóm beat. Nhịp kép (ví dụ 6/8 so với 2/4) và cách đếm half/double-time có thể cần chỉnh lại; đây không phải xác nhận tuyệt đối về số chỉ nhịp."
        )
    else:
        chosen_meter = meter_hint
    numerator, denominator = map(int, chosen_meter.split("/"))
    bar_quarters = numerator * 4 / denominator
    beat_unit = bar_quarters / beats_per_bar
    if not any(
        math.isclose(beat_unit, value, abs_tol=1e-9) for value in (0.25, 0.5, 1, 1.5, 2, 3, 4, 6)
    ):
        raise ValueError(
            "Số chỉ nhịp gợi ý không khớp cách nhóm beat đã phát hiện. Bỏ gợi ý để model đề xuất nhịp, hoặc chủ động dùng chế độ thủ công."
        )
    intervals = np.diff(beats)
    median_interval = float(np.median(intervals))
    if (
        median_interval <= 0
        or np.any(intervals > median_interval * 2.25)
        or np.any(intervals < median_interval * 0.40)
    ):
        raise ValueError(
            "Lưới beat bị đứt quãng hoặc có beat bất thường; chưa thể căn nhịp tự động đáng tin cậy. Thử đoạn có nhịp rõ hơn hoặc chỉnh thủ công."
        )
    bpm = 60 * beat_unit / median_interval
    if not 20 <= bpm <= 400:
        raise ValueError(
            "Tempo suy ra nằm ngoài 20–400 nốt đen/phút; kiểm tra số chỉ nhịp hoặc dùng clip khác."
        )
    p10, p90 = np.percentile(intervals, [10, 90])
    if p90 / p10 > 1.12:
        warnings.append(
            "Khoảng cách beat thay đổi đáng kể. Bản ký âm dùng lưới beat đã phát hiện; BPM hiển thị là giá trị đại diện, không phải tempo cố định của toàn đoạn."
        )
    if len(downbeats) == 2:
        warnings.append(
            "Chỉ có một khoảng ô nhịp đầy đủ để suy ra số chỉ nhịp; nghe lớp click và thử đoạn dài hơn nếu kết quả chưa đúng."
        )
    missing_downbeats = sum(int(count // beats_per_bar - 1) for count in counts)
    if missing_downbeats:
        warnings.append(
            f"Có {missing_downbeats} vị trí đầu ô nhịp không được model đánh dấu trực tiếp; lưới beat vẫn được giữ, cần nghe kiểm tra các đoạn này."
        )
    positions = (np.arange(len(beats), dtype=float) - int(downbeat_indices[0])) * beat_unit
    return RhythmAnalysis(
        engine="beat_this",
        bpm=float(bpm),
        meter=chosen_meter,
        first_downbeat_seconds=float(aligned_downbeats[0]),
        beats_seconds=beats.tolist(),
        downbeats_seconds=aligned_downbeats.tolist(),
        beat_positions_quarters=positions.tolist(),
        beat_unit_quarters=float(beat_unit),
        warnings=warnings,
        metadata={
            "source_revision": BEAT_THIS_REVISION,
            "model": "final0",
            "postprocessing": "minimal_no_dbn",
            "time_resolution_seconds": 0.02,
            "time_reference": "decoded_clip_start",
            "bpm_kind": "median_detected_beat_interval_converted_to_quarter_notes",
            "meter_source": "user_hint"
            if meter_hint is not None
            else "detected_beat_group_suggestion",
            "beats_per_bar": beats_per_bar,
            "observed_bar_intervals": len(counts),
            "single_bar_interval_support": frequencies[beats_per_bar],
            "bar_interval_beat_counts": counts.tolist(),
            "missing_downbeat_markers": missing_downbeats,
            "beat_interval_p10_seconds": float(p10),
            "beat_interval_p90_seconds": float(p90),
            "raw_downbeats_seconds": downbeats.tolist(),
            "discarded_tail_beats": discarded_tail_beats,
            "discarded_tail_downbeats": discarded_tail_downbeats,
            "confidence_kind": "not_calibrated_or_reported",
        },
    )


def analyze_rhythm(
    audio_path: Path,
    models_dir: Path,
    *,
    device: str = "auto",
    meter_hint: str | None = None,
) -> RhythmAnalysis:
    checkpoint = require_file(_checkpoint(models_dir))
    status = rhythm_status(models_dir)
    if not status["ready"]:
        raise EngineUnavailable(status["detail"])
    try:
        import torch
        from beat_this.inference import Audio2Beats
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(
            "Không nạp được Beat This! Kiểm tra extra beat và cặp Torch/TorchAudio cùng build CPU/CUDA."
        ) from exc
    waveform, rate = sf.read(audio_path, dtype="float32", always_2d=True)
    if not len(waveform) or not np.isfinite(waveform).all():
        raise ValueError("Audio rỗng hoặc có giá trị không hợp lệ.")
    if np.max(np.abs(waveform)) < 1e-7:
        raise ValueError("Đoạn audio im lặng; không thể nhận diện tempo hoặc phách 1.")
    selected = select_device(torch, device)
    tracker = None
    try:
        # Passing an already-open local stream to torch.load prevents a missing
        # pathname from activating upstream's named-model download fallback.
        with checkpoint.open("rb") as local_weights:
            tracker = Audio2Beats(checkpoint_path=local_weights, device=selected, dbn=False)
        with torch.inference_mode():
            beats, downbeats = tracker(waveform, rate)
        result = analysis_from_beats(
            beats, downbeats, duration_seconds=len(waveform) / rate, meter_hint=meter_hint
        )
        result.metadata["device"] = selected
        manifest_path = checkpoint.parent / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                result.metadata["weights_sha256"] = manifest.get("weights_sha256")
            except (OSError, ValueError):
                pass
        return result
    finally:
        if tracker is not None:
            del tracker
        if selected == "cuda":
            torch.cuda.empty_cache()


def write_rhythm_preview(audio_path: Path, analysis: RhythmAnalysis, output_path: Path) -> None:
    """Overlay distinct detected beat/downbeat clicks onto the exact decoded clip."""
    waveform, rate = sf.read(audio_path, dtype="float32", always_2d=True)
    if not len(waveform) or not np.isfinite(waveform).all():
        raise ValueError("Không tạo được audio đối chiếu nhịp từ file rỗng hoặc không hợp lệ.")
    waveform *= 0.65
    t = np.arange(max(1, round(0.035 * rate)), dtype=np.float32) / rate
    envelope = np.exp(-120 * t)
    normal = 0.22 * np.sin(2 * np.pi * 1100 * t) * envelope
    strong = 0.38 * np.sin(2 * np.pi * 1800 * t) * envelope
    for when in analysis.beats_seconds:
        click = (
            strong if any(abs(when - db) < 1e-6 for db in analysis.downbeats_seconds) else normal
        )
        first = round(when * rate)
        count = min(len(click), len(waveform) - first)
        if first >= 0 and count > 0:
            waveform[first : first + count] += click[:count, None]
    peak = float(np.max(abs(waveform)))
    if peak > 0.98:
        waveform *= 0.98 / peak
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, waveform, rate, subtype="PCM_16")
