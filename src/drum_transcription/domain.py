"""Shared contracts. Raw event timing is never changed by score quantization."""

import math
from bisect import bisect_right
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

METER_PATTERN = r"^([1-9]|[12][0-9]|3[0-2])/(2|4|8|16)$"


class RhythmBar(BaseModel):
    """A complete nominal bar; an extrapolated last boundary can exceed the clip."""

    model_config = ConfigDict(extra="forbid")

    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    start_quarter: float = Field(ge=0, allow_inf_nan=False)
    end_quarter: float = Field(gt=0, allow_inf_nan=False)
    meter: str = Field(pattern=METER_PATTERN)
    grouping: list[int] = Field(default_factory=list)
    source: str = "detected"
    estimated: bool = True

    @model_validator(mode="after")
    def valid_bar(self) -> "RhythmBar":
        numerator, denominator = map(int, self.meter.split("/"))
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Bar end must follow its start.")
        if not math.isclose(
            self.end_quarter - self.start_quarter,
            numerator * 4 / denominator,
            abs_tol=1e-6,
        ):
            raise ValueError("Bar length must match its time signature.")
        if self.grouping and (
            any(value <= 0 for value in self.grouping) or sum(self.grouping) != numerator
        ):
            raise ValueError("Beat groups must be positive and add up to the meter numerator.")
        return self


class BarOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bar_index: int = Field(ge=0)
    meter: str = Field(pattern=METER_PATTERN)
    grouping: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_grouping(self) -> "BarOverride":
        numerator = int(self.meter.split("/")[0])
        if self.grouping and (
            any(value <= 0 for value in self.grouping) or sum(self.grouping) != numerator
        ):
            raise ValueError("Nhóm phách phải là các số dương có tổng bằng tử số của nhịp.")
        return self


