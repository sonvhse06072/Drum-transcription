"""Review the notation of detected bars without changing observed timestamps."""

from bisect import bisect_right
from statistics import median

import numpy as np

from .domain import RhythmAnalysis, RhythmBar, RhythmRescoreOptions


def apply_explicit_barlines(
    analysis: RhythmAnalysis, options: RhythmRescoreOptions
) -> RhythmAnalysis:
    """Apply user-specified bar *times* and signatures, preserving raw AI output.

    This is a manual review revision, NOT improved automatic recognition. The
    source track/DrumHit timestamps and original model events are never altered.
    """
    from .rhythm import _default_grouping

    boundaries = options.barline_seconds
    meters = options.barline_meters
    if boundaries is None or meters is None:
        raise ValueError("Thiếu mốc ô hoặc chuỗi số chỉ nhịp.")
    times = list(analysis.beats_seconds)
    if boundaries[0] >= times[-1] or boundaries[-1] < times[-1] - 0.05:
        raise ValueError(
            "Bản đồ mới phải bắt đầu trong clip và kết thúc sau beat cuối phát hiện. "
            "Kiểm tra các mốc trong clip, không dùng thời gian của file nhạc gốc."
        )
    if boundaries[-1] > analysis.bars[-1].end_seconds + 5 if analysis.bars else False:
        raise ValueError("Ranh giới cuối nằm quá xa vùng audio đã phân tích.")

    # A boundary can be between detected beats (e.g. 5/16). Nearby model
    # markers within 25 ms are replaced, not doubled into implausible tiny beats.
    for boundary in boundaries:
        times = [value for value in times if abs(value - boundary) > 0.025]
        times.append(boundary)
    times.sort()
    if any(b - a <= 1e-6 for a, b in zip(times, times[1:])):
        raise ValueError("Ranh giới trùng nhau sau khi căn thời gian.")

    bar_lengths = []
    starts = [0.0]
    bars = []
    for index, signature in enumerate(meters):
        numerator, denominator = map(int, signature.split("/"))
        length = numerator * 4 / denominator
        bar_lengths.append(length)
        bars.append(
            RhythmBar(
                start_seconds=boundaries[index],
                end_seconds=boundaries[index + 1],
                start_quarter=starts[-1],
                end_quarter=starts[-1] + length,
                meter=signature,
                grouping=_default_grouping(signature),
                source="user_boundary",
                estimated=False,
            )
        )
        starts.append(starts[-1] + length)

    def at_time(when: float) -> float:
        if when < boundaries[0]:
            return (when - boundaries[0]) * bar_lengths[0] / (
                boundaries[1] - boundaries[0]
            )
        if when >= boundaries[-1]:
            return starts[-1] + (when - boundaries[-1]) * bar_lengths[-1] / (
                boundaries[-1] - boundaries[-2]
            )
        index = min(bisect_right(boundaries, when) - 1, len(meters) - 1)
        fraction = (when - boundaries[index]) / (
            boundaries[index + 1] - boundaries[index]
        )
        return starts[index] + fraction * bar_lengths[index]

    positions = [at_time(when) for when in times]
    intervals = [
        (right - left) / (positions[i + 1] - positions[i])
        for i, (left, right) in enumerate(zip(times, times[1:]))
    ]
    bpm = 60 / median(intervals)
    if not 20 <= bpm <= 400:
        raise ValueError("BPM suy ra từ các mốc chỉnh nằm ngoài 20–400.")
    inherited_warnings = analysis.metadata.get("detector_warnings", analysis.warnings)
    warning = (
        "Ranh giới và số chỉ nhịp đã được người dùng nhập để hiệu chỉnh; "
        "đây không phải các ô nhịp AI tự nhận diện chính xác. "
        "Dữ liệu beat/downbeat gốc được lưu riêng."
    )
    history = list(analysis.metadata.get("boundary_edit_history", []))
    history.append({"barline_seconds": boundaries, "barline_meters": meters})
    return RhythmAnalysis.model_validate(
        {
            **analysis.model_dump(),
            "bpm": bpm,
            "meter": meters[0],
            "first_downbeat_seconds": boundaries[0],
            "beats_seconds": times,
            "downbeats_seconds": boundaries[:-1],
            "beat_positions_quarters": positions,
            "beat_unit_quarters": bar_lengths[0] / (
                (boundaries[1] - boundaries[0])
                / median([b - a for a, b in zip(times, times[1:])])
            ),
            "bars": [bar.model_dump() for bar in bars],
            "warnings": list(dict.fromkeys([warning, *inherited_warnings])),
            "metadata": {
                **analysis.metadata,
                "meter_source": "user_barline_review",
                "bar_boundaries_edited": True,
                "boundary_edit_history": history,
                "model_downbeats_before_user_review": analysis.downbeats_seconds,
                "detector_warnings": inherited_warnings,
                "bar_map_requires_review": False,
                "rhythm_review_status": "user_corrected_not_model_verified",
                "confidence_kind": "user_supplied_timing_not_model_probability",
            },
        }
    )


