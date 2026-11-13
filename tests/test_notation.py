from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

import mido
import pytest
import soundfile as sf

from drum_transcription.domain import DrumHit, ScoreOptions, Transcription
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
    assert [(p, v) for p, _, v in score] == [(38, 110), (46, 70)]
    assert [t for _, t, _ in score] == pytest.approx([0, 0.25], abs=0.001)
    assert result["metrics"]["grid_collisions"] == 1
    assert result["metrics"]["hits_before_downbeat"] == 1
    assert any("collapsed" in warning for warning in result["warnings"])
    assert any("Pickup" in warning for warning in result["warnings"])
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
