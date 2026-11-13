import io
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from drum_transcription.app import create_app
from drum_transcription.config import Settings
from drum_transcription.domain import (
    DrumHit,
    JobOptions,
    RhythmAnalysis,
    RhythmBar,
    Transcription,
)
from drum_transcription.jobs import JobManager


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models")


def completed(client, job_id, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            assert job["status"] == "completed", job.get("error")
            return job
        time.sleep(0.05)
    pytest.fail("Local job did not finish before test deadline")


def test_demo_and_rescore_never_call_ai_and_keep_raw_events(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Demo or rescore must not call an AI engine")

    monkeypatch.setattr("drum_transcription.engines.transcribe", forbidden)
    monkeypatch.setattr("drum_transcription.rhythm.analyze_rhythm", forbidden)
    monkeypatch.setattr(Settings, "musescore", property(lambda self: None))
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/demo")
        assert response.status_code == 202
        first = completed(client, response.json()["id"])
        assert first["kind"] == "demo"
        assert first["metrics"]["hits_count"] > 0
        assert set(first["files"]) >= {
            "musicxml",
            "raw_midi",
            "score_midi",
            "events",
            "preview_audio",
        }
        assert "pdf" not in first["files"]
        events = client.get(first["files"]["events"]).json()
        assert events["metadata"]["accuracy_evaluation"] is False
        assert b"<score-partwise" in client.get(first["files"]["musicxml"]).content
        assert client.get(first["files"]["raw_midi"]).content.startswith(b"MThd")
        assert client.get(first["files"]["preview_audio"]).content.startswith(b"RIFF")

        second_response = client.post(
            f"/api/jobs/{first['id']}/rescore",
            json={
                "title": "Re-scored",
                "bpm": 100,
                "meter": "3/4",
                "subdivisions": 2,
                "first_downbeat_seconds": 0,
            },
        )
        assert second_response.status_code == 202
        second = completed(client, second_response.json()["id"])
        assert second["id"] != first["id"]
        assert second["parent_id"] == first["id"]
        assert second["metrics"]["reused_inference"] is True
        assert client.get(second["files"]["events"]).json() == events
        assert (
            client.get(first["files"]["score_midi"]).content
            != client.get(second["files"]["score_midi"]).content
        )
        assert client.get(f"/api/jobs/{first['id']}/files/job.json").status_code == 404


def test_real_upload_decodes_clip_and_calls_explicit_engine(settings, monkeypatch):
    if settings.ffmpeg is None:
        pytest.skip("FFmpeg is required for the real audio decode integration test")
    calls = []
    monkeypatch.setattr(Settings, "musescore", property(lambda self: None))
    monkeypatch.setattr(
        "drum_transcription.engines.get_engine_status",
        lambda _: [
            {"id": "adtof", "ready": True, "label": "Test adapter", "detail": "", "classes": 5},
        ],
    )

    def test_engine(path, engine, **kwargs):
        info = sf.info(path)
        assert info.samplerate == 44100
        assert info.channels == 2
        assert info.duration == pytest.approx(1.0, abs=0.01)
        calls.append((engine, kwargs["threshold"]))
        return Transcription(
            engine="test-adapter",
            duration_seconds=info.duration,
            hits=[
                DrumHit(time_seconds=0.123, instrument="snare"),
                DrumHit(time_seconds=0.51, instrument="kick"),
            ],
        )

    monkeypatch.setattr("drum_transcription.engines.transcribe", test_engine)
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(32000, dtype=np.float32), 16000, format="WAV")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            data={
                "options": JobOptions(
                    rhythm_mode="manual",
                    bpm=120,
                    meter="4/4",
                    first_downbeat_seconds=0,
                    clip_start_seconds=0.5,
                    clip_duration_seconds=1,
                ).model_dump_json()
            },
            files={"file": ("test.wav", buffer.getvalue(), "audio/wav")},
        )
        assert response.status_code == 202
        job = completed(client, response.json()["id"])
        assert calls == [("adtof", 0.25)]
        events = client.get(job["files"]["events"]).json()
        assert [hit["time_seconds"] for hit in events["hits"]] == [0.123, 0.51]
        assert events["metadata"]["clip_start_seconds"] == 0.5
        assert "source_audio" in job["files"]
        audio = client.get(job["files"]["source_audio"], headers={"Range": "bytes=0-43"})
        assert audio.status_code == 206
        assert audio.content.startswith(b"RIFF")


