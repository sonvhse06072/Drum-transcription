"""Optional local SheetSage2-Refined beat/bar-head adapter.

Only the frozen audio encoder and beat/bar head are used; the music decoder is
not loaded. Model files are obtained only by the explicit setup command.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
from bisect import bisect_right
from pathlib import Path
from statistics import median
from typing import Any

from .domain import METER_PATTERN, EngineUnavailable, RhythmAnalysis, RhythmBar
from .engines.common import require_file, select_device

SOURCE_REVISION = "6b966e2e495b51024753c29b4bef8fa82935b51a"
MODEL_REPO = "anime-song/SheetSage2-Refined"
MODEL_LICENSE = "CC BY-NC 4.0 (non-commercial weights)"
METER_RE = re.compile(METER_PATTERN)


def _folder(models_dir: Path) -> Path:
    return Path(models_dir) / "sheetsage2_refined"


def _relative_model_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("SheetSage2 model config has a missing artifact path.")
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Unsafe SheetSage2 model artifact path.")
    if path.suffix != ".safetensors":
        raise ValueError("SheetSage2 weights must be SafeTensors artifacts.")
    return path


def _artifacts(models_dir: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    folder = _folder(models_dir)
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise EngineUnavailable("Thiếu manifest SheetSage2-Refined local. Chạy setup-models.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("engine") != "sheetsage2_refined"
        or manifest.get("source_revision") != SOURCE_REVISION
        or manifest.get("model_repo") != MODEL_REPO
        or not re.fullmatch(r"[a-f0-9]{40}", str(manifest.get("weights_revision", "")))
    ):
        raise EngineUnavailable("SheetSage2 manifest không hợp lệ hoặc không đúng phiên bản source.")
    config_path = require_file(folder / "config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weights_name = _relative_model_path(config["base_weights"])
    head_name = _relative_model_path(config["beat_tracker"]["head"])
    if [str(weights_name), str(head_name)] != manifest.get("artifacts"):
        raise EngineUnavailable("Model configuration and recorded artifact paths disagree.")
    weights = require_file(folder / weights_name)
    head = require_file(folder / head_name)
    return weights, head, config_path, manifest


def meter_status(models_dir: Path) -> dict:
    missing = []
    for name in ("torch", "torchaudio", "sheetsage2_refined", "safetensors", "numba"):
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    if not missing:
        try:
            _artifacts(models_dir)
        except (OSError, ValueError, KeyError, TypeError, EngineUnavailable) as exc:
            missing.append(str(exc))
    return {
        "id": "sheetsage2_refined",
        "label": "SheetSage2-Refined · nhịp lẻ / đổi nhịp",
        "ready": not missing,
        "detail": (
            "Sẵn sàng (kiểm tra file/module, chưa xác nhận accuracy trên PC)."
            if not missing
            else "Thiếu: " + "; ".join(missing)
            + ". Cài extra 'meter-aware' và chạy setup-models sheetsage2_refined."
        ),
        "license": MODEL_LICENSE,
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prepare_meter_model(models_dir: Path) -> dict:
    """Explicit network-enabled setup. No other method here downloads files."""
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError as exc:
        raise EngineUnavailable(
            "Cài extra trước: python -m pip install -e '.[meter-aware]'"
        ) from exc
    folder = _folder(models_dir)
    folder.mkdir(parents=True, exist_ok=True)
    previous = folder / "manifest.json"
    if previous.is_file():
        try:
            _artifacts(models_dir)
            return json.loads(previous.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, KeyError, EngineUnavailable):
            raise EngineUnavailable(
                "SheetSage2 cài đặt chưa hoàn chỉnh. Giữ lại file để kiểm tra; "
                "di chuyển models/sheetsage2_refined rồi chạy setup lại."
            ) from None
    # Resolve 'main' exactly once during explicit setup. Every downloaded file
    # is fetched from this immutable Hugging Face git revision, not future main.
    model_info = HfApi().model_info(MODEL_REPO, revision="main")
    revision = str(model_info.sha)
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise EngineUnavailable("Không nhận được SHA model cố định từ Hugging Face.")
    def download(name: str) -> Path:
        return Path(hf_hub_download(
            repo_id=MODEL_REPO, filename=name, revision=revision, local_dir=str(folder),
        ))
    config_path = download("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weights_name = _relative_model_path(config["base_weights"])
    head_name = _relative_model_path(config["beat_tracker"]["head"])
    artifacts = [str(weights_name), str(head_name)]
    weights_path = download(artifacts[0])
    head_path = download(artifacts[1])
    for path in (weights_path, head_path):
        require_file(path)
        if not path.resolve().is_relative_to(folder.resolve()):
            raise EngineUnavailable("A model file unexpectedly escaped its local directory.")
    manifest = {
        "engine": "sheetsage2_refined",
        "source_revision": SOURCE_REVISION,
        "model_repo": MODEL_REPO,
        "weights_revision": revision,
        "license": MODEL_LICENSE,
        "artifacts": artifacts,
        "config_sha256": _sha256(config_path),
        "weights_sha256": _sha256(weights_path),
        "head_sha256": _sha256(head_path),
    }
    temporary = folder / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(previous)
    return manifest


def _clean_predictions(payload: dict, duration_seconds: float):
    """Validate the advertised beat/bar results without 'fixing' the AI labels."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Độ dài audio không hợp lệ.")
    beats = payload.get("beats")
    downbeats = payload.get("downbeats")
    raw_bars = payload.get("bars")
    if not isinstance(beats, list) or not isinstance(downbeats, list) or not isinstance(raw_bars, list):
        raise ValueError("SheetSage2 không trả về beat/downbeat/bar map hợp lệ.")
    if len(beats) < 4 or len(downbeats) < 2 or len(raw_bars) < 2:
        raise ValueError("Chưa có đủ dữ liệu phách và ô; hãy thử clip dài hơn.")
    def check(values):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("SheetSage2 trả về thời gian không hữu hạn.")
        if any(b - a < 0.045 for a, b in zip(values, values[1:])):
            raise ValueError("SheetSage2 trả về mốc không tăng dần hoặc quá gần nhau.")
        if values[0] < 0 or values[-1] > duration_seconds + 0.05:
            raise ValueError("SheetSage2 trả về mốc ngoài clip.")
    check(beats)
    check(downbeats)
    raw_starts = []
    meters = []
    counts = []
    for bar in raw_bars:
        if not isinstance(bar, dict):
            raise ValueError("Bar map chứa ô không hợp lệ.")
        when, signature, pulses = bar.get("start"), bar.get("meter"), bar.get("beats")
        if not isinstance(when, (float, int)) or not math.isfinite(when):
            raise ValueError("Barline không hợp lệ.")
        if not isinstance(signature, str) or METER_RE.fullmatch(signature) is None:
            raise ValueError("Model không cung cấp số chỉ nhịp được hỗ trợ; không suy đoán mặc định.")
        if type(pulses) is not int or not 1 <= pulses <= 32:
            raise ValueError("Model trả số phách trong ô không hợp lệ.")
        raw_starts.append(float(when))
        meters.append(signature)
        counts.append(pulses)
    check(raw_starts)
    if len(raw_starts) != len(downbeats) or any(abs(a-b) > 0.06 for a,b in zip(raw_starts, downbeats)):
        raise ValueError("Đầu ô model không khớp danh sách phách 1.")
    return [float(v) for v in beats if v < duration_seconds], raw_starts, meters, counts


