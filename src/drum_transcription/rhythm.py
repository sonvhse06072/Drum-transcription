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

from .domain import METER_PATTERN, EngineUnavailable, RhythmAnalysis, RhythmBar
from .engines.common import require_file, select_device
from .meter_decoder import decode_barlines

BEAT_THIS_REVISION = "b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c"
CHECKPOINT_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"
METER_RE = re.compile(METER_PATTERN)


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


def _default_grouping(meter: str) -> list[int]:
    numerator, denominator = map(int, meter.split("/"))
    if denominator in (8, 16) and numerator >= 3:
        if numerator % 3 == 0:
            return [3] * (numerator // 3)
        if numerator % 2 == 0:
            return [2] * (numerator // 2)
        return [2] * ((numerator - 3) // 2) + [3]
    return [1] * numerator


def _additive_pattern(intervals: np.ndarray) -> tuple[int, ...] | None:
    """Suggest long/short pulse groups; repetition or a compatible hint must confirm it.

    Duration ratios alone cannot distinguish a single tempo change from odd meter.
    Limit this heuristic to the common groups of two/three subdivisions, with a
    tight fit, instead of rationalizing arbitrary expressive timing into a meter.
    """
    if len(intervals) < 2 or len(intervals) > 16:
        return None
    groups = np.rint(intervals / float(np.min(intervals)) * 2).astype(int)
    if not set(groups) == {2, 3} or int(np.sum(groups)) > 32:
        return None
    unit = float(np.median(intervals / groups))
    errors = abs(intervals - groups * unit) / (groups * unit)
    if float(np.max(errors)) > 0.08 or float(np.sqrt(np.mean(errors**2))) > 0.05:
        return None
    return tuple(int(value) for value in groups)


def _standard_pulse(value: float) -> bool:
    return any(
        math.isclose(value, unit, abs_tol=1e-9)
        for unit in (0.125, 0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64)
    )


def _meter_for_pulses(count: int, pulse: float, preferred_denominator: int | None = None) -> str:
    # Denominators are notation suggestions, not a quantity directly predicted
    # by Beat This!. Retain an initial user interpretation when it still fits.
    preferred = 8 if math.isclose(pulse, 1.5) else 4
    if pulse < 1:
        preferred = round(4 / pulse)
    elif math.isclose(pulse, 2):
        preferred = 2
    for denominator in dict.fromkeys([preferred_denominator, preferred, 4, 8, 16, 2]):
        if denominator not in (2, 4, 8, 16):
            continue
        numerator = count * pulse * denominator / 4
        if math.isclose(numerator, round(numerator), abs_tol=1e-9):
            meter = f"{round(numerator)}/{denominator}"
            if METER_RE.fullmatch(meter):
                return meter
    raise ValueError(
        "Khoảng giữa hai phách 1 quá dài hoặc không khớp một ô nhịp được hỗ trợ; cần kiểm tra marker beat/downbeat."
    )


def _time_at_quarter(quarter: float, beats: np.ndarray, positions: np.ndarray) -> float:
    index = int(np.searchsorted(positions, quarter))
    left = max(0, min(index - 1, len(positions) - 2))
    ratio = (quarter - positions[left]) / (positions[left + 1] - positions[left])
    return float(beats[left] + ratio * (beats[left + 1] - beats[left]))


def analysis_from_beats(
    beats,
    downbeats,
    *,
    duration_seconds: float,
    meter_hint: str | None = None,
    downbeat_logits=None,
    downbeat_fps: int = 50,
) -> RhythmAnalysis:
    """Keep local bar structure and timestamped tempo, with explicit uncertainty.

    Beat This! supplies timestamps, not a time-signature classifier. Every
    inferred signature/grouping is therefore reviewable and marked estimated.
    A meter hint anchors only the initial section's pulse unit, not the length
    of all subsequent bars.
    """
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
    discarded_tail_beats = int(np.sum(beats >= duration_seconds))
    discarded_tail_downbeats = int(np.sum(downbeats >= duration_seconds))
    beats = beats[beats < duration_seconds]
    downbeats = downbeats[downbeats < duration_seconds]
    if len(beats) < 4 or len(downbeats) < 2:
        raise ValueError(
            "Chưa phát hiện đủ beat và ít nhất hai phách 1 để xác định tempo/ô nhịp. Thử đoạn 20–60 giây có nhịp rõ, hoặc dùng chế độ chỉnh thủ công."
        )
    if meter_hint is not None and not METER_RE.fullmatch(meter_hint):
        raise ValueError("Số chỉ nhịp gợi ý không được hỗ trợ.")
    downbeat_indices = np.array([int(np.argmin(abs(beats - value))) for value in downbeats])
    if np.any(abs(beats[downbeat_indices] - downbeats) > 0.06) or np.any(
        np.diff(downbeat_indices) <= 0
    ):
        raise ValueError("Beat và phách 1 không khớp; không tự lấy cú đánh đầu làm phách 1.")
    raw_downbeat_indices = downbeat_indices.copy()
    decode_result = None
    if downbeat_logits is not None:
        decode_result = decode_barlines(
            beats, raw_downbeat_indices, np.asarray(downbeat_logits).reshape(-1),
            fps=downbeat_fps,
        )
        downbeat_indices = np.asarray(decode_result.indices, dtype=int)
    aligned_downbeats = beats[downbeat_indices]
    counts = np.diff(downbeat_indices)
    frequencies = Counter(int(value) for value in counts)
    warnings = []
    if decode_result is not None and decode_result.skipped_raw_markers:
        warnings.append(
            f"Bộ giải mã đã bỏ {decode_result.skipped_raw_markers} phách 1 nghi thừa "
            f"và thêm {decode_result.inserted_markers} ranh giới ứng viên. "
            "Đây là ước lượng từ điểm model, không phải xác nhận nhịp chính xác; "
            "hãy nghe click và kiểm tra từng ô."
        )
    if decode_result is not None and decode_result.mode == "raw_excessive_correction":
        warnings.append(
            "Điểm downbeat gợi ý thay đổi quá nhiều ranh giới. Đã giữ dự đoán gốc "
            "để không âm thầm viết lại ô nhịp; cần kiểm tra thủ công."
        )
    if discarded_tail_beats or discarded_tail_downbeats:
        warnings.append(
            "Đã bỏ marker beat/downbeat trong frame đệm ở mép cuối clip; không có mẫu audio tương ứng để phát."
        )

    raw_downbeat_indices_set = set(int(i) for i in raw_downbeat_indices)
    # Only an isolated multiple bracketed by the same count is tentatively split.
    # Sustained regimes (e.g. 2,2,2,4,4,4) must not be flattened with a global GCD:
    # they can be a real meter change or a changed model interpretation.
    spans: list[tuple[int, int, str]] = []
    missing_downbeats = 0
    for index, (start, end) in enumerate(zip(downbeat_indices, downbeat_indices[1:])):
        count = int(end - start)
        baseline = int(counts[index - 1]) if index > 0 else 0
        split = (
            0 < index < len(counts) - 1
            and baseline == int(counts[index + 1])
            and baseline >= 2
            and count > baseline
            and count % baseline == 0
        )
        if split:
            missing_downbeats += count // baseline - 1
            spans.extend(
                (left, left + baseline, "interpolated")
                for left in range(int(start), int(end), baseline)
            )
        else:
            spans.append((int(start), int(end), "detected"))
    if missing_downbeats:
        warnings.append(
            f"Có {missing_downbeats} đầu ô được nội suy tại khoảng dài đơn lẻ giữa các ô cùng nhịp. Đây có thể là downbeat bị bỏ sót hoặc một ô đổi nhịp thật; cần nghe kiểm tra."
        )

    patterns = [
        _additive_pattern(np.diff(beats[start : end + 1])) for start, end, _ in spans
    ]
    repeated = [
        pattern is not None
        and any(
            pattern == patterns[other]
            for other in range(max(0, index - 4), min(len(patterns), index + 5))
            if other != index
        )
        for index, pattern in enumerate(patterns)
    ]
    first_count = spans[0][1] - spans[0][0]
    first_pattern = patterns[0]
    use_first_pattern = repeated[0]
    initial_meter = meter_hint
    initial_units: list[float]
    grouped_unit = 0.5
    if meter_hint is not None:
        numerator, denominator = map(int, meter_hint.split("/"))
        first_length = numerator * 4 / denominator
        # A hint may disambiguate one short grouped bar, but cannot stretch
        # three equal pulses into 4/4 by inventing 4/3-quarter beats.
        pattern_unit = first_length / sum(first_pattern) if first_pattern else 0
        hint_accepts_pattern = first_pattern is not None and _standard_pulse(pattern_unit)
        use_first_pattern = hint_accepts_pattern
        if hint_accepts_pattern:
            initial_units = [value * pattern_unit for value in first_pattern]
            grouped_unit = pattern_unit
        else:
            pulse = first_length / first_count
            if not _standard_pulse(pulse):
                raise ValueError(
                    "Số chỉ nhịp gợi ý không khớp cách nhóm beat đầu đoạn. Bỏ gợi ý hoặc sửa từng ô nhịp sau khi phân tích."
                )
            initial_units = [pulse] * first_count
    elif use_first_pattern:
        initial_meter = f"{sum(first_pattern)}/8"
        initial_units = [value * 0.5 for value in first_pattern]
    else:
        initial_meter = _meter_for_pulses(first_count, 1)
        initial_units = [1.0] * first_count
    equal_pulse_unit = min(initial_units)
    if meter_hint is None:
        warnings.append(
            "Số chỉ nhịp là gợi ý từ beat/downbeat, chưa được xác nhận. Các pulse đều không phân biệt chắc chắn n/4 với n/8; half/double-time và nhịp kép cần nghe đối chiếu."
        )
    else:
        warnings.append(
            "Số chỉ nhịp gợi ý chỉ neo cách đếm ở đầu đoạn; các ô sau vẫn có thể đổi nhịp và cần kiểm tra."
        )

    positions = np.empty(len(beats), dtype=float)
    anchor_index = int(downbeat_indices[0])
    positions[anchor_index] = 0
    for index in range(anchor_index - 1, -1, -1):
        unit = initial_units[(index - anchor_index) % len(initial_units)]
        positions[index] = positions[index + 1] - unit
    bars: list[RhythmBar] = []
    last_units = initial_units
    last_meter = initial_meter
    last_grouping = _default_grouping(initial_meter)
    for index, (start, end, source) in enumerate(spans):
        pattern = patterns[index]
        if index == 0:
            meter = initial_meter
            units = initial_units
            use_pattern = use_first_pattern
        elif repeated[index]:
            meter = (
                initial_meter
                if use_first_pattern and pattern == first_pattern
                else _meter_for_pulses(
                    sum(pattern), grouped_unit,
                    int(initial_meter.split("/")[1]) if use_first_pattern else None,
                )
            )
            units = [value * grouped_unit for value in pattern]
            use_pattern = True
        else:
            meter = _meter_for_pulses(
                end - start, equal_pulse_unit,
                int(initial_meter.split("/")[1]) if not use_first_pattern else None,
            )
            units = [equal_pulse_unit] * (end - start)
            use_pattern = False
        grouping = _default_grouping(meter)
        if use_pattern:
            numerator = int(meter.split("/")[0])
            scale = numerator / sum(pattern)
            values = [value * scale for value in pattern]
            if all(math.isclose(value, round(value), abs_tol=1e-9) for value in values):
                grouping = [round(value) for value in values]
        for offset, unit in enumerate(units):
            positions[start + offset + 1] = positions[start + offset] + unit
        bar_source = source
        if source == "detected" and int(start) not in raw_downbeat_indices_set:
            bar_source = "estimated_barline"
        if bar_source == "detected" and use_pattern:
            bar_source = "detected_grouping"
        if index == 0 and meter_hint is not None:
            bar_source = "user_hint"
        bars.append(
            RhythmBar(
                start_seconds=float(beats[start]),
                end_seconds=float(beats[end]),
                start_quarter=float(positions[start]),
                end_quarter=float(positions[end]),
                meter=meter,
                grouping=grouping,
                source=bar_source,
                estimated=True,
            )
        )
        last_units, last_meter, last_grouping = units, meter, grouping

    # Keep all real beat positions after the final detected downbeat. Extend the
    # last interpretation only to finish nominal notation bars; mark these as
    # extrapolated and retain the raw downbeat list unchanged.
    last_anchor = int(downbeat_indices[-1])
    for index in range(last_anchor, len(beats) - 1):
        positions[index + 1] = positions[index] + last_units[
            (index - last_anchor) % len(last_units)
        ]
    start_quarter = float(positions[last_anchor])
    start_seconds = float(beats[last_anchor])
    bar_length = sum(last_units)
    while start_seconds < duration_seconds - 1e-9:
        end_quarter = start_quarter + bar_length
        end_seconds = _time_at_quarter(end_quarter, beats, positions)
        bars.append(
            RhythmBar(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                start_quarter=start_quarter,
                end_quarter=end_quarter,
                meter=last_meter,
                grouping=last_grouping,
                source="extrapolated",
                estimated=True,
            )
        )
        start_seconds, start_quarter = end_seconds, end_quarter

    intervals = np.diff(beats)
    quarter_intervals = intervals / np.diff(positions)
    bpm = 60 / float(np.median(quarter_intervals))
    if not 20 <= bpm <= 400:
        raise ValueError(
            "Tempo suy ra nằm ngoài 20–400 nốt đen/phút; kiểm tra số chỉ nhịp hoặc dùng clip khác."
        )
    p10, p90 = np.percentile(intervals, [10, 90])
    quarter_p10, quarter_p90 = np.percentile(quarter_intervals, [10, 90])
    if quarter_p90 / quarter_p10 > 1.12:
        warnings.append(
            "Tempo thay đổi theo lưới beat đã phát hiện. BPM tổng quan là giá trị đại diện; MIDI ký âm và MusicXML giữ bản đồ tempo theo thời gian."
        )
    unusual_intervals = int(np.sum((60 / quarter_intervals < 20) | (60 / quarter_intervals > 400)))
    if unusual_intervals:
        warnings.append(
            f"Có {unusual_intervals} khoảng beat gợi tempo ngoài 20–400 BPM; có thể thiếu/thừa marker, cần nghe kiểm tra."
        )
    changes = [
        {
            "bar_index": index,
            "start_seconds": bar.start_seconds,
            "meter": bar.meter,
            "grouping": bar.grouping,
            "estimated": True,
        }
        for index, bar in enumerate(bars)
        if index > 0
        and (bar.meter, bar.grouping) != (bars[index - 1].meter, bars[index - 1].grouping)
    ]
    if changes:
        warnings.append(
            f"Có {len(changes)} vị trí đổi số chỉ nhịp/nhóm phách được gợi ý. Đây cũng có thể là model đổi cách đếm hoặc bỏ sót downbeat; kiểm tra bản đồ từng ô trước khi chốt sheet."
        )
    short_bar_intervals = int(np.sum(counts == 1))
    frequent_changes = len(changes) > max(2, len(spans) // 3)
    review_reasons = []
    if short_bar_intervals >= 3:
        review_reasons.append("many_single_beat_intervals")
    if frequent_changes:
        review_reasons.append("frequent_meter_or_group_changes")
    if decode_result is not None and decode_result.skipped_raw_markers >= 3:
        review_reasons.append("downbeat_model_disagreement")
    readable_review_reasons = []
    if short_bar_intervals >= 3:
        readable_review_reasons.append(
            f"Có {short_bar_intervals} khoảng giữa downbeat chỉ chứa một beat; có thể có marker thừa."
        )
    if frequent_changes:
        readable_review_reasons.append(
            f"Có {len(changes)} lần đổi số chỉ nhịp hoặc nhóm phách trong {len(spans)} ô quan sát; cần phân biệt nhịp phức tạp với cách đếm của model bị thay đổi."
        )
    if review_reasons:
        warnings.append(
            f"Lưới có {short_bar_intervals} khoảng downbeat chỉ chứa 1 beat và {len(changes)} lần đổi nhịp/nhóm phách. Đây có thể là đoạn nhịp phức tạp hoặc marker model sai; không coi bản đồ này là kết quả nhận diện đã xác nhận."
        )
    if any(repeated):
        warnings.append(
            "Nhóm phách ngắn/dài được gợi ý từ tỷ lệ thời gian lặp lại (nhóm 2 hoặc 3). Cách viết nhịp lẻ vẫn cần nghe xác nhận."
        )
    if len(downbeats) == 2:
        warnings.append(
            "Chỉ có một khoảng ô nhịp đầy đủ để suy ra số chỉ nhịp; nghe lớp click và thử đoạn dài hơn nếu kết quả chưa đúng."
        )
    return RhythmAnalysis(
        engine="beat_this",
        bpm=float(bpm),
        meter=initial_meter,
        first_downbeat_seconds=float(aligned_downbeats[0]),
        beats_seconds=beats.tolist(),
        downbeats_seconds=aligned_downbeats.tolist(),
        beat_positions_quarters=positions.tolist(),
        beat_unit_quarters=float(initial_units[0]),
        bars=bars,
        warnings=warnings,
        metadata={
            "source_revision": BEAT_THIS_REVISION,
            "model": "final0",
            "postprocessing": "local_bar_map_viterbi_no_dbn" if decode_result else "local_bar_map_no_dbn",
            "barline_decoder_mode": decode_result.mode if decode_result else "raw_peaks",
            "barline_filtered_count": decode_result.skipped_raw_markers if decode_result else 0,
            "barline_inserted_count": decode_result.inserted_markers if decode_result else 0,
            "barline_raw_marker_count": len(raw_downbeat_indices),
            "barline_selected_marker_count": len(downbeat_indices),
            "raw_downbeat_indices": raw_downbeat_indices.tolist(),
            "time_resolution_seconds": 0.02,
            "time_reference": "decoded_clip_start",
            "bpm_kind": "inverse_median_seconds_per_quarter",
            "meter_source": "user_hint" if meter_hint else "detected_beat_group_suggestion",
            "meter_hint_scope": "initial_section" if meter_hint else None,
            "beats_per_bar": first_count,
            "observed_bar_intervals": len(counts),
            "single_bar_interval_support": frequencies[first_count],
            "bar_interval_beat_counts": counts.tolist(),
            "missing_downbeat_markers": missing_downbeats,
            "meter_changes": changes,
            "meter_change_count": len(changes),
            "bar_map_requires_review": True,
            "bar_map_review_reasons": review_reasons,
            "rhythm_review_status": "unstable" if review_reasons else "estimated",
            "rhythm_review_reasons": readable_review_reasons,
            "rhythm_review_criteria": {
                "single_beat_bar_count_threshold": 3,
                "meter_change_count_exclusive_threshold": max(2, len(spans) // 3),
                "meter_change_density": len(changes) / len(spans),
            },
            "single_beat_bar_intervals": short_bar_intervals,
            "additive_grouping_heuristic": "repeated_two_three_duration_ratio",
            "beat_interval_p10_seconds": float(p10),
            "beat_interval_p90_seconds": float(p90),
            "quarter_interval_p10_seconds": float(quarter_p10),
            "quarter_interval_p90_seconds": float(quarter_p90),
            "unusual_beat_intervals": unusual_intervals,
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
        # Keep the same model forward pass for logits and detected timestamps.
        # The raw peak list alone loses the evidence needed to reject spurious
        # downbeats in mixed meter. Older test adapters keep the original API.
        downbeat_logits = None
        with torch.inference_mode():
            if all(
                hasattr(tracker, name)
                for name in ("signal2spect", "spect2frames", "frames2beats")
            ):
                spectrogram = tracker.signal2spect(waveform, rate)
                beat_frames, downbeat_frames = tracker.spect2frames(spectrogram)
                beats, downbeats = tracker.frames2beats(beat_frames, downbeat_frames)
                downbeat_logits = downbeat_frames.detach().float().cpu().numpy()
            else:
                beats, downbeats = tracker(waveform, rate)
        result = analysis_from_beats(
            beats, downbeats, duration_seconds=len(waveform) / rate,
            meter_hint=meter_hint, downbeat_logits=downbeat_logits,
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