class RhythmRescoreOptions(BaseModel):
    """Review meter/grouping while retaining detected timestamps and raw drum events."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Drum transcription", min_length=1, max_length=120)
    subdivisions: Literal[1, 2, 3, 4, 6, 8] = 4
    bar_overrides: list[BarOverride] = Field(default_factory=list, max_length=4096)
    barline_seconds: list[float] | None = Field(default=None, max_length=1025)
    barline_meters: list[str] | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def unique_bars(self) -> "RhythmRescoreOptions":
        indices = [item.bar_index for item in self.bar_overrides]
        if len(indices) != len(set(indices)):
            raise ValueError("Mỗi ô nhịp chỉ được xuất hiện một lần trong phần chỉnh sửa.")
        boundary_mode = self.barline_seconds is not None or self.barline_meters is not None
        if boundary_mode:
            boundaries = self.barline_seconds
            meters = self.barline_meters
            if boundaries is None or meters is None or len(boundaries) != len(meters) + 1:
                raise ValueError("Cần N+1 mốc ranh giới cho N ô nhịp và số chỉ nhịp.")
            if len(meters) < 1 or self.bar_overrides:
                raise ValueError("Không trộn chỉnh ranh giới với chỉnh nhãn ô hiện có.")
            if any(not math.isfinite(value) or value < 0 for value in boundaries):
                raise ValueError("Các mốc ô nhịp phải là số giây không âm và hữu hạn.")
            if any(b - a < 0.05 for a, b in zip(boundaries, boundaries[1:])):
                raise ValueError("Các mốc đầu ô phải tăng dần và cách nhau ít nhất 50 ms.")
            import re
            if any(re.fullmatch(METER_PATTERN, signature) is None for signature in meters):
                raise ValueError("Số chỉ nhịp của từng ô không hợp lệ.")
        return self


class DrumHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_seconds: float = Field(ge=0, allow_inf_nan=False)
    instrument: str
    velocity: int | None = Field(default=None, ge=1, le=127)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    articulations: list[str] = Field(default_factory=list)


class RhythmAnalysis(BaseModel):
    """Detected timing, relative to the decoded clip; quarter zero is its first downbeat."""

    engine: str = "beat_this"
    bpm: float = Field(ge=20, le=400, allow_inf_nan=False)
    meter: str = Field(pattern=METER_PATTERN)
    first_downbeat_seconds: float = Field(ge=0, allow_inf_nan=False)
    beats_seconds: list[float]
    downbeats_seconds: list[float]
    beat_positions_quarters: list[float]
    beat_unit_quarters: float = Field(default=1, gt=0, le=64, allow_inf_nan=False)
    bars: list[RhythmBar] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_grid(self) -> "RhythmAnalysis":
        if len(self.beats_seconds) < 2 or len(self.beats_seconds) != len(
            self.beat_positions_quarters
        ):
            raise ValueError("Rhythm grid needs matching beat times and musical positions.")
        for values in (self.beats_seconds, self.downbeats_seconds, self.beat_positions_quarters):
            if not values or not all(math.isfinite(value) for value in values):
                raise ValueError("Rhythm grid contains missing or non-finite values.")
            if any(right <= left for left, right in zip(values, values[1:])):
                raise ValueError("Rhythm grid must increase strictly.")
        if self.beats_seconds[0] < 0 or self.downbeats_seconds[0] < 0:
            raise ValueError("Detected times cannot precede the audio clip.")
        if abs(self.first_downbeat_seconds - self.downbeats_seconds[0]) > 1e-6:
            raise ValueError("The first downbeat must match the detected downbeat list.")

        def quarter_at_time(when: float) -> float:
            index = min(
                max(bisect_right(self.beats_seconds, when) - 1, 0), len(self.beats_seconds) - 2
            )
            proportion = (when - self.beats_seconds[index]) / (
                self.beats_seconds[index + 1] - self.beats_seconds[index]
            )
            return self.beat_positions_quarters[index] + proportion * (
                self.beat_positions_quarters[index + 1] - self.beat_positions_quarters[index]
            )

        if abs(quarter_at_time(self.first_downbeat_seconds)) > 1e-5:
            raise ValueError("The first downbeat must map to musical quarter zero.")
        if self.bars:
            first = self.bars[0]
            if (
                abs(first.start_quarter) > 1e-6
                or abs(first.start_seconds - self.first_downbeat_seconds) > 1e-6
                or first.meter != self.meter
            ):
                raise ValueError(
                    "The bar map must begin at the first downbeat with the initial meter."
                )
            for previous, following in zip(self.bars, self.bars[1:]):
                if (
                    abs(previous.end_quarter - following.start_quarter) > 1e-6
                    or abs(previous.end_seconds - following.start_seconds) > 1e-6
                ):
                    raise ValueError("The bar map must be continuous in time and musical position.")
            for bar in self.bars:
                if (
                    abs(quarter_at_time(bar.start_seconds) - bar.start_quarter) > 1e-5
                    or abs(quarter_at_time(bar.end_seconds) - bar.end_quarter) > 1e-5
                ):
                    raise ValueError("Bar boundaries must match the detected beat grid.")
        return self


class Transcription(BaseModel):
    engine: str
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    hits: list[DrumHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    rhythm: RhythmAnalysis | None = None


class ScoreOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Drum transcription", min_length=1, max_length=120)
    bpm: float = Field(default=120, ge=20, le=400, allow_inf_nan=False)
    meter: str = Field(default="4/4", pattern=METER_PATTERN)
    subdivisions: Literal[1, 2, 3, 4, 6, 8] = 4
    first_downbeat_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


class JobOptions(ScoreOptions):
    rhythm_mode: Literal["auto", "manual"] = "auto"
    bpm: float | None = Field(default=None, ge=20, le=400, allow_inf_nan=False)
    meter: str | None = Field(default=None, pattern=METER_PATTERN)
    first_downbeat_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    engine: Literal["adtof", "adt_str"] = "adtof"
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    threshold: float = Field(default=0.25, gt=0, lt=1, allow_inf_nan=False)
    clip_start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    clip_duration_seconds: float = Field(default=60, ge=1, le=600, allow_inf_nan=False)
    separate_drums: bool = False

    @model_validator(mode="after")
    def downbeat_inside_clip(self) -> "JobOptions":
        if self.rhythm_mode == "auto" and (
            self.bpm is not None or self.first_downbeat_seconds is not None
        ):
            raise ValueError(
                "Automatic timing detects BPM and downbeat. Use manual mode to override them."
            )
        if self.rhythm_mode == "manual" and any(
            value is None for value in (self.bpm, self.meter, self.first_downbeat_seconds)
        ):
            raise ValueError("Manual timing requires BPM, meter and the first downbeat.")
        if (
            self.first_downbeat_seconds is not None
            and self.first_downbeat_seconds >= self.clip_duration_seconds
        ):
            raise ValueError("The first downbeat must lie inside the selected clip.")
        return self


class EngineUnavailable(RuntimeError):
    """Required local dependency, model or device is missing."""
