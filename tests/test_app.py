import io
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from drum_transcription.app import create_app
from drum_transcription.config import Settings
from drum_transcription.domain import DrumHit, JobOptions, Transcription
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
                    clip_start_seconds=0.5, clip_duration_seconds=1
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
