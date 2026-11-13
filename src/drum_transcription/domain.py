"""Shared contracts. Raw event timing is never changed by score quantization."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    meter: str = Field(pattern=r"^(2|3|4|5|6|7|9|12)/(4|8)$")
    first_downbeat_seconds: float = Field(ge=0, allow_inf_nan=False)
    beats_seconds: list[float]
    downbeats_seconds: list[float]
    beat_positions_quarters: list[float]
    beat_unit_quarters: float = Field(default=1, gt=0, le=6, allow_inf_nan=False)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_grid(self) -> "RhythmAnalysis":
        import math

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
    meter: str = Field(default="4/4", pattern=r"^(2|3|4|5|6|7|9|12)/(4|8)$")
    subdivisions: Literal[1, 2, 3, 4, 6, 8] = 4
    first_downbeat_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


class JobOptions(ScoreOptions):
    rhythm_mode: Literal["auto", "manual"] = "auto"
    bpm: float | None = Field(default=None, ge=20, le=400, allow_inf_nan=False)
    meter: str | None = Field(default=None, pattern=r"^(2|3|4|5|6|7|9|12)/(4|8)$")
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
