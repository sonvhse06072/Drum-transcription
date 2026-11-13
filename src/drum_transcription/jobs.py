"""Single-worker local jobs, durable results, and re-scoring without re-inference."""

import json
import logging
import shutil
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .audio import decode_clip, separate_drums
from .config import Settings
from .domain import DrumHit, JobOptions, ScoreOptions, Transcription

LOGGER = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def demo_transcription() -> Transcription:
    """Known events for inspecting export, never presented as detected audio."""
    hits: list[DrumHit] = []
    for eighth in range(32):
        if eighth == 23:  # The open hi-hat below replaces this closed stroke.
            continue
        hits.append(DrumHit(time_seconds=eighth * 0.25, instrument="hihat_closed", velocity=70))
    for bar in range(4):
        for beat in (0, 2):
            hits.append(DrumHit(time_seconds=bar * 2 + beat * 0.5, instrument="kick", velocity=100))
        for beat in (1, 3):
            hits.append(
                DrumHit(
                    time_seconds=bar * 2 + beat * 0.5,
                    instrument="snare",
                    velocity=105,
                    articulations=["accent"],
                )
            )
    hits += [
        DrumHit(time_seconds=0, instrument="crash", velocity=100),
        DrumHit(time_seconds=3.25, instrument="snare", velocity=38, articulations=["ghost"]),
        DrumHit(time_seconds=5.75, instrument="hihat_open", velocity=75),
        DrumHit(time_seconds=7.0, instrument="tom_high", velocity=90),
        DrumHit(time_seconds=7.25, instrument="tom_mid", velocity=95),
        DrumHit(time_seconds=7.5, instrument="tom_low", velocity=100),
    ]
    return Transcription(
        engine="demo",
        duration_seconds=8,
        hits=sorted(hits, key=lambda hit: hit.time_seconds),
        warnings=[
            "DEMO: sự kiện trống được tạo sẵn để kiểm tra ký âm. Đây không phải kết quả nhận diện AI và không dùng đo độ chính xác."
        ],
        metadata={"synthetic": True, "accuracy_evaluation": False},
    )


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drum-job")
        self.jobs: dict[str, dict] = {}
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job["id"] != path.parent.name or len(job["id"]) != 32:
                    continue
                self.jobs[job["id"]] = job
                if job["status"] in {"queued", "running"}:
                    self.update(
                        job["id"],
                        status="failed",
                        error="Phiên xử lý trước đã dừng. Hãy tạo lại job.",
                        stage="Đã dừng",
                    )
            except (OSError, ValueError, KeyError):
                LOGGER.warning("Ignoring unreadable job file %s", path)

    def directory(self, job_id: str) -> Path:
        if not (len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id)):
            raise KeyError(job_id)
        return self.root / job_id

    def get(self, job_id: str) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.jobs[job_id]))

    def list(self) -> list[dict]:
        with self.lock:
            return [
                self.get(job_id)
                for job_id in sorted(
                    self.jobs, key=lambda key: self.jobs[key]["created_at"], reverse=True
                )[:50]
            ]

    def _save(self, job: dict) -> None:
        directory = self.directory(job["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "job.json.tmp"
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        temporary.replace(directory / "job.json")

    def update(self, job_id: str, **fields) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            job.update(fields, updated_at=now())
            self._save(job)
            return self.get(job_id)

    def create(
        self, options: JobOptions, *, kind: str = "transcription", parent_id: str | None = None
    ) -> dict:
        with self.lock:
            pending = sum(job["status"] in {"queued", "running"} for job in self.jobs.values())
            if pending >= self.settings.max_pending_jobs:
                raise ValueError("Hàng đợi đã đầy. Đợi một job hoàn tất trước khi thêm audio.")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "kind": kind,
                "parent_id": parent_id,
                "status": "queued",
                "stage": "Đang chờ",
                "progress": 0,
                "created_at": now(),
                "updated_at": now(),
                "options": options.model_dump(),
                "warnings": [],
                "files": {},
                "metrics": {},
                "error": None,
            }
            self.jobs[job_id] = job
            self._save(job)
            return self.get(job_id)

    def enqueue(self, job_id: str) -> None:
        self.executor.submit(self._run, job_id)

    def create_rescore(self, source_id: str, options: ScoreOptions) -> dict:
        previous = self.get(source_id)
        if previous["status"] != "completed":
            raise ValueError("Chỉ có thể xuất lại một job đã hoàn tất.")
        transcription = self.read_events(source_id)
        if options.first_downbeat_seconds >= transcription.duration_seconds:
            raise ValueError("Phách đầu tiên nằm ngoài audio thực tế.")
        merged = JobOptions.model_validate({**previous["options"], **options.model_dump()})
        new = self.create(merged, kind=previous["kind"], parent_id=source_id)
        try:
            for name in ("events.json", "source.wav", "drums.wav"):
                source = self.directory(source_id) / name
                if source.is_file():
                    shutil.copyfile(source, self.directory(new["id"]) / name)
            self.enqueue(new["id"])
        except Exception:
            self.update(
                new["id"], status="failed", error="Không sao chép được kết quả để xuất lại."
            )
            raise
        return new

    def read_events(self, job_id: str) -> Transcription:
        self.get(job_id)
        return Transcription.model_validate_json(
            (self.directory(job_id) / "events.json").read_text(encoding="utf-8")
        )

    def _run(self, job_id: str) -> None:
        start = time.monotonic()
        try:
            from .engines import transcribe
            from .notation import export_score

            job = self.get(job_id)
            directory = self.directory(job_id)
            options = JobOptions.model_validate(job["options"])
            self.update(job_id, status="running", stage="Chuẩn bị audio", progress=5)
            files = {}
            if job["parent_id"]:
                transcription = self.read_events(job_id)
                self.update(job_id, stage="Dùng lại sự kiện gốc", progress=70)
            elif job["kind"] == "demo":
                transcription = demo_transcription()
            else:
                source = directory / "input.audio"
                duration = decode_clip(
                    source, directory / "source.wav", options, self.settings.ffmpeg
                )
                inference_audio = directory / "source.wav"
                if options.separate_drums:
                    self.update(job_id, stage="Tách trống bằng Demucs local", progress=20)
                    inference_audio = separate_drums(
                        inference_audio, directory, self.settings.models_dir, options.device
                    )
                self.update(job_id, stage=f"Nhận diện bằng {options.engine} local", progress=40)
                transcription = transcribe(
                    inference_audio,
                    options.engine,
                    device=options.device,
                    threshold=options.threshold,
                    models_dir=self.settings.models_dir,
                )
                transcription.duration_seconds = duration
                transcription.metadata.update(
                    {
                        "clip_start_seconds": options.clip_start_seconds,
                        "time_reference": "seconds relative to decoded source.wav clip, not original full song",
                        "separate_drums": options.separate_drums,
                        "options": options.model_dump(),
                    }
                )
                if not options.separate_drums:
                    transcription.warnings.append(
                        "Đang nhận diện trực tiếp trên audio đầu vào. Có thể so sánh với drum stem/tách trống để kiểm chứng chất lượng."
                    )
            (directory / "events.json").write_text(
                transcription.model_dump_json(indent=2), encoding="utf-8"
            )
            self.update(job_id, stage="Căn nhịp và xuất MusicXML / MIDI", progress=80)
            score_options = ScoreOptions.model_validate(
                options.model_dump(include=set(ScoreOptions.model_fields))
            )
            exported = export_score(
                transcription, score_options, directory, self.settings.musescore
            )
            for key, filename in exported.get("files", {}).items():
                name = Path(filename).name
                if (directory / name).is_file():
                    files[key] = f"/api/jobs/{job_id}/files/{name}"
            for key, name in (
                ("events", "events.json"),
                ("source_audio", "source.wav"),
                ("drums_audio", "drums.wav"),
            ):
                if (directory / name).is_file():
                    files[key] = f"/api/jobs/{job_id}/files/{name}"
            warnings = list(dict.fromkeys(transcription.warnings + exported.get("warnings", [])))
            if not transcription.hits:
                warnings.append(
                    "Không tìm thấy cú đánh. Bản sheet trống không chứng minh audio không có trống; hãy kiểm tra ngưỡng hoặc model."
                )
            metrics = {
                **exported.get("metrics", {}),
                "hits_count": len(transcription.hits),
                "counts_by_instrument": dict(Counter(hit.instrument for hit in transcription.hits)),
                "duration_seconds": transcription.duration_seconds,
                "elapsed_seconds": round(time.monotonic() - start, 2),
                "engine": transcription.engine,
                "reused_inference": bool(job["parent_id"]),
            }
            self.update(
                job_id,
                status="completed",
                stage="Hoàn tất",
                progress=100,
                files=files,
                warnings=warnings,
                metrics=metrics,
            )
        except Exception as exc:
            LOGGER.exception("Job %s failed", job_id)
            self.update(
                job_id, status="failed", stage="Không hoàn tất", error=str(exc)[:1500], progress=0
            )

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