def prediction_to_rhythm(
    payload: dict, duration_seconds: float, *, manifest: dict | None = None,
    meter_hint: str | None = None,
) -> RhythmAnalysis:
    """Map the meter-aware model to the app's piecewise musical beat grid.

    The original timestamps and labels remain in metadata. A beat marker within
    35 ms of a barline is snapped to the boundary; no audio hit is modified.
    """
    from .rhythm import _default_grouping

    beats, starts, meters, counts = _clean_predictions(payload, duration_seconds)
    raw_beats = list(beats)
    # Estimate only the unobserved end of the last bar, not any detected change.
    last_start = starts[-1]
    final_beats = [value for value in beats if value >= last_start - 1e-6]
    recent = [b - a for a, b in zip(final_beats, final_beats[1:])]
    if not recent:
        recent = [b - a for a, b in zip(beats[-8:], beats[-7:])]
    if not recent:
        raise ValueError("Thiếu beat để ngoại suy mép cuối clip.")
    last_interval = median(recent)
    if not 0.04 <= last_interval <= 3.0:
        raise ValueError("Khoảng beat cuối không đáng tin cậy.")
    end = last_start + max(1, counts[-1]) * last_interval
    while end < duration_seconds - 0.04:
        starts.append(end)
        meters.append(meters[-1])
        counts.append(counts[-1])
        end += max(1, counts[-1]) * last_interval
        if len(starts) > 1024:
            raise ValueError("Bản đồ ô nhịp ngoại suy vượt giới hạn.")
    boundaries = starts + [end]
    if any(b <= a for a, b in zip(boundaries, boundaries[1:])):
        raise ValueError("Độ dài ô nhịp model không hợp lệ.")
    # The model may offset its beat and downbeat heads by a few milliseconds.
    # Prefer the explicit downbeat boundary when they are very close.
    snapped = [
        b for b in beats
        if not any(abs(b - mark) < 0.035 for mark in boundaries)
    ]
    timeline = sorted([*snapped, *boundaries])
    if any(b - a < 0.006 for a, b in zip(timeline, timeline[1:])):
        raise ValueError("Các beat mới gần trùng nhau; cần kiểm tra file hoặc model.")
    bar_starts_quarters = [0.0]
    for signature in meters:
        n, d = map(int, signature.split("/"))
        bar_starts_quarters.append(bar_starts_quarters[-1] + n*4/d)
    measures = []
    for i, signature in enumerate(meters):
        measures.append(RhythmBar(
            start_seconds=boundaries[i], end_seconds=boundaries[i+1],
            start_quarter=bar_starts_quarters[i],
            end_quarter=bar_starts_quarters[i+1],
            meter=signature, grouping=_default_grouping(signature),
            source="sheetsage2_head" if i < len(payload["bars"]) else "extrapolated",
            estimated=True,
        ))
    def position_at(when: float) -> float:
        if when < starts[0]:
            early = [t for t in timeline if starts[0] <= t <= boundaries[1]]
            step_seconds = median([y-x for x,y in zip(early, early[1:])])
            step_q = (bar_starts_quarters[1] - bar_starts_quarters[0])/(len(early)-1)
            return (when - starts[0]) * step_q / step_seconds
        index = min(bisect_right(boundaries, when)-1, len(meters)-1)
        interval_points = [t for t in timeline if boundaries[index] <= t <= boundaries[index+1]]
        spot = interval_points.index(when)
        return bar_starts_quarters[index] + (
            bar_starts_quarters[index+1] - bar_starts_quarters[index]
        ) * spot / (len(interval_points)-1)
    positions = [position_at(t) for t in timeline]
    if any(b <= a for a,b in zip(positions, positions[1:])):
        raise ValueError("Lưới nốt không tăng dần sau khi đổi nhịp.")
    quarter_intervals = [(b-a)/(q-p) for a,b,p,q in zip(
        timeline,timeline[1:],positions,positions[1:]
    )]
    bpm = 60 / median(quarter_intervals)
    if not 20 <= bpm <= 400:
        raise ValueError("BPM sau khi quy đổi meter vượt vùng 20–400.")
    marked_starts = [v for v in starts if v < duration_seconds]
    warnings = [
        "Số chỉ nhịp và ranh giới ô do SheetSage2-Refined ước lượng; "
        "cần nghe kiểm tra, đặc biệt với 5/16 và đoạn chuyển nhịp.",
        "Các nhóm 2+2+3 trên sheet là quy ước ký âm, không phải nhấn phách đã được model xác minh.",
    ]
    if len(starts) > len(payload["bars"]):
        warnings.append("Đã ngoại suy ô cuối để bao phủ phần audio còn lại.")
    if meter_hint and meter_hint != meters[0]:
        warnings.append("Gợi ý số chỉ nhịp đầu đoạn khác kết quả SheetSage2; không tự ép nhãn model.")
    metadata = {
        "source_revision": SOURCE_REVISION,
        "model": "SheetSage2-Refined beat/bar head",
        "model_repo": MODEL_REPO,
        "model_weights_revision": (manifest or {}).get("weights_revision"),
        "weights_sha256": (manifest or {}).get("weights_sha256"),
        "head_sha256": (manifest or {}).get("head_sha256"),
        "meter_source": "model_bar_head_estimate",
        "raw_beats_seconds": raw_beats,
        "raw_downbeats_seconds": payload["downbeats"],
        "raw_bars": payload["bars"],
        "bar_map_requires_review": True,
        "rhythm_review_status": "estimated",
        "rhythm_review_reasons": ["SheetSage2 meter inference has not been validated for this audio."],
        "grouping_source": "notation_default_not_model",
        "virtual_grid_tail_seconds": end if end >= duration_seconds else None,
    }
    return RhythmAnalysis(
        engine="sheetsage2_refined",
        bpm=bpm, meter=meters[0], first_downbeat_seconds=starts[0],
        beats_seconds=timeline, downbeats_seconds=marked_starts,
        beat_positions_quarters=positions, beat_unit_quarters=(
            (bar_starts_quarters[1]-bar_starts_quarters[0]) /
            (len([t for t in timeline if starts[0] <= t <= boundaries[1]])-1)
        ),
        bars=measures, warnings=warnings, metadata=metadata,
    )


def analyze_meter_rhythm(audio_path: Path, models_dir: Path, *,
                         device: str = "auto", meter_hint: str | None = None) -> RhythmAnalysis:
    """Run the optional pretrained head against the local decoded WAV only."""
    status = meter_status(models_dir)
    if not status["ready"]:
        raise EngineUnavailable(status["detail"])
    weights, head, _config, manifest = _artifacts(models_dir)
    # This is deliberately set before importing the upstream library.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        import torch
        from sheetsage2_refined.beats import HeadBeats
    except (ImportError, OSError) as exc:
        raise EngineUnavailable("Không import được SheetSage2-Refined local.") from exc
    selected = select_device(torch, device, allow_mps=False)
    tracker = None
    try:
        # Supplying both local artifacts bypasses the upstream Hub loader.
        tracker = HeadBeats.load(
            weights=weights, heads=[head], device=selected,
        )
        payload = tracker.track(str(audio_path))
        import soundfile as sf
        duration = float(sf.info(audio_path).duration)
        result = prediction_to_rhythm(payload, duration, manifest=manifest, meter_hint=meter_hint)
        result.metadata["device"] = selected
        return result
    finally:
        if tracker is not None:
            del tracker
        if selected == "cuda":
            torch.cuda.empty_cache()
