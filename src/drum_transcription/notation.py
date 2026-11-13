"""Deterministic drum engraving; recognition and musical interpretation stay separate.

Detected beat times or an explicit manual grid determine note positions. Local
meter and tempo maps drive the score; raw MIDI retains the original event times.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree as ET

import mido
from music21 import (
    articulations,
    bar,
    clef,
    duration,
    expressions,
    instrument,
    layout,
    metadata,
    meter,
    note,
    percussion,
    stream,
    tempo,
)

from .domain import DrumHit, RhythmAnalysis, ScoreOptions, Transcription
from .preview_audio import write_preview_audio


@dataclass(frozen=True)
class InstrumentSpec:
    midi: int
    display: str
    head: str
    voice: int
    label: str


# Display names describe placement on a five-line percussion staff, using
# treble-staff coordinates solely as an engraving convention, not pitched notes.
INSTRUMENTS: dict[str, InstrumentSpec] = {
    "kick": InstrumentSpec(36, "F4", "normal", 2, "Bass drum"),
    "snare": InstrumentSpec(38, "C5", "normal", 1, "Snare"),
    "side_stick": InstrumentSpec(37, "C5", "x", 1, "Side stick"),
    "clap": InstrumentSpec(39, "C5", "diamond", 1, "Hand clap"),
    "tom_low": InstrumentSpec(43, "G4", "normal", 1, "Low tom"),
    "tom_mid": InstrumentSpec(47, "D5", "normal", 1, "Mid tom"),
    "tom_high": InstrumentSpec(50, "E5", "normal", 1, "High tom"),
    "hihat_closed": InstrumentSpec(42, "G5", "x", 1, "Closed hi-hat"),
    "hihat_open": InstrumentSpec(46, "G5", "x", 1, "Open hi-hat"),
    "hihat_pedal": InstrumentSpec(44, "D4", "x", 2, "Pedal hi-hat"),
    "crash": InstrumentSpec(49, "A5", "x", 1, "Crash"),
    "ride": InstrumentSpec(51, "F5", "x", 1, "Ride"),
    "ride_bell": InstrumentSpec(53, "F5", "diamond", 1, "Ride bell"),
    "china": InstrumentSpec(52, "B5", "x", 1, "China"),
    "splash": InstrumentSpec(55, "E5", "x", 1, "Splash"),
    "cowbell": InstrumentSpec(56, "E5", "triangle", 1, "Cowbell"),
    "tambourine": InstrumentSpec(54, "D5", "x", 1, "Tambourine"),
    "vibraslap": InstrumentSpec(58, "B4", "diamond", 1, "Vibraslap"),
    "conga": InstrumentSpec(64, "B4", "normal", 1, "Grouped hand drums"),
    "shaker": InstrumentSpec(70, "D5", "diamond", 1, "Shaker"),
    "whistle": InstrumentSpec(71, "B5", "diamond", 1, "Whistle"),
    "guiro": InstrumentSpec(73, "A4", "diamond", 1, "Guiro"),
    "claves": InstrumentSpec(75, "A4", "x", 1, "Claves"),
    "cuica": InstrumentSpec(78, "G4", "diamond", 1, "Cuica"),
    "triangle": InstrumentSpec(81, "F5", "triangle", 1, "Triangle"),
}


@dataclass(frozen=True)
class QuantizedHit:
    quarter: Fraction
    hit: DrumHit
    error_seconds: float


@dataclass(frozen=True)
class MeasurePlan:
    """A written measure; ``nominal_start`` precedes ``start`` for a pickup."""

    start: Fraction
    end: Fraction
    nominal_start: Fraction
    meter: str
    grouping: tuple[int, ...] = ()


@dataclass(frozen=True)
class TempoPoint:
    quarter: float
    microseconds: int
    display_bpm: float | None = None


def _interpolate(value: float, source: list[float], target: list[float]) -> float:
    """Piecewise linear interpolation, using the nearest interval at either edge."""
    index = min(max(bisect_right(source, value) - 1, 0), len(source) - 2)
    position = (value - source[index]) / (source[index + 1] - source[index])
    return target[index] + position * (target[index + 1] - target[index])


def _time_to_quarter(when: float, options: ScoreOptions, rhythm: RhythmAnalysis | None) -> float:
    if rhythm is not None:
        return _interpolate(when, rhythm.beats_seconds, rhythm.beat_positions_quarters)
    return (when - options.first_downbeat_seconds) * options.bpm / 60


def _quarter_to_time(quarter: float, options: ScoreOptions, rhythm: RhythmAnalysis | None) -> float:
    if rhythm is not None:
        return _interpolate(quarter, rhythm.beat_positions_quarters, rhythm.beats_seconds)
    return options.first_downbeat_seconds + quarter * 60 / options.bpm


def _meter_lengths(options: ScoreOptions) -> tuple[Fraction, Fraction]:
    numerator, denominator = map(int, options.meter.split("/"))
    bar_length = Fraction(numerator * 4, denominator)
    beat_length = Fraction(3, 2) if denominator == 8 and numerator in (6, 9, 12) else Fraction(4, denominator)
    return bar_length, beat_length


def _group_lengths(signature: str, grouping: tuple[int, ...] = ()) -> list[Fraction]:
    numerator, denominator = map(int, signature.split("/"))
    if not grouping:
        if denominator in (8, 16) and numerator >= 6 and numerator % 3 == 0:
            grouping = (3,) * (numerator // 3)
        elif denominator in (8, 16) and numerator >= 5 and numerator % 2:
            grouping = (2,) * ((numerator - 3) // 2) + (3,)
        else:
            grouping = (1,) * numerator
    return [Fraction(value * 4, denominator) for value in grouping]


def _score_origin(
    options: ScoreOptions, events: list[QuantizedHit], rhythm: RhythmAnalysis | None = None,
) -> Fraction:
    """Start at a beat boundary so a cut within a beat has an intelligible pickup."""
    earliest = min((event.quarter for event in events), default=Fraction(0))
    if rhythm is not None and rhythm.bars and earliest < 0:
        first = rhythm.bars[0]
        groups = _group_lengths(first.meter, tuple(first.grouping))
        bar_length = sum(groups, Fraction(0))
        bar_start = (earliest // bar_length) * bar_length
        boundaries = [bar_start]
        for group in groups:
            boundaries.append(boundaries[-1] + group)
        return max(value for value in boundaries if value <= earliest)
    _, beat_length = _meter_lengths(options)
    return min(Fraction(0), (earliest // beat_length) * beat_length)


def _measure_plan(
    transcription: Transcription, options: ScoreOptions, events: list[QuantizedHit],
    rhythm: RhythmAnalysis | None,
) -> list[MeasurePlan]:
    """Use explicit local bars; extrapolate only before/after the available map."""
    origin = _score_origin(options, events, rhythm)
    clip_end = max(0, _time_to_quarter(transcription.duration_seconds, options, rhythm))
    # An attack needs a containing bar, not an entire grid cell beyond its
    # onset. A triplet grid may leave less than one cell before an odd barline.
    note_end = float(events[-1].quarter) + 1e-7 if events else 0
    score_end = max(clip_end, note_end)
    plans: list[MeasurePlan] = []
    first_meter = rhythm.bars[0].meter if rhythm is not None and rhythm.bars else options.meter
    first_grouping = tuple(rhythm.bars[0].grouping) if rhythm is not None and rhythm.bars else ()
    numerator, denominator = map(int, first_meter.split("/"))
    first_length = Fraction(numerator * 4, denominator)
    cursor = (origin // first_length) * first_length
    while cursor < 0:
        plans.append(MeasurePlan(max(origin, cursor), cursor + first_length, cursor, first_meter, first_grouping))
        cursor += first_length
    if rhythm is not None and rhythm.bars:
        for local_bar in rhythm.bars:
            start = Fraction(local_bar.start_quarter).limit_denominator(1920)
            end = Fraction(local_bar.end_quarter).limit_denominator(1920)
            if start >= score_end - 1e-9 and plans:
                break
            plans.append(MeasurePlan(start, end, start, local_bar.meter, tuple(local_bar.grouping)))
        cursor = plans[-1].end if plans else Fraction(0)
        last_meter = plans[-1].meter if plans else first_meter
        last_grouping = plans[-1].grouping if plans else first_grouping
    else:
        cursor = Fraction(0)
        last_meter, last_grouping = first_meter, first_grouping
    numerator, denominator = map(int, last_meter.split("/"))
    last_length = Fraction(numerator * 4, denominator)
    while cursor < score_end - 1e-9 or not plans or plans[-1].end <= 0:
        plans.append(MeasurePlan(cursor, cursor + last_length, cursor, last_meter, last_grouping))
        cursor += last_length
    return plans


def _tempo_points(
    options: ScoreOptions, rhythm: RhythmAnalysis | None, origin: Fraction, end: Fraction,
) -> list[TempoPoint]:
    """Piecewise slopes preserve performed beat times; visible labels are smoothed."""
    if rhythm is None:
        return [TempoPoint(float(origin), mido.bpm2tempo(options.bpm), options.bpm)]
    positions, seconds = rhythm.beat_positions_quarters, rhythm.beats_seconds
    quarters = [float(origin)] + [value for value in positions[:-1] if origin < value < end]
    points: list[TempoPoint] = []
    for quarter in quarters:
        index = min(max(bisect_right(positions, quarter) - 1, 0), len(positions) - 2)
        microseconds = round(1_000_000 * (seconds[index + 1] - seconds[index]) / (positions[index + 1] - positions[index]))
        if not 1 <= microseconds <= 0xFFFFFF:
            raise ValueError("The detected tempo grid lies outside the MIDI tempo range.")
        points.append(TempoPoint(quarter, microseconds))
    bpms = [60_000_000 / point.microseconds for point in points]
    # Three neighbouring pulse intervals reject detector jitter. A new label
    # needs two agreeing intervals and >=8% / 4 BPM difference from the last
    # printed tempo. The full, unsmoothed map remains in playback.
    initial = round(median(bpms[:3]), 1)
    result = [TempoPoint(points[0].quarter, points[0].microseconds, initial)]
    previous_label = initial
    for index, point in enumerate(points[1:], 1):
        future = bpms[index:index + 3]
        candidate = round(median(future), 1)
        stable = len(future) >= 2 and max(future) / min(future) <= 1.10
        change = abs(candidate - previous_label) >= max(4, previous_label * 0.08)
        # Locate a sustained step at the first interval of the new tempo,
        # rather than one pulse early when the median window sees ahead.
        matches_current = abs(bpms[index] - candidate) <= candidate * 0.05
        visible = candidate if stable and change and matches_current else None
        if visible is not None:
            previous_label = visible
        result.append(TempoPoint(point.quarter, point.microseconds, visible))
    return result


def _time_signature(signature: str, grouping: tuple[int, ...]) -> meter.TimeSignature:
    result = meter.TimeSignature(signature)
    if grouping:
        denominator = int(signature.split("/")[1])
        parts = [f"{value}/{denominator}" for value in grouping]
        result.beamSequence.partitionByList(parts)
        result.beatSequence.partitionByList(parts)
    return result


def _quantized_position(
    position: float, options: ScoreOptions, rhythm: RhythmAnalysis | None,
    *, bar_starts: list[float] | None = None,
) -> Fraction:
    """Anchor subdivisions at the local bar; every barline is a valid target.

    For example, the next 3/8 bar begins at quarter 1.5 even when the chosen
    grid is thirds of a quarter. Rounding on one global thirds grid would move
    its downbeat to 5/3 and misplace every subsequent bar.
    """
    if rhythm is not None and rhythm.bars:
        starts = bar_starts if bar_starts is not None else [item.start_quarter for item in rhythm.bars]
        index = bisect_right(starts, position) - 1
        if index >= 0:
            local_bar = rhythm.bars[index]
            start = Fraction(local_bar.start_quarter).limit_denominator(1920)
            end = Fraction(local_bar.end_quarter).limit_denominator(1920)
            if position >= float(end):
                length = end - start
                start += math.floor((position - float(start)) / float(length)) * length
                end = start + length
        else:
            length = Fraction(rhythm.bars[0].end_quarter).limit_denominator(1920)
            start = math.floor(position / float(length)) * length
            end = start + length
    else:
        length, _ = _meter_lengths(options)
        start = math.floor(position / float(length)) * length
        end = start + length
    step_index = math.floor((position - float(start)) * options.subdivisions + 0.5)
    rounded = max(start, min(end, start + Fraction(step_index, options.subdivisions)))
    candidates = {start, rounded, end}
    error = min(abs(float(candidate) - position) for candidate in candidates)
    return max(candidate for candidate in candidates if abs(float(candidate) - position) <= error + 1e-10)


def _quantize(
    transcription: Transcription, options: ScoreOptions, rhythm: RhythmAnalysis | None = None
) -> tuple[list[QuantizedHit], list[str], dict[str, Any]]:
    warnings: list[str] = []
    accepted: dict[tuple[Fraction, str], QuantizedHit] = {}
    errors: list[float] = []
    unknown: set[str] = set()
    before = 0
    collisions = 0
    supported_marks = {"ghost", "accent", "open", "closed", "roll"}
    unsupported_marks: set[str] = set()
    bar_starts = [item.start_quarter for item in rhythm.bars] if rhythm is not None else []
    for hit in sorted(transcription.hits, key=lambda h: h.time_seconds):
        if hit.instrument not in INSTRUMENTS:
            unknown.add(hit.instrument)
            continue
        if hit.time_seconds < options.first_downbeat_seconds - 1e-9:
            before += 1
        musical_position = _time_to_quarter(hit.time_seconds, options, rhythm)
        quarter = _quantized_position(musical_position, options, rhythm, bar_starts=bar_starts)
        error = _quarter_to_time(float(quarter), options, rhythm) - hit.time_seconds
        errors.append(abs(error))
        unsupported_marks.update(set(hit.articulations) - supported_marks)
        event = QuantizedHit(quarter, hit, error)
        key = quarter, hit.instrument
        if key in accepted:
            collisions += 1
            old = accepted[key].hit
            # Keep the stronger event for engraving. Neither original event is
            # removed from raw JSON or raw MIDI.
            if (hit.confidence or 0, hit.velocity or 0) <= (old.confidence or 0, old.velocity or 0):
                continue
        accepted[key] = event
    if unknown:
        warnings.append(
            "Unknown instrument IDs are retained in raw events but omitted from notation/MIDI: "
            + ", ".join(sorted(unknown))
        )
    if before:
        warnings.append(
            f"{before} hit(s) before the first downbeat are retained on the score grid; "
            "negative positions form a pickup or preceding measures. Raw events/MIDI "
            "retain their original clip times."
        )
    if rhythm is not None and any(
        hit.time_seconds < rhythm.beats_seconds[0] or hit.time_seconds > rhythm.beats_seconds[-1]
        for hit in transcription.hits
    ):
        warnings.append(
            "Some hits lie outside the detected beat range. Their score positions use the nearest "
            "beat interval extrapolated to the clip edge; review the pickup and ending."
        )
    if collisions:
        warnings.append(
            f"{collisions} same-instrument hit(s) collapsed onto an occupied score grid position. "
            "Raw events/MIDI preserve them; try a finer grid for fast strokes."
        )
    if unsupported_marks:
        warnings.append(
            "Articulations not engraved in this version: " + ", ".join(sorted(unsupported_marks))
        )
    if any(h.velocity is None for h in transcription.hits):
        warnings.append(
            "Missing velocities use MIDI velocity 100 for playback only. Dynamics, ghost notes "
            "and accents are not inferred from this placeholder."
        )
    if errors and max(errors) > 0.05:
        warnings.append(
            "Some hits move by more than 50 ms on the chosen grid. Check BPM, first downbeat "
            "and subdivision against the original audio."
        )
    events = sorted(accepted.values(), key=lambda e: (e.quarter, e.hit.instrument))
    metrics = {
        "raw_hit_count": len(transcription.hits),
        "score_hit_count": len(events),
        "grid_collisions": collisions,
        "hits_before_downbeat": before,
        "mean_quantization_error_ms": round(1000 * sum(errors) / len(errors), 3) if errors else 0,
        "max_quantization_error_ms": round(1000 * max(errors), 3) if errors else 0,
    }
    return events, warnings, metrics


def _write_midi(
    path: Path, timed_hits: list[tuple[float, DrumHit]], options: ScoreOptions
) -> None:
    """MIDI durations are short audition gates, not detected note lengths."""
    midi_file = mido.MidiFile(type=0, ticks_per_beat=960)
    track = mido.MidiTrack()
    midi_file.tracks.append(track)
    numerator, denominator = map(int, options.meter.split("/"))
    tempo_us = mido.bpm2tempo(options.bpm)
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    track.append(
        mido.MetaMessage(
            "time_signature", numerator=numerator, denominator=denominator,
            clocks_per_click=36 if denominator == 8 and numerator in (6, 9, 12) else 24, time=0,
        )
    )
    by_pitch: dict[int, list[tuple[float, DrumHit]]] = defaultdict(list)
    for when, hit in timed_hits:
        if hit.instrument in INSTRUMENTS:
            by_pitch[INSTRUMENTS[hit.instrument].midi].append((when, hit))
    midi_events: list[tuple[int, int, mido.Message]] = []
    for pitch, hits in by_pitch.items():
        hits.sort(key=lambda item: item[0])
        for index, (when, hit) in enumerate(hits):
            next_when = hits[index + 1][0] if index + 1 < len(hits) else when + 1
            gate = min(0.10, max(0.001, (next_when - when) / 2))
            onset = round(mido.second2tick(when, midi_file.ticks_per_beat, tempo_us))
            offset = max(onset + 1, round(mido.second2tick(when + gate, midi_file.ticks_per_beat, tempo_us)))
            midi_events.append((onset, 1, mido.Message("note_on", channel=9, note=pitch, velocity=hit.velocity or 100)))
            midi_events.append((offset, 0, mido.Message("note_off", channel=9, note=pitch, velocity=0)))
    previous_tick = 0
    for tick, _, message in sorted(midi_events, key=lambda event: (event[0], event[1])):
        message.time = tick - previous_tick
        track.append(message)
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi_file.save(path)


def _write_score_midi(
    path: Path, events: list[QuantizedHit], options: ScoreOptions,
    rhythm: RhythmAnalysis | None, plans: list[MeasurePlan], tempos: list[TempoPoint],
) -> None:
    """Encode musical positions with a local tempo map and bar signatures."""
    midi_file = mido.MidiFile(type=0, ticks_per_beat=960)
    track = mido.MidiTrack()
    midi_file.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    origin = plans[0].start
    midi_events: list[tuple[int, int, mido.Message | mido.MetaMessage]] = []
    previous_tempo: int | None = None
    for point in tempos:
        if point.microseconds != previous_tempo:
            tick = round((point.quarter - float(origin)) * midi_file.ticks_per_beat)
            midi_events.append((tick, -3, mido.MetaMessage("set_tempo", tempo=point.microseconds)))
            previous_tempo = point.microseconds
    previous_meter: str | None = None
    for plan in plans:
        if plan.meter != previous_meter:
            numerator, denominator = map(int, plan.meter.split("/"))
            midi_events.append((
                round((plan.start - origin) * midi_file.ticks_per_beat), -2,
                mido.MetaMessage(
                    "time_signature", numerator=numerator, denominator=denominator,
                    clocks_per_click=36 if denominator == 8 and numerator in (6, 9, 12) else 24,
                ),
            ))
            previous_meter = plan.meter
    by_pitch: dict[int, list[QuantizedHit]] = defaultdict(list)
    for event in events:
        by_pitch[INSTRUMENTS[event.hit.instrument].midi].append(event)
    for pitch, hits in by_pitch.items():
        hits.sort(key=lambda event: event.quarter)
        for index, event in enumerate(hits):
            when = _quarter_to_time(float(event.quarter), options, rhythm)
            next_when = (
                _quarter_to_time(float(hits[index + 1].quarter), options, rhythm)
                if index + 1 < len(hits) else when + 1
            )
            gate = min(0.10, max(0.001, (next_when - when) / 2))
            end_quarter = _time_to_quarter(when + gate, options, rhythm)
            onset = round((event.quarter - origin) * midi_file.ticks_per_beat)
            offset = max(onset + 1, round((end_quarter - float(origin)) * midi_file.ticks_per_beat))
            midi_events.append((onset, 1, mido.Message("note_on", channel=9, note=pitch, velocity=event.hit.velocity or 100)))
            midi_events.append((offset, 0, mido.Message("note_off", channel=9, note=pitch, velocity=0)))
    previous_tick = 0
    for tick, _, message in sorted(midi_events, key=lambda event: (event[0], event[1])):
        message.time = tick - previous_tick
        track.append(message)
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi_file.save(path)


def _make_instrument(name: str) -> instrument.UnpitchedPercussion:
    spec = INSTRUMENTS[name]
    drum = instrument.UnpitchedPercussion()
    drum.instrumentName = spec.label
    drum.instrumentId = "I-" + name
    drum.percMapPitch = spec.midi
    drum.midiChannel = 9
    return drum


def _unpitched(hit: DrumHit, stem: str, identifier: str) -> note.Unpitched:
    spec = INSTRUMENTS[hit.instrument]
    drum = _make_instrument(hit.instrument)
    result = note.Unpitched(displayName=spec.display, storedInstrument=drum)
    result.id = identifier
    result.notehead = spec.head
    result.stemDirection = stem
    result.volume.velocity = hit.velocity or 100
    if "ghost" in hit.articulations:
        result.noteheadParenthesis = True
    if "accent" in hit.articulations:
        result.articulations.append(articulations.Accent())
    if hit.instrument == "hihat_open" or "open" in hit.articulations:
        result.articulations.append(articulations.OpenString())
    if "closed" in hit.articulations:
        result.articulations.append(articulations.Stopped())
    if "roll" in hit.articulations:
        roll = expressions.Tremolo()
        roll.numberOfMarks = 3
        result.expressions.append(roll)
    return result


def _duration_piece(remaining: Fraction, position: Fraction, triplets: bool) -> Fraction:
    """Use simple values and respect their grid alignment, avoiding tied drum attacks."""
    values = [Fraction(3, 2), Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(3, 8), Fraction(1, 4), Fraction(1, 8)]
    if triplets:
        values += [Fraction(2, 3), Fraction(1, 3), Fraction(1, 6)]
    for value in sorted(set(values), reverse=True):
        if value > remaining:
            continue
        # A dotted value can begin on its underlying undotted duration grid.
        alignment = value * Fraction(2, 3) if value in (Fraction(3, 2), Fraction(3, 4), Fraction(3, 8)) else value
        if position % alignment == 0:
            return value
    return min(remaining, Fraction(1, 6) if triplets else Fraction(1, 8))


def _append_rests(
    voice: stream.Voice, start: Fraction, end: Fraction, triplets: bool,
    *, alignment_origin: Fraction = Fraction(0),
) -> None:
    cursor = start
    while cursor < end:
        length = _duration_piece(end - cursor, cursor - alignment_origin, triplets)
        rest = note.Rest(quarterLength=length)
        voice.insert(cursor, rest)
        cursor += length


def _build_score(
    transcription: Transcription, options: ScoreOptions, events: list[QuantizedHit],
    rhythm: RhythmAnalysis | None = None,
) -> tuple[stream.Score, int, dict[str, DrumHit]]:
    score = stream.Score(id="drum-score")
    score.metadata = metadata.Metadata(title=options.title)
    score.metadata.composer = "Drum transcription • local prototype"
    part = stream.Part(id="drum-kit")
    part.partName = "Drum kit"
    part.partAbbreviation = "Dr."
    note_sources: dict[str, DrumHit] = {}
    plans = _measure_plan(transcription, options, events, rhythm)
    origin = plans[0].start
    has_pickup = plans[0].start != plans[0].nominal_start
    starts = [plan.start for plan in plans]
    by_measure: dict[int, list[QuantizedHit]] = defaultdict(list)
    for event in events:
        index = bisect_right(starts, event.quarter) - 1
        if index < 0 or event.quarter >= plans[index].end:
            raise ValueError("A quantized event falls outside the written bar map.")
        by_measure[index].append(event)
    tempos = _tempo_points(options, rhythm, origin, plans[-1].end)
    tempos_by_measure: dict[int, list[tuple[int, TempoPoint]]] = defaultdict(list)
    for point_index, point in enumerate(tempos):
        measure_index = bisect_right(starts, point.quarter) - 1
        tempos_by_measure[measure_index].append((point_index, point))
    previous_tempo: int | None = None
    previous_signature: tuple[str, tuple[int, ...]] | None = None
    triplets = options.subdivisions in (3, 6)
    for measure_index, plan in enumerate(plans):
        measure_length = plan.end - plan.start
        bar_length = plan.end - plan.nominal_start
        measure = stream.Measure(number=measure_index if has_pickup else measure_index + 1)
        if plan.start != plan.nominal_start:
            measure.paddingLeft = plan.start - plan.nominal_start
            measure.showNumber = stream.enums.ShowNumber.NEVER
        if measure_index == 0:
            measure.insert(0, clef.PercussionClef())
            measure.insert(0, layout.StaffLayout(staffLines=5))
        signature = (plan.meter, plan.grouping)
        if signature != previous_signature:
            measure.insert(0, _time_signature(*signature))
            if len(set(plan.grouping)) > 1:
                grouping_text = expressions.TextExpression("+".join(map(str, plan.grouping)))
                grouping_text.style.fontSize = 9
                measure.insert(0, grouping_text)
            previous_signature = signature
        for point_index, point in tempos_by_measure[measure_index]:
            if point.microseconds == previous_tempo and point.display_bpm is None:
                continue
            exact_bpm = 60_000_000 / point.microseconds
            mark = tempo.MetronomeMark(
                number=point.display_bpm if point.display_bpm is not None else exact_bpm,
                numberSounding=exact_bpm, referent=duration.Duration(1),
            )
            mark.id = f"dt-tempo-{'visible' if point.display_bpm is not None else 'hidden'}-{point_index}"
            measure.insert(Fraction(point.quarter - float(plan.start)).limit_denominator(1920), mark)
            previous_tempo = point.microseconds
        group_spans: list[tuple[Fraction, Fraction]] = []
        group_start = plan.nominal_start
        for group_length in _group_lengths(plan.meter, plan.grouping):
            group_end = group_start + group_length
            if group_end > plan.start:
                group_spans.append((max(group_start, plan.start) - plan.start, group_end - plan.start))
            group_start = group_end
        for voice_id in (1, 2):
            voice = stream.Voice(id=str(voice_id))
            groups: dict[Fraction, list[DrumHit]] = defaultdict(list)
            for event in by_measure.get(measure_index, []):
                if INSTRUMENTS[event.hit.instrument].voice == voice_id:
                    groups[event.quarter - plan.start].append(event.hit)
            if not groups and measure_length == bar_length:
                rest = note.Rest(quarterLength=bar_length)
                rest.fullMeasure = True
                voice.insert(0, rest)
            else:
                for beat_start, beat_end in group_spans:
                    positions = sorted(p for p in groups if beat_start <= p < beat_end)
                    cursor = beat_start
                    for index, position in enumerate(positions):
                        _append_rests(voice, cursor, position, triplets, alignment_origin=beat_start)
                        next_position = positions[index + 1] if index + 1 < len(positions) else beat_end
                        length = _duration_piece(next_position - position, position - beat_start, triplets)
                        notes = []
                        for hit in groups[position]:
                            identifier = f"dt-note-{len(note_sources)}"
                            note_sources[identifier] = hit
                            notes.append(_unpitched(hit, "up" if voice_id == 1 else "down", identifier))
                        element = notes[0] if len(notes) == 1 else percussion.PercussionChord(notes)
                        element.quarterLength = length
                        element.stemDirection = "up" if voice_id == 1 else "down"
                        voice.insert(position, element)
                        cursor = position + length
                    _append_rests(voice, cursor, beat_end, triplets, alignment_origin=beat_start)
            stream.makeNotation.makeTupletBrackets(voice, inPlace=True)
            measure.insert(0, voice)
        if measure_index == len(plans) - 1:
            measure.rightBarline = bar.Barline("final")
        part.append(measure)
    # Beaming and tuplet brackets follow the declared meter; no hand-built beam
    # counts are inferred from how many onset events happen to be present.
    part.makeBeams(inPlace=True)
    score.insert(0, part)
    return score, len(plans), note_sources


def _fix_percussion_xml(path: Path, note_sources: dict[str, DrumHit]) -> None:
    """Preserve per-note percussion IDs/marks across music21's chord exporter.

    music21 9.x identifies instruments by Python class rather than semantic
    percussion pitch. Generic UnpitchedPercussion instances therefore collapse.
    It also promotes chord marks to the entire chord. We keep its rhythm/layout
    export, then attach exact GM definitions and marks to stable note IDs.
    MusicXML's midi-unpitched values are one-based (GM note + 1).
    """
    tree = ET.parse(path)
    root = tree.getroot()
    score_part = root.find("./part-list/score-part")
    if score_part is None:
        raise RuntimeError("MusicXML export contains no drum part.")
    for child in list(score_part):
        if child.tag in {"score-instrument", "midi-instrument", "midi-device"}:
            score_part.remove(child)
    names = sorted({hit.instrument for hit in note_sources.values()})
    for name in names:
        definition = ET.SubElement(score_part, "score-instrument", id="I-" + name)
        ET.SubElement(definition, "instrument-name").text = INSTRUMENTS[name].label
    for name in names:
        definition = ET.SubElement(score_part, "midi-instrument", id="I-" + name)
        ET.SubElement(definition, "midi-channel").text = "10"
        ET.SubElement(definition, "midi-unpitched").text = str(INSTRUMENTS[name].midi + 1)
    for xml_note in root.findall("./part/measure/note"):
        if xml_note.find("unpitched") is None:
            continue
        source = note_sources.get(xml_note.get("id", ""))
        if source is None:
            raise RuntimeError("MusicXML lost an event identifier; refusing ambiguous drum mapping.")
        for existing in xml_note.findall("instrument"):
            xml_note.remove(existing)
        children = list(xml_note)
        last_timing_index = max(i for i, child in enumerate(children) if child.tag in {"duration", "tie"})
        xml_note.insert(last_timing_index + 1, ET.Element("instrument", id="I-" + source.instrument))
        notation = xml_note.find("notations")
        if notation is not None:
            for child in list(notation):
                if child.tag in {"articulations", "technical", "ornaments"}:
                    notation.remove(child)
        marks = set(source.articulations)
        if source.instrument == "hihat_open":
            marks.add("open")
        if marks & {"accent", "open", "closed", "roll"}:
            if notation is None:
                notation = ET.SubElement(xml_note, "notations")
            if "accent" in marks:
                ET.SubElement(ET.SubElement(notation, "articulations"), "accent")
            if "open" in marks or "closed" in marks:
                technical = ET.SubElement(notation, "technical")
                if "open" in marks:
                    ET.SubElement(technical, "open-string")
                if "closed" in marks:
                    ET.SubElement(technical, "stopped")
            if "roll" in marks:
                ET.SubElement(ET.SubElement(notation, "ornaments"), "tremolo", type="single").text = "3"
        if "ghost" in marks:
            head = xml_note.find("notehead")
            if head is None:
                head = ET.Element("notehead")
                head.text = INSTRUMENTS[source.instrument].head
                # notehead follows stem and precedes beam/notations in MusicXML.
                insert_at = next((i for i, child in enumerate(xml_note) if child.tag in {"beam", "notations"}), len(xml_note))
                xml_note.insert(insert_at, head)
            head.set("parentheses", "yes")
    # music21 exports the playback tempo but does not preserve its style's
    # hideObjectOnPrint flag. MusicXML's explicit metronome attribute does.
    for metronome in root.findall("./part/measure/direction/direction-type/metronome"):
        if metronome.get("id", "").startswith("dt-tempo-hidden-"):
            metronome.set("print-object", "no")
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def find_musescore(configured: str | None = None) -> str | None:
    if configured:
        expanded = Path(configured).expanduser()
        if expanded.is_file():
            return str(expanded.resolve())
        return shutil.which(configured)
    for executable in ("MuseScore4", "MuseScore4.exe", "musescore4", "mscore", "musescore", "musescore3"):
        found = shutil.which(executable)
        if found:
            return found
    return None


def _export_pdf(xml_path: Path, pdf_path: Path, executable: str) -> str | None:
    # A failed re-export must never publish a PDF from an older score.
    pdf_path.unlink(missing_ok=True)
    env = os.environ.copy()
    if os.name != "nt" and not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        result = subprocess.run(
            [executable, "-o", str(pdf_path.resolve()), str(xml_path.resolve())],
            capture_output=True, text=True, timeout=90, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pdf_path.unlink(missing_ok=True)
        return f"MuseScore PDF export failed: {exc}. MusicXML remains available."
    if result.returncode != 0 or not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        pdf_path.unlink(missing_ok=True)
        return f"MuseScore PDF export failed (exit {result.returncode}): {detail}. MusicXML remains available."
    if pdf_path.read_bytes()[:5] != b"%PDF-":
        pdf_path.unlink(missing_ok=True)
        return "MuseScore output is not a PDF. MusicXML remains available."
    return None


def export_score(
    transcription: Transcription,
    options: ScoreOptions,
    output_dir: Path,
    musescore_bin: str | None = None,
    *,
    rhythm: RhythmAnalysis | None = None,
) -> dict[str, Any]:
    """Write editable notation and comparison files without modifying raw hits."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if rhythm is not None and (
        options.meter != rhythm.meter
        or not math.isclose(options.first_downbeat_seconds, rhythm.first_downbeat_seconds, abs_tol=1e-6)
    ):
        raise ValueError("Detected rhythm grid does not match the resolved score settings.")
    events, warnings, metrics = _quantize(transcription, options, rhythm)
    plans = _measure_plan(transcription, options, events, rhythm)
    origin = plans[0].start
    tempos = _tempo_points(options, rhythm, origin, plans[-1].end)
    bar_length, _ = _meter_lengths(options)
    first_bar_index = int(origin // bar_length)
    pickup_length = (first_bar_index + 1) * bar_length - origin if origin != first_bar_index * bar_length else Fraction(0)
    if first_bar_index < -1:
        warnings.append(
            "The first detected/selected downbeat follows more than one preceding measure. "
            "Earlier hits are preserved using the available grid; review the inferred bar numbering."
        )
    raw_midi = output_dir / "raw.mid"
    score_midi = output_dir / "score.mid"
    xml_path = output_dir / "score.musicxml"
    preview_path = output_dir / "preview.wav"
    _write_midi(raw_midi, [(hit.time_seconds, hit) for hit in transcription.hits], options)
    _write_score_midi(score_midi, events, options, rhythm, plans, tempos)
    score, bar_count, note_sources = _build_score(transcription, options, events, rhythm)
    score.write("musicxml", fp=xml_path)
    _fix_percussion_xml(xml_path, note_sources)
    write_preview_audio(transcription.hits, preview_path, duration_seconds=transcription.duration_seconds)
    files = {
        "raw_midi": raw_midi.name,
        "score_midi": score_midi.name,
        "musicxml": xml_path.name,
        "preview_audio": preview_path.name,
    }
    executable = find_musescore(musescore_bin)
    if executable:
        pdf_path = output_dir / "score.pdf"
        error = _export_pdf(xml_path, pdf_path, executable)
        if error:
            warnings.append(error)
        else:
            files["pdf"] = pdf_path.name
    else:
        warnings.append("MuseScore was not found. Open score.musicxml in MuseScore to view, edit or export PDF.")
    warnings.append("Preview audio uses synthesized percussion at original hit times; it is not a separated drum recording.")
    if rhythm is not None:
        warnings.append(
            "The sheet and score MIDI follow the local beat, meter and tempo map. "
            "Small tempo fluctuations are hidden in MusicXML playback; only sustained "
            "changes receive visible tempo labels. Review inferred meter and groupings."
        )
    meter_changes: list[dict[str, Any]] = []
    previous_meter: tuple[str, tuple[int, ...]] | None = None
    for plan in plans:
        current_meter = (plan.meter, plan.grouping)
        if current_meter != previous_meter:
            meter_changes.append({"quarter": float(plan.start), "meter": plan.meter, "grouping": list(plan.grouping)})
            previous_meter = current_meter
    visible_tempos = [{"quarter": point.quarter, "bpm": point.display_bpm} for point in tempos if point.display_bpm is not None]
    bpm_values = [60_000_000 / point.microseconds for point in tempos]
    metrics.update({
        "bars": bar_count,
        "bpm_source": rhythm.engine if rhythm is not None else "user",
        "tempo_mode": "detected_tempo_map" if rhythm is not None else "constant",
        "bpm_initial": visible_tempos[0]["bpm"],
        "bpm_min": round(min(bpm_values), 2),
        "bpm_max": round(max(bpm_values), 2),
        "tempo_change_count": len(visible_tempos) - 1,
        "tempo_changes": visible_tempos,
        "meter_change_count": len(meter_changes) - 1,
        "meter_changes": meter_changes,
        "first_downbeat_seconds": options.first_downbeat_seconds,
        "score_origin_quarter": float(origin),
        "pickup_quarters": float(pickup_length),
        "preceding_measures": -first_bar_index,
        "detected_downbeat_measure": -first_bar_index if pickup_length else 1 - first_bar_index,
        "legend": [
            {"id": name, "label": INSTRUMENTS[name].label, "gm_note": INSTRUMENTS[name].midi,
             "staff_position": INSTRUMENTS[name].display, "notehead": INSTRUMENTS[name].head,
             "voice": INSTRUMENTS[name].voice}
            for name in sorted({h.instrument for h in transcription.hits} & INSTRUMENTS.keys())
        ],
    })
    return {"files": files, "warnings": warnings, "metrics": metrics}