def apply_bar_overrides(analysis: RhythmAnalysis, options: RhythmRescoreOptions) -> RhythmAnalysis:
    """Keep bar/beat times; rescale musical positions where meter length changes.

    Grouping controls engraving. It does not move beat/downbeat detections or
    pretend to repair a missing/spurious downbeat marker.
    """
    if options.barline_seconds is not None:
        return apply_explicit_barlines(analysis, options)
    if not analysis.bars:
        raise ValueError(
            "Kết quả này chưa có bản đồ ô nhịp. Chạy lại nhận diện với phiên bản mới trước khi chỉnh từng ô."
        )
    changes = {item.bar_index: item for item in options.bar_overrides}
    if any(index >= len(analysis.bars) for index in changes):
        raise ValueError("Ô nhịp cần chỉnh nằm ngoài bản đồ nhịp đã nhận diện.")
    if not changes:
        return analysis.model_copy(deep=True)

    bars: list[RhythmBar] = []
    position = 0.0
    for index, original in enumerate(analysis.bars):
        update = changes.get(index)
        chosen_meter = update.meter if update else original.meter
        numerator, denominator = map(int, chosen_meter.split("/"))
        length = numerator * 4 / denominator
        bars.append(
            RhythmBar(
                start_seconds=original.start_seconds,
                end_seconds=original.end_seconds,
                start_quarter=position,
                end_quarter=position + length,
                meter=chosen_meter,
                grouping=update.grouping if update else original.grouping,
                source="user" if update else original.source,
                estimated=False if update else original.estimated,
            )
        )
        position += length

    boundaries = [bar.end_quarter for bar in analysis.bars]
    positions = []
    for old_position in analysis.beat_positions_quarters:
        index = min(bisect_right(boundaries, old_position), len(bars) - 1)
        previous, current = analysis.bars[index], bars[index]
        fraction = (old_position - previous.start_quarter) / (
            previous.end_quarter - previous.start_quarter
        )
        positions.append(
            current.start_quarter + fraction * (current.end_quarter - current.start_quarter)
        )
    steps = [right - left for left, right in zip(positions, positions[1:])]
    intervals = [
        right - left for left, right in zip(analysis.beats_seconds, analysis.beats_seconds[1:])
    ]
    quarter_intervals = [interval / step for step, interval in zip(steps, intervals)]
    bpm = 60 / median(quarter_intervals)
    if not 20 <= bpm <= 400:
        raise ValueError(
            "Cách ghi nhịp này làm tempo trung vị vượt 20–400 nốt đen/phút. Kiểm tra lại số chỉ nhịp."
        )
    detector_warnings = analysis.metadata.get("detector_warnings", analysis.warnings)
    warnings = [f"Nhận diện ban đầu: {warning}" for warning in detector_warnings]
    warnings.append(
        "Đã chỉnh số chỉ nhịp/nhóm phách theo từng ô. Mốc thời gian beat và phách 1 vẫn giữ nguyên; nhóm phách chỉ thay cách nối móc trên sheet."
    )
    history = list(analysis.metadata.get("meter_edit_history", []))
    history.append([item.model_dump() for item in options.bar_overrides])
    meter_changes = [
        {
            "bar_index": index,
            "start_seconds": bar.start_seconds,
            "meter": bar.meter,
            "grouping": bar.grouping,
            "estimated": bar.estimated,
        }
        for index, bar in enumerate(bars)
        if index and (bar.meter, bar.grouping) != (bars[index - 1].meter, bars[index - 1].grouping)
    ]
    p10, p90 = np.percentile(quarter_intervals, [10, 90])
    return RhythmAnalysis.model_validate(
        {
            **analysis.model_dump(),
            "bpm": bpm,
            "meter": bars[0].meter,
            "bars": [bar.model_dump() for bar in bars],
            "beat_positions_quarters": positions,
            "beat_unit_quarters": median(steps),
            "warnings": list(dict.fromkeys(warnings)),
            "metadata": {
                **analysis.metadata,
                "meter_source": "reviewed_bar_map",
                "bpm_kind": "inverse_median_seconds_per_quarter",
                "meter_edit_history": history,
                "meter_changes": meter_changes,
                "meter_change_count": len(meter_changes),
                "bar_map_requires_review": any(bar.estimated for bar in bars),
                "quarter_interval_p10_seconds": float(p10),
                "quarter_interval_p90_seconds": float(p90),
                "unusual_beat_intervals": sum(
                    not 20 <= 60 / interval <= 400 for interval in quarter_intervals
                ),
                "detector_warnings": detector_warnings,
                "bar_boundaries_edited": False,
            },
        }
    )
