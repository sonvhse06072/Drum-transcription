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


class Transcription(BaseModel):
    engine: str
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    hits: list[DrumHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoreOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Drum transcription", min_length=1, max_length=120)
    bpm: float = Field(default=120, ge=20, le=400, allow_inf_nan=False)
    meter: str = Field(default="4/4", pattern=r"^(2|3|4|5|6|7|9|12)/(4|8)$")
    subdivisions: Literal[1, 2, 3, 4, 6, 8] = 4
    first_downbeat_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


class JobOptions(ScoreOptions):
    engine: Literal["adtof", "adt_str"] = "adtof"
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    threshold: float = Field(default=0.25, gt=0, lt=1, allow_inf_nan=False)
    clip_start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    clip_duration_seconds: float = Field(default=60, ge=1, le=600, allow_inf_nan=False)
    separate_drums: bool = False

    @model_validator(mode="after")
    def downbeat_inside_clip(self) -> "JobOptions":
        if self.first_downbeat_seconds >= self.clip_duration_seconds:
            raise ValueError("The first downbeat must lie inside the selected clip.")
        return self


class EngineUnavailable(RuntimeError):
    """Required local dependency, model or device is missing."""
