from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

import mido
import pytest
import soundfile as sf

from drum_transcription.domain import (
    DrumHit,
    RhythmAnalysis,
    RhythmBar,
    ScoreOptions,
    Transcription,
)
from drum_transcription.notation import INSTRUMENTS, _build_score, _quantize, export_score


def _midi_onsets(path: Path) -> list[tuple[int, float, int]]:
    result = []
    elapsed = 0.0
    for message in mido.MidiFile(path):
        elapsed += message.time
        if message.type == "note_on" and message.velocity > 0:
            assert message.channel == 9
            result.append((message.note, elapsed, message.velocity))
    return result


def test_original_timing_survives_quantization_and_collision(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    transcription = Transcription(
        engine="test", duration_seconds=2,
        hits=[
            DrumHit(time_seconds=0.1, instrument="kick", velocity=90),
            DrumHit(time_seconds=0.507, instrument="snare", velocity=40),
            DrumHit(time_seconds=0.531, instrument="snare", velocity=110),
            DrumHit(time_seconds=0.76, instrument="hihat_open", velocity=70),
        ],
    )
    before = transcription.model_dump_json()
    result = export_score(transcription, ScoreOptions(first_downbeat_seconds=0.5), tmp_path)
    assert transcription.model_dump_json() == before
    raw = _midi_onsets(tmp_path / result["files"]["raw_midi"])
    score = _midi_onsets(tmp_path / result["files"]["score_midi"])
    assert [p for p, _, _ in raw] == [36, 38, 38, 46]
    assert [t for _, t, _ in raw] == pytest.approx([0.1, 0.507, 0.531, 0.76], abs=0.001)
    assert [(p, v) for p, _, v in score] == [(36, 90), (38, 110), (46, 70)]
    assert [t for _, t, _ in score] == pytest.approx([0.125, 0.5, 0.75], abs=0.001)
    assert result["metrics"]["grid_collisions"] == 1
    assert result["metrics"]["hits_before_downbeat"] == 1
    assert any("collapsed" in warning for warning in result["warnings"])
    assert result["metrics"]["pickup_quarters"] == 1
    assert any("retained" in warning for warning in result["warnings"])
    preview, sr = sf.read(tmp_path / result["files"]["preview_audio"])
    assert sr == 22050
    assert len(preview) >= 2 * sr
    assert max(abs(preview)) > 0


def test_xml_preserves_gm_mapping_and_marks_inside_chords(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    transcription = Transcription(
        engine="test", duration_seconds=2,
        hits=[
            DrumHit(time_seconds=0, instrument="kick"),
            DrumHit(time_seconds=0, instrument="hihat_open"),
            DrumHit(time_seconds=0, instrument="snare", articulations=["ghost", "accent"]),
            DrumHit(time_seconds=0.5, instrument="ride_bell"),
            DrumHit(time_seconds=1.0, instrument="snare", articulations=["roll"]),
        ],
    )
    result = export_score(transcription, ScoreOptions(bpm=96, meter="3/4"), tmp_path)
    xml = ET.parse(tmp_path / result["files"]["musicxml"])
    assert xml.findtext(".//clef/sign") == "percussion"
    assert xml.findtext(".//staff-lines") == "5"
    assert xml.findtext(".//beats") == "3"
    assert xml.findtext(".//beat-type") == "4"
    assert xml.findtext(".//metronome/beat-unit") == "quarter"
    assert xml.findtext(".//metronome/per-minute") == "96"
    instruments = {
        item.get("id"): (item.findtext("midi-channel"), int(item.findtext("midi-unpitched")))
        for item in xml.findall(".//midi-instrument")
    }
    assert instruments["I-kick"] == ("10", 37)
    assert instruments["I-snare"] == ("10", 39)
    assert instruments["I-hihat_open"] == ("10", 47)
    assert instruments["I-ride_bell"] == ("10", 54)
    notes = xml.findall("./part/measure/note")
    pitched = [item for item in notes if item.find("unpitched") is not None]
    assert len(pitched) == 5
    assert all(item.find("instrument").get("id") in instruments for item in pitched)
    snare = next(item for item in pitched if item.find("instrument").get("id") == "I-snare")
    assert snare.find("notehead").get("parentheses") == "yes"
    assert snare.find(".//accent") is not None
    open_hat = next(item for item in pitched if item.find("instrument").get("id") == "I-hihat_open")
    assert open_hat.find(".//open-string") is not None
    assert snare.find(".//open-string") is None
    kick = next(item for item in pitched if item.find("instrument").get("id") == "I-kick")
    assert kick.findtext("voice") == "2"
    assert kick.findtext("stem") == "down"
    assert snare.findtext("voice") == "1"
    assert snare.findtext("stem") == "up"
    assert xml.findtext(".//tremolo") == "3"
    assert any("Missing velocities" in warning for warning in result["warnings"])


@pytest.mark.parametrize("time_signature", [f"{n}/{d}" for n in (2, 3, 4, 5, 6, 7, 9, 12) for d in (4, 8)])
@pytest.mark.parametrize("subdivisions", [1, 2, 3, 4, 6, 8])
def test_each_voice_fills_each_measure_exactly(time_signature, subdivisions):
    options = ScoreOptions(bpm=120, meter=time_signature, subdivisions=subdivisions)
    transcription = Transcription(
        engine="fixture", duration_seconds=2.3,
        hits=[
            DrumHit(time_seconds=0.01, instrument="kick"),
            DrumHit(time_seconds=0.167, instrument="hihat_closed"),
            DrumHit(time_seconds=0.43, instrument="snare"),
            DrumHit(time_seconds=0.76, instrument="hihat_closed"),
            DrumHit(time_seconds=1.02, instrument="kick"),
            DrumHit(time_seconds=1.96, instrument="tom_low"),
        ],
    )
    events, _, _ = _quantize(transcription, options)
    score, _, _ = _build_score(transcription, options, events)
    numerator, denominator = map(int, time_signature.split("/"))
    expected = Fraction(numerator * 4, denominator)
    for measure in score.parts[0].getElementsByClass("Measure"):
        assert len(measure.voices) == 2
        for voice in measure.voices:
            total = sum((Fraction(element.quarterLength) for element in voice.notesAndRests), Fraction(0))
            assert total == expected
            assert Fraction(voice.highestTime) == expected


def test_triplet_xml_and_midi_contain_declared_tempo(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    transcription = Transcription(
        engine="fixture", duration_seconds=1,
        hits=[DrumHit(time_seconds=i / 6, instrument="snare", velocity=80) for i in range(6)],
    )
    result = export_score(transcription, ScoreOptions(subdivisions=3), tmp_path)
    xml = ET.parse(tmp_path / result["files"]["musicxml"])
    assert xml.findtext(".//time-modification/actual-notes") == "3"
    assert xml.findtext(".//time-modification/normal-notes") == "2"
    assert xml.find(".//tuplet[@type='start']") is not None
    for key in ("raw_midi", "score_midi"):
        messages = list(mido.MidiFile(tmp_path / result["files"][key]).tracks[0])
        assert any(message.type == "set_tempo" and message.tempo == 500000 for message in messages)
        assert any(message.type == "time_signature" and message.numerator == 4 for message in messages)


def test_unknown_instrument_is_reported_and_every_supported_class_exports(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    hits = [DrumHit(time_seconds=i * 0.25, instrument=name, velocity=90) for i, name in enumerate(INSTRUMENTS)]
    hits.append(DrumHit(time_seconds=0, instrument="unknown_future_class"))
    result = export_score(Transcription(engine="fixture", duration_seconds=8, hits=hits), ScoreOptions(), tmp_path)
    assert result["metrics"]["score_hit_count"] == len(INSTRUMENTS)
    assert any("unknown_future_class" in warning for warning in result["warnings"])
    xml = ET.parse(tmp_path / result["files"]["musicxml"])
    assert len(xml.findall(".//midi-instrument")) == len(INSTRUMENTS)


def test_failed_musescore_does_not_advertise_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: "/missing/MuseScore4")
    result = export_score(Transcription(engine="fixture", duration_seconds=0), ScoreOptions(), tmp_path)
    assert "pdf" not in result["files"]
    assert (tmp_path / result["files"]["musicxml"]).is_file()
    assert any("PDF export failed" in warning for warning in result["warnings"])


def _rhythm(beats: list[float], first_downbeat_index: int = 0, bpm: float = 120) -> RhythmAnalysis:
    return RhythmAnalysis(
        bpm=bpm, meter="4/4", first_downbeat_seconds=beats[first_downbeat_index],
        beats_seconds=beats, downbeats_seconds=beats[first_downbeat_index::4],
        beat_positions_quarters=[float(index - first_downbeat_index) for index in range(len(beats))],
    )


def test_fractional_downbeat_preserves_pickup_in_xml_and_both_midis(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    original = Transcription(
        engine="fixture", duration_seconds=3.5,
        hits=[DrumHit(time_seconds=value, instrument="snare", velocity=90) for value in (0, 0.5, 1, 1.5, 2)],
    )
    snapshot = original.model_dump_json()
    options = ScoreOptions(first_downbeat_seconds=1.5)
    rhythm = _rhythm([index * 0.5 for index in range(8)], 3)
    result = export_score(original, options, tmp_path, rhythm=rhythm)
    assert original.model_dump_json() == snapshot
    assert result["metrics"]["score_hit_count"] == 5
    assert result["metrics"]["pickup_quarters"] == 3
    assert result["metrics"]["score_origin_quarter"] == -3
    assert result["metrics"]["detected_downbeat_measure"] == 1
    xml = ET.parse(tmp_path / result["files"]["musicxml"])
    measures = xml.findall("./part/measure")
    assert measures[0].get("implicit") == "yes"
    assert measures[0].get("number") == "0"
    assert measures[1].get("number") == "1"
    assert len(measures[0].findall("./note/unpitched")) == 3
    assert len(xml.findall("./part/measure/note/unpitched")) == 5
    divisions = int(xml.findtext(".//divisions"))
    for voice in ("1", "2"):
        duration = sum(int(item.findtext("duration")) for item in measures[0].findall(f"note[voice='{voice}']") if item.find("chord") is None)
        assert duration == 3 * divisions
    # A simple pickup must not acquire accidental tuplets or fragmented dotted rests.
    assert measures[0].find(".//time-modification") is None
    foot_rests = measures[0].findall("note[voice='2']")
    assert [item.findtext("type") for item in foot_rests] == ["quarter"] * 3
    assert [when for _, when, _ in _midi_onsets(tmp_path / "raw.mid")] == pytest.approx([0, 0.5, 1, 1.5, 2], abs=0.001)
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([0, 0.5, 1, 1.5, 2], abs=0.001)


def test_silence_and_fractional_clip_offset_do_not_become_a_pickup(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    original = Transcription(
        engine="fixture", duration_seconds=3.37,
        metadata={"clip_start_seconds": 1.5},
        hits=[DrumHit(time_seconds=value, instrument="kick", velocity=90) for value in (0.37, 0.87, 1.37)],
    )
    rhythm = _rhythm([0.37 + index * 0.5 for index in range(7)])
    result = export_score(original, ScoreOptions(first_downbeat_seconds=0.37), tmp_path, rhythm=rhythm)
    assert result["metrics"]["pickup_quarters"] == 0
    assert result["metrics"]["score_origin_quarter"] == 0
    assert result["metrics"]["first_downbeat_seconds"] == 0.37
    assert ET.parse(tmp_path / "score.musicxml").find("./part/measure").get("implicit") == "no"
    assert [when for _, when, _ in _midi_onsets(tmp_path / "raw.mid")] == pytest.approx([0.37, 0.87, 1.37], abs=0.001)
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([0, 0.5, 1], abs=0.001)
    assert original.metadata == {"clip_start_seconds": 1.5}


def test_detected_grid_and_score_playback_follow_unequal_intervals(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    beats = [0.37, 0.87, 1.47, 2.17, 2.97]
    rhythm = _rhythm(beats)
    original = Transcription(engine="fixture", duration_seconds=3.4, hits=[DrumHit(time_seconds=value, instrument="snare", velocity=85) for value in beats])
    options = ScoreOptions(first_downbeat_seconds=0.37)
    detected, _, metrics = _quantize(original, options, rhythm)
    constant, _, _ = _quantize(original, options)
    assert [event.quarter for event in detected] == list(map(Fraction, range(5)))
    assert [event.quarter for event in constant] != [event.quarter for event in detected]
    assert metrics["max_quantization_error_ms"] == 0
    result = export_score(original, options, tmp_path, rhythm=rhythm)
    assert result["metrics"]["tempo_mode"] == "detected_tempo_map"
    assert [when for _, when, _ in _midi_onsets(tmp_path / "raw.mid")] == pytest.approx(beats, abs=0.001)
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([value - beats[0] for value in beats], abs=0.001)
    xml = ET.parse(tmp_path / "score.musicxml")
    assert xml.findall(".//metronome[@print-object='no']")


def test_time_shifted_audio_produces_identical_score_positions():
    baseline = [index * 0.5 for index in range(9)]
    shifted = [value + 1.537 for value in baseline]
    positions = []
    for values in (baseline, shifted):
        original = Transcription(engine="fixture", duration_seconds=values[-1] + 0.3, hits=[DrumHit(time_seconds=value, instrument="snare") for value in values])
        rhythm = _rhythm(values, first_downbeat_index=3)
        options = ScoreOptions(first_downbeat_seconds=values[3])
        events, _, _ = _quantize(original, options, rhythm)
        positions.append([event.quarter for event in events])
    assert positions[0] == positions[1] == list(map(Fraction, range(-3, 6)))


def test_edge_extrapolation_keeps_prebeat_hits_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = _rhythm([0.37 + index * 0.5 for index in range(7)], first_downbeat_index=2)
    original = Transcription(engine="fixture", duration_seconds=4, hits=[
        DrumHit(time_seconds=0.12, instrument="snare"),
        DrumHit(time_seconds=1.37, instrument="kick"),
        DrumHit(time_seconds=3.62, instrument="snare"),
    ])
    options = ScoreOptions(first_downbeat_seconds=1.37)
    events, _, _ = _quantize(original, options, rhythm)
    assert [event.quarter for event in events] == [Fraction(-5, 2), Fraction(0), Fraction(9, 2)]
    result = export_score(original, options, tmp_path, rhythm=rhythm)
    assert result["metrics"]["score_hit_count"] == 3
    assert any("extrapolated" in warning for warning in result["warnings"])
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([0.25, 1.5, 3.75], abs=0.001)


def test_multiple_preceding_measures_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    original = Transcription(engine="fixture", duration_seconds=6, hits=[DrumHit(time_seconds=value, instrument="kick") for value in (0, 0.5, 2.5, 4.5)])
    options = ScoreOptions(first_downbeat_seconds=4.5)
    events, _, _ = _quantize(original, options)
    score, _, _ = _build_score(original, options, events)
    lengths = [tuple(Fraction(voice.highestTime) for voice in measure.voices) for measure in score.parts[0].getElementsByClass("Measure")]
    assert lengths == [(Fraction(1), Fraction(1))] + [(Fraction(4), Fraction(4))] * 3
    result = export_score(original, options, tmp_path)
    assert result["metrics"]["preceding_measures"] == 3
    assert result["metrics"]["detected_downbeat_measure"] == 3
    assert result["metrics"]["score_hit_count"] == 4
    assert any("more than one preceding measure" in warning for warning in result["warnings"])


def test_compound_meter_pickup_keeps_two_balanced_voices(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    original = Transcription(engine="fixture", duration_seconds=2, hits=[DrumHit(time_seconds=value, instrument="snare") for value in (0, 0.25, 0.5, 0.75)])
    options = ScoreOptions(meter="6/8", first_downbeat_seconds=0.75)
    result = export_score(original, options, tmp_path)
    assert result["metrics"]["pickup_quarters"] == 1.5
    xml = ET.parse(tmp_path / "score.musicxml")
    pickup = xml.find("./part/measure")
    foot_rests = pickup.findall("note[voice='2']")
    assert len(foot_rests) == 1
    assert foot_rests[0].findtext("type") == "quarter"
    assert foot_rests[0].find("dot") is not None
    assert len(xml.findall("./part/measure/note/unpitched")) == 4


def _mapped_rhythm(
    signatures: list[str], groupings: list[list[int]] | None = None,
    bpms: list[float] | None = None, start: float = 0,
) -> RhythmAnalysis:
    """An annotated grid fixture; this does not claim audio-model recognition."""
    groupings = groupings or [[] for _ in signatures]
    bpms = bpms or [120] * len(signatures)
    bars = []
    positions, seconds = [0.0], [start]
    quarter, when = 0.0, start
    for signature, grouping, bpm in zip(signatures, groupings, bpms, strict=True):
        numerator, denominator = map(int, signature.split("/"))
        length = numerator * 4 / denominator
        end = when + length * 60 / bpm
        bars.append(RhythmBar(
            start_seconds=when, end_seconds=end,
            start_quarter=quarter, end_quarter=quarter + length,
            meter=signature, grouping=grouping,
            source="fixture", estimated=False,
        ))
        for step in range(1, numerator + 1):
            positions.append(quarter + step * 4 / denominator)
            seconds.append(when + step * 4 / denominator * 60 / bpm)
        quarter += length
        when = end
    return RhythmAnalysis(
        bpm=bpms[0], meter=signatures[0], first_downbeat_seconds=start,
        beats_seconds=seconds, downbeats_seconds=[bar.start_seconds for bar in bars],
        beat_positions_quarters=positions, bars=bars,
    )


def _voice_durations(xml: ET.ElementTree) -> list[tuple[Fraction, Fraction]]:
    divisions = int(xml.findtext(".//divisions"))
    return [tuple(
        Fraction(sum(
            int(item.findtext("duration")) for item in measure.findall(f"note[voice='{voice}']")
            if item.find("chord") is None
        ), divisions)
        for voice in ("1", "2")
    ) for measure in xml.findall("./part/measure")]


def test_changed_meter_keeps_hits_on_both_sides_of_barline(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = _mapped_rhythm(["4/4", "3/4"], start=0.375)
    times = [0.375, 2.25, 2.375, 3.75]
    original = Transcription(engine="annotated", duration_seconds=3.875, hits=[
        DrumHit(time_seconds=when, instrument="snare", velocity=85) for when in times
    ])
    before = original.model_dump_json()
    result = export_score(original, ScoreOptions(first_downbeat_seconds=0.375), tmp_path, rhythm=rhythm)
    assert original.model_dump_json() == before
    xml = ET.parse(tmp_path / "score.musicxml")
    assert [(item.findtext("beats"), item.findtext("beat-type")) for item in xml.findall("./part/measure/attributes/time")] == [("4", "4"), ("3", "4")]
    assert _voice_durations(xml) == [(Fraction(4), Fraction(4)), (Fraction(3), Fraction(3))]
    assert [len(measure.findall("note/unpitched")) for measure in xml.findall("./part/measure")] == [2, 2]
    assert result["metrics"]["score_hit_count"] == 4
    assert result["metrics"]["meter_change_count"] == 1
    assert [when for _, when, _ in _midi_onsets(tmp_path / "raw.mid")] == pytest.approx(times, abs=0.001)
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([value - 0.375 for value in times], abs=0.001)
    signatures = []
    tick = 0
    for message in mido.MidiFile(tmp_path / "score.mid").tracks[0]:
        tick += message.time
        if message.type == "time_signature":
            signatures.append((tick, message.numerator, message.denominator))
    assert signatures == [(0, 4, 4), (3840, 3, 4)]


def test_odd_meter_grouping_changes_beams_without_changing_bar_length(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = _mapped_rhythm(["7/8", "7/8"], [[3, 2, 2], [2, 2, 3]])
    original = Transcription(engine="annotated", duration_seconds=3.5, hits=[
        DrumHit(time_seconds=index * 0.25, instrument="hihat_closed", velocity=80) for index in range(14)
    ])
    result = export_score(original, ScoreOptions(meter="7/8"), tmp_path, rhythm=rhythm)
    xml = ET.parse(tmp_path / "score.musicxml")
    measures = xml.findall("./part/measure")
    assert _voice_durations(xml) == [(Fraction(7, 2), Fraction(7, 2))] * 2
    expected = [["begin", "continue", "end", "begin", "end", "begin", "end"], ["begin", "end", "begin", "end", "begin", "continue", "end"]]
    for measure, beams in zip(measures, expected, strict=True):
        assert [item.findtext("beam[@number='1']") for item in measure.findall("note") if item.find("unpitched") is not None] == beams
    assert [item.text for item in xml.findall(".//direction-type/words")] == ["3+2+2", "2+2+3"]
    assert result["metrics"]["grid_collisions"] == 0


def test_mixed_odd_meters_and_pickup_fill_both_voices(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = _mapped_rhythm(["5/8", "7/8", "13/16"], [[2, 3], [2, 2, 3], [3, 3, 3, 2, 2]], start=0.5)
    # A half-beat before the first downbeat stays in the 5/8 pickup.
    times = [0.25, 0.5, 1.5, 1.75, 3.25, 3.5, 5.0]
    original = Transcription(engine="annotated", duration_seconds=5.125, hits=[
        DrumHit(time_seconds=when, instrument="snare", velocity=85) for when in times
    ])
    result = export_score(original, ScoreOptions(meter="5/8", first_downbeat_seconds=0.5), tmp_path, rhythm=rhythm)
    xml = ET.parse(tmp_path / "score.musicxml")
    assert _voice_durations(xml) == [
        (Fraction(3, 2), Fraction(3, 2)), (Fraction(5, 2), Fraction(5, 2)),
        (Fraction(7, 2), Fraction(7, 2)), (Fraction(13, 4), Fraction(13, 4)),
    ]
    assert len(xml.findall("./part/measure/note/unpitched")) == len(times)
    assert result["metrics"]["pickup_quarters"] == 1.5
    assert result["metrics"]["meter_change_count"] == 2
    assert xml.find("./part/measure").get("implicit") == "yes"


def test_sustained_tempo_change_is_printed_and_score_midi_stays_in_time(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = _mapped_rhythm(["4/4"] * 4, bpms=[120, 120, 90, 90], start=0.37)
    original = Transcription(engine="annotated", duration_seconds=rhythm.bars[-1].end_seconds, hits=[
        DrumHit(time_seconds=when, instrument="kick", velocity=85) for when in rhythm.beats_seconds[:-1]
    ])
    result = export_score(original, ScoreOptions(first_downbeat_seconds=0.37), tmp_path, rhythm=rhythm)
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx(
        [when - 0.37 for when in rhythm.beats_seconds[:-1]], abs=0.001,
    )
    xml = ET.parse(tmp_path / "score.musicxml")
    visible = [item for item in xml.findall(".//metronome") if item.get("print-object") != "no"]
    assert [float(item.findtext("per-minute")) for item in visible] == [120, 90]
    assert result["metrics"]["bpm_initial"] == 120
    assert result["metrics"]["tempo_changes"] == [{"quarter": 0.0, "bpm": 120}, {"quarter": 8.0, "bpm": 90}]
    assert result["metrics"]["bpm_min"] == pytest.approx(90, abs=0.001)
    assert result["metrics"]["bpm_max"] == 120


def test_grouped_unequal_pulses_do_not_create_spurious_tempo_changes(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = RhythmAnalysis(
        bpm=120, meter="7/8", first_downbeat_seconds=0.37,
        beats_seconds=[0.37, 0.87, 1.37, 2.12, 2.62, 3.12, 3.87],
        downbeats_seconds=[0.37, 2.12],
        beat_positions_quarters=[0, 1, 2, 3.5, 4.5, 5.5, 7],
        bars=[
            RhythmBar(start_seconds=0.37, end_seconds=2.12, start_quarter=0, end_quarter=3.5, meter="7/8", grouping=[2, 2, 3]),
            RhythmBar(start_seconds=2.12, end_seconds=3.87, start_quarter=3.5, end_quarter=7, meter="7/8", grouping=[2, 2, 3]),
        ],
    )
    original = Transcription(engine="annotated", duration_seconds=3.87, hits=[
        DrumHit(time_seconds=when, instrument="snare", velocity=90) for when in rhythm.beats_seconds[:-1]
    ])
    result = export_score(original, ScoreOptions(meter="7/8", first_downbeat_seconds=0.37), tmp_path, rhythm=rhythm)
    assert result["metrics"]["bpm_min"] == result["metrics"]["bpm_max"] == 120
    assert result["metrics"]["tempo_change_count"] == 0
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([when - 0.37 for when in rhythm.beats_seconds[:-1]], abs=0.001)


@pytest.mark.parametrize("signature,subdivisions", [("3/8", 3), ("13/16", 3), ("13/16", 6)])
def test_triplet_grid_restarts_at_odd_barline_and_midi_keeps_local_tempo(
    tmp_path, monkeypatch, signature, subdivisions,
):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    rhythm = _mapped_rhythm([signature] * 2, bpms=[120, 90], start=0.375)
    length = Fraction(rhythm.bars[0].end_quarter)
    step = Fraction(1, subdivisions)
    # Last regular grid point stays in its own bar, even if less than a full
    # grid cell remains. The following downbeat is an exact, separate target.
    last_grid = (length // step) * step
    positions = [Fraction(0), last_grid, length, length + step, length + last_grid]
    times = [
        0.375 + float(position) * 0.5 if position < length
        else rhythm.bars[1].start_seconds + float(position - length) * 2 / 3
        for position in positions
    ]
    original = Transcription(engine="annotated", duration_seconds=rhythm.bars[-1].end_seconds, hits=[
        DrumHit(time_seconds=when, instrument="snare", velocity=90) for when in times
    ])
    options = ScoreOptions(meter=signature, subdivisions=subdivisions, first_downbeat_seconds=0.375)
    events, _, metrics = _quantize(original, options, rhythm)
    assert [event.quarter for event in events] == positions
    assert metrics["max_quantization_error_ms"] == 0
    result = export_score(original, options, tmp_path, rhythm=rhythm)
    xml = ET.parse(tmp_path / "score.musicxml")
    assert _voice_durations(xml) == [(length, length)] * 2
    assert [len(item.findall("note/unpitched")) for item in xml.findall("./part/measure")] == [2, 3]
    assert result["metrics"]["bars"] == 2
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([when - 0.375 for when in times], abs=0.001)


def test_manual_triplet_grid_preserves_odd_meter_downbeat_and_pickup(tmp_path, monkeypatch):
    monkeypatch.setattr("drum_transcription.notation.find_musescore", lambda configured: None)
    positions = [Fraction(-1, 2), Fraction(0), Fraction(4, 3), Fraction(3, 2), Fraction(17, 6)]
    times = [0.375 + float(position) / 2 for position in positions]
    original = Transcription(engine="annotated", duration_seconds=1.875, hits=[
        DrumHit(time_seconds=when, instrument="snare", velocity=90) for when in times
    ])
    options = ScoreOptions(meter="3/8", subdivisions=3, first_downbeat_seconds=0.375)
    events, _, _ = _quantize(original, options)
    assert [event.quarter for event in events] == positions
    result = export_score(original, options, tmp_path)
    xml = ET.parse(tmp_path / "score.musicxml")
    assert _voice_durations(xml) == [(Fraction(1, 2), Fraction(1, 2)), (Fraction(3, 2), Fraction(3, 2)), (Fraction(3, 2), Fraction(3, 2))]
    assert [when for _, when, _ in _midi_onsets(tmp_path / "score.mid")] == pytest.approx([when - times[0] for when in times], abs=0.001)
    assert result["metrics"]["score_origin_quarter"] == -0.5