def test_missing_model_rejected_without_creating_job(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            data={"options": JobOptions().model_dump_json()},
            files={"file": ("song.wav", b"fake", "audio/wav")},
        )
        assert response.status_code == 409
        assert client.get("/api/jobs").json()["jobs"] == []
        assert (
            client.post("/api/demo", headers={"Origin": "https://external.invalid"}).status_code
            == 403
        )


def test_restarted_jobs_mark_interrupted_and_queue_is_bounded(settings):
    manager = JobManager(settings)
    jobs = [manager.create(JobOptions()) for _ in range(settings.max_pending_jobs)]
    with pytest.raises(ValueError, match="Hàng đợi"):
        manager.create(JobOptions())
    manager.shutdown()
    restarted = JobManager(settings)
    try:
        assert all(restarted.get(job["id"])["status"] == "failed" for job in jobs)
        with pytest.raises(KeyError):
            restarted.directory("../../outside")
    finally:
        restarted.shutdown()


def test_static_assets_are_self_contained(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 200
        for path in ("/static/style.css", "/static/app.js"):
            assert client.get(path).status_code == 200
        policy = client.get("/").headers["content-security-policy"]
        assert "connect-src 'self'" in policy


def detected_fractional_rhythm():
    return RhythmAnalysis(
        bpm=120,
        meter="4/4",
        first_downbeat_seconds=1.613,
        beats_seconds=[0.113 + i * 0.5 for i in range(16)],
        downbeats_seconds=[1.613 + i * 2 for i in range(4)],
        beat_positions_quarters=[float(i - 3) for i in range(16)],
    )


def test_automatic_job_uses_fractional_detected_anchor_and_preserves_pickup(settings, monkeypatch):
    if settings.ffmpeg is None:
        pytest.skip("FFmpeg required")
    monkeypatch.setattr(Settings, "musescore", property(lambda self: None))
    monkeypatch.setattr(
        "drum_transcription.engines.get_engine_status",
        lambda _: [
            {"id": "adtof", "ready": True, "label": "Test", "detail": "", "classes": 5},
        ],
    )
    monkeypatch.setattr(
        "drum_transcription.rhythm.rhythm_status",
        lambda _: {"ready": True, "detail": "", "engine": "beat_this"},
    )
    detector_calls, drum_calls = [], []

    def detector(path, models_dir, **kwargs):
        assert sf.info(path).duration == pytest.approx(8)
        assert kwargs["meter_hint"] is None
        detector_calls.append(path)
        return detected_fractional_rhythm()

    def engine(path, name, **kwargs):
        drum_calls.append(name)
        return Transcription(
            engine="test",
            duration_seconds=8,
            hits=[
                DrumHit(time_seconds=0.613, instrument="snare"),
                DrumHit(time_seconds=1.613, instrument="kick"),
                DrumHit(time_seconds=3.613, instrument="kick"),
            ],
        )

    monkeypatch.setattr("drum_transcription.rhythm.analyze_rhythm", detector)
    monkeypatch.setattr("drum_transcription.engines.transcribe", engine)
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(152000, dtype=np.float32), 16000, format="WAV")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            data={
                "options": JobOptions(
                    clip_start_seconds=1.5, clip_duration_seconds=8
                ).model_dump_json()
            },
            files={"file": ("clip.wav", buffer.getvalue(), "audio/wav")},
        )
        assert response.status_code == 202, response.text
        job = completed(client, response.json()["id"])
        assert job["options"]["bpm"] is None
        assert job["options"]["first_downbeat_seconds"] is None
        resolved = job["resolved_score_options"]
        assert resolved["first_downbeat_seconds"] == pytest.approx(1.613)
        assert resolved["bpm"] == 120
        assert job["metrics"]["first_downbeat_original_seconds"] == pytest.approx(3.113)
        assert job["metrics"]["bpm_source"] == "beat_this"
        assert job["metrics"]["score_hit_count"] == 3
        assert job["metrics"]["hits_before_downbeat"] == 1
        assert job["metrics"]["score_origin_quarter"] < 0
        assert client.get(job["files"]["beat_preview"]).content.startswith(b"RIFF")
        assert client.get(job["files"]["rhythm"]).json()["first_downbeat_seconds"] == pytest.approx(
            1.613
        )
        original = client.get(job["files"]["events"]).json()
        assert [hit["time_seconds"] for hit in original["hits"]] == [0.613, 1.613, 3.613]
        second = client.post(f"/api/jobs/{job['id']}/rescore", json={**resolved, "bpm": 118})
        assert second.status_code == 202
        rescored = completed(client, second.json()["id"])
        assert rescored["options"]["rhythm_mode"] == "manual"
        assert rescored["metrics"]["bpm_source"] == "user"
        assert client.get(rescored["files"]["events"]).json() == original
        assert len(detector_calls) == len(drum_calls) == 1


