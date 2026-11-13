"""Deterministic drum engraving; recognition and musical interpretation stay separate.

The score uses the supplied constant tempo and first-downbeat anchor. It does not
claim that short MIDI note lengths, velocities or expressive marks were detected.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
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

from .domain import DrumHit, ScoreOptions, Transcription
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


def _quantize(
    transcription: Transcription, options: ScoreOptions
) -> tuple[list[QuantizedHit], list[str], dict[str, Any]]:
    warnings: list[str] = []
    accepted: dict[tuple[Fraction, str], QuantizedHit] = {}
    errors: list[float] = []
    unknown: set[str] = set()
    before = 0
    collisions = 0
    supported_marks = {"ghost", "accent", "open", "closed", "roll"}
    unsupported_marks: set[str] = set()
    for hit in sorted(transcription.hits, key=lambda h: h.time_seconds):
        if hit.instrument not in INSTRUMENTS:
            unknown.add(hit.instrument)
            continue
        relative = hit.time_seconds - options.first_downbeat_seconds
        if relative < -1e-9:
            before += 1
            continue
        grid_position = max(0, math.floor(relative * options.bpm / 60 * options.subdivisions + 0.5))
        quarter = Fraction(grid_position, options.subdivisions)
        error = float(quarter) * 60 / options.bpm - relative
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
            f"{before} hit(s) before the first downbeat remain in raw events/MIDI but are omitted "
            "from the score. Pickup measures are not implemented."
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


def _append_rests(voice: stream.Voice, start: Fraction, end: Fraction, triplets: bool) -> None:
    cursor = start
    while cursor < end:
        length = _duration_piece(end - cursor, cursor, triplets)
        rest = note.Rest(quarterLength=length)
        voice.insert(cursor, rest)
        cursor += length


def _build_score(
    transcription: Transcription, options: ScoreOptions, events: list[QuantizedHit]
) -> tuple[stream.Score, int, dict[str, DrumHit]]:
    score = stream.Score(id="drum-score")
    score.metadata = metadata.Metadata(title=options.title)
    score.metadata.composer = "Drum transcription • local prototype"
    part = stream.Part(id="drum-kit")
    part.partName = "Drum kit"
    part.partAbbreviation = "Dr."
    note_sources: dict[str, DrumHit] = {}
    numerator, denominator = map(int, options.meter.split("/"))
    bar_length = Fraction(numerator * 4, denominator)
    beat_length = Fraction(3, 2) if denominator == 8 and numerator in (6, 9, 12) else Fraction(4, denominator)
    clip_quarters = max(0, transcription.duration_seconds - options.first_downbeat_seconds) * options.bpm / 60
    last_quarter = float(events[-1].quarter) + 1 / options.subdivisions if events else 0
    bar_count = max(1, math.ceil(max(clip_quarters, last_quarter) / float(bar_length) - 1e-9))
    by_measure: dict[int, list[QuantizedHit]] = defaultdict(list)
    for event in events:
        by_measure[int(event.quarter // bar_length)].append(event)
    triplets = options.subdivisions in (3, 6)
    for bar_index in range(bar_count):
        measure = stream.Measure(number=bar_index + 1)
        if bar_index == 0:
            measure.insert(0, clef.PercussionClef())
            measure.insert(0, layout.StaffLayout(staffLines=5))
            measure.insert(0, meter.TimeSignature(options.meter))
            measure.insert(0, tempo.MetronomeMark(number=options.bpm, referent=duration.Duration(1)))
        for voice_id in (1, 2):
            voice = stream.Voice(id=str(voice_id))
            groups: dict[Fraction, list[DrumHit]] = defaultdict(list)
            for event in by_measure.get(bar_index, []):
                if INSTRUMENTS[event.hit.instrument].voice == voice_id:
                    groups[event.quarter - bar_index * bar_length].append(event.hit)
            if not groups:
                rest = note.Rest(quarterLength=bar_length)
                rest.fullMeasure = True
                voice.insert(0, rest)
            else:
                beat_start = Fraction(0)
                while beat_start < bar_length:
                    beat_end = min(beat_start + beat_length, bar_length)
                    positions = sorted(p for p in groups if beat_start <= p < beat_end)
                    cursor = beat_start
                    for index, position in enumerate(positions):
                        _append_rests(voice, cursor, position, triplets)
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
                    _append_rests(voice, cursor, beat_end, triplets)
                    beat_start = beat_end
            stream.makeNotation.makeTupletBrackets(voice, inPlace=True)
            measure.insert(0, voice)
        if bar_index == bar_count - 1:
            measure.rightBarline = bar.Barline("final")
        part.append(measure)
    # Beaming and tuplet brackets follow the declared meter; no hand-built beam
    # counts are inferred from how many onset events happen to be present.
    part.makeBeams(inPlace=True)
    score.insert(0, part)
    return score, bar_count, note_sources


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
) -> dict[str, Any]:
    """Write editable notation and comparison files without modifying raw hits."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events, warnings, metrics = _quantize(transcription, options)
    raw_midi = output_dir / "raw.mid"
    score_midi = output_dir / "score.mid"
    xml_path = output_dir / "score.musicxml"
    preview_path = output_dir / "preview.wav"
    _write_midi(raw_midi, [(hit.time_seconds, hit) for hit in transcription.hits], options)
    _write_midi(score_midi, [(float(event.quarter) * 60 / options.bpm, event.hit) for event in events], options)
    score, bar_count, note_sources = _build_score(transcription, options, events)
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
    metrics.update({
        "bars": bar_count,
        "bpm_source": "user",
        "tempo_mode": "constant",
        "first_downbeat_seconds": options.first_downbeat_seconds,
        "legend": [
            {"id": name, "label": INSTRUMENTS[name].label, "gm_note": INSTRUMENTS[name].midi,
             "staff_position": INSTRUMENTS[name].display, "notehead": INSTRUMENTS[name].head,
             "voice": INSTRUMENTS[name].voice}
            for name in sorted({h.instrument for h in transcription.hits} & INSTRUMENTS.keys())
        ],
    })
    return {"files": files, "warnings": warnings, "metrics": metrics}