def test_missing_rhythm_model_does_not_silently_use_defaults(settings, monkeypatch):
    monkeypatch.setattr(
        "drum_transcription.engines.get_engine_status",
        lambda _: [
            {"id": "adtof", "ready": True, "detail": ""},
        ],
    )
    monkeypatch.setattr(
        "drum_transcription.rhythm.rhythm_status",
        lambda _: {"ready": False, "detail": "setup-models beat_this"},
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            data={"options": JobOptions().model_dump_json()},
            files={"file": ("clip.mp3", b"fake", "audio/mpeg")},
        )
        assert response.status_code == 409
        assert "beat_this" in response.json()["detail"]
        assert client.get("/api/jobs").json()["jobs"] == []


def test_auto_options_require_no_manual_timing_and_reject_disguised_override():
    defaults = JobOptions()
    assert defaults.rhythm_mode == "auto"
    assert defaults.bpm is defaults.first_downbeat_seconds is None
    with pytest.raises(ValueError, match="Automatic timing"):
        JobOptions(bpm=120, first_downbeat_seconds=0)
    with pytest.raises(ValueError, match="Manual timing"):
        JobOptions(rhythm_mode="manual")


def test_legacy_job_keeps_its_original_manual_timing():
    from drum_transcription.jobs import job_options

    options = job_options(
        {"title": "Old job", "bpm": 95, "meter": "3/4", "first_downbeat_seconds": 1.5}
    )
    assert options.rhythm_mode == "manual"
    assert options.bpm == 95
    assert options.first_downbeat_seconds == 1.5


def test_bar_review_reuses_active_map_and_events_then_can_switch_to_manual(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Reviewing a bar map must not run either AI model again")

    monkeypatch.setattr("drum_transcription.engines.transcribe", forbidden)
    monkeypatch.setattr("drum_transcription.rhythm.analyze_rhythm", forbidden)
    monkeypatch.setattr(Settings, "musescore", property(lambda self: None))
    rhythm = RhythmAnalysis(
        bpm=120,
        meter="4/4",
        first_downbeat_seconds=0.37,
        beats_seconds=[0.12, 0.37, 0.87, 1.37, 1.87, 2.37, 2.87, 3.37],
        downbeats_seconds=[0.37, 2.37],
        beat_positions_quarters=[-0.5, 0, 1, 2, 3, 4, 5, 6],
        bars=[
            RhythmBar(
                start_seconds=0.37, end_seconds=2.37, start_quarter=0, end_quarter=4, meter="4/4"
            ),
            RhythmBar(
                start_seconds=2.37,
                end_seconds=3.87,
                start_quarter=4,
                end_quarter=7,
                meter="3/4",
                source="extrapolated",
            ),
        ],
    )
    events = Transcription(
        engine="annotated-fixture",
        duration_seconds=3.7,
        rhythm=rhythm,
        hits=[DrumHit(time_seconds=when, instrument="kick") for when in (0.12, 0.37, 2.37, 3.37)],
    )
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.jobs
        original = manager.create(JobOptions(clip_start_seconds=1.5, clip_duration_seconds=3.7))
        directory = manager.directory(original["id"])
        (directory / "events.json").write_text(events.model_dump_json(), encoding="utf-8")
        sf.write(directory / "source.wav", np.zeros(59200, dtype=np.float32), 16000)
        manager.update(original["id"], status="completed", rhythm=rhythm.model_dump())

        response = client.post(
            f"/api/jobs/{original['id']}/rhythm-rescore",
            json={
                "title": "Mixed meter",
                "subdivisions": 4,
                "bar_overrides": [{"bar_index": 1, "meter": "7/8", "grouping": [2, 2, 3]}],
            },
        )
        assert response.status_code == 202, response.text
        reviewed = completed(client, response.json()["id"])
        assert reviewed["kind"] == "rhythm_rescore"
        assert reviewed["options"]["rhythm_mode"] == "auto"
        assert reviewed["options"]["bpm"] is None
        assert reviewed["metrics"]["active_rhythm_reused"] is True
        assert reviewed["metrics"]["meter_reviewed"] is True
        assert reviewed["metrics"]["first_downbeat_original_seconds"] == pytest.approx(1.87)
        assert reviewed["metrics"]["score_hit_count"] == 4
        assert [bar["meter"] for bar in reviewed["rhythm"]["bars"]] == ["4/4", "7/8"]
        assert client.get(reviewed["files"]["events"]).json() == events.model_dump()
        assert client.get(reviewed["files"]["rhythm"]).json() == reviewed["rhythm"]
        assert client.get(reviewed["files"]["beat_preview"]).content.startswith(b"RIFF")
        assert manager.get(original["id"])["rhythm"] == rhythm.model_dump()

        again = client.post(
            f"/api/jobs/{reviewed['id']}/rhythm-rescore",
            json={
                "title": "Second review",
                "subdivisions": 4,
                "bar_overrides": [{"bar_index": 0, "meter": "5/4"}],
            },
        )
        second = completed(client, again.json()["id"])
        assert [bar["meter"] for bar in second["rhythm"]["bars"]] == ["5/4", "7/8"]
        assert client.get(second["files"]["events"]).json() == events.model_dump()
        assert second["rhythm"]["bars"][1]["start_quarter"] == 5

        fixed = client.post(
            f"/api/jobs/{second['id']}/rescore",
            json={
                "title": "Fixed timing",
                "bpm": 110,
                "meter": "4/4",
                "subdivisions": 4,
                "first_downbeat_seconds": 0.37,
            },
        )
        assert fixed.status_code == 202
        manual = completed(client, fixed.json()["id"])
        assert manual["options"]["rhythm_mode"] == "manual"
        assert manual["rhythm"] is None
        assert manual["metrics"]["active_rhythm_reused"] is False
        assert client.get(manual["files"]["events"]).json() == events.model_dump()

        count = len(manager.list())
        invalid = client.post(
            f"/api/jobs/{second['id']}/rhythm-rescore",
            json={
                "bar_overrides": [{"bar_index": 20, "meter": "7/8"}],
            },
        )
        assert invalid.status_code == 422
        assert client.post(f"/api/jobs/{manual['id']}/rhythm-rescore", json={}).status_code == 422
        assert len(manager.list()) == count
