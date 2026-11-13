"""Loopback web app. Static assets, audio and inference all stay on this machine."""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__
from .audio import ALLOWED_EXTENSIONS, separation_status
from .config import Settings
from .domain import JobOptions, ScoreOptions
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = JobManager(settings)
        yield
        app.state.jobs.shutdown()

    app = FastAPI(
        title="Drum transcription local",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def local_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "DELETE", "PATCH"} and origin:
            if urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(
                    {"detail": "Cross-origin writes are disabled."}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
            "frame-src 'self'; object-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health():
        from .engines import get_engine_status
        from .rhythm import rhythm_status

        return {
            "version": __version__,
            "offline": True,
            "engines": get_engine_status(settings.models_dir),
            "ffmpeg": settings.ffmpeg is not None,
            "musescore": settings.musescore is not None,
            "separation": separation_status(settings.models_dir),
            "rhythm": rhythm_status(settings.models_dir),
        }

    @app.get("/api/jobs")
    def list_jobs(request: Request):
        return {"jobs": request.app.state.jobs.list()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        try:
            return request.app.state.jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, "Không tìm thấy job.") from None

    @app.post("/api/jobs", status_code=202)
    async def create_job(request: Request, file: UploadFile = File(...), options: str = Form(...)):
        from .engines import get_engine_status
        from .rhythm import rhythm_status

        try:
            parsed = JobOptions.model_validate_json(options)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, "Chọn file WAV, MP3, FLAC, OGG, M4A, AAC, AIFF hoặc OPUS.")
        status = next(
            (
                item
                for item in get_engine_status(settings.models_dir)
                if item["id"] == parsed.engine
            ),
            None,
        )
        if not status or not status["ready"]:
            raise HTTPException(409, status["detail"] if status else "Model chưa sẵn sàng.")
        if parsed.rhythm_mode == "auto":
            rhythm = rhythm_status(settings.models_dir)
            if not rhythm["ready"]:
                raise HTTPException(409, rhythm["detail"])
        if settings.ffmpeg is None:
            raise HTTPException(409, "Cần cài FFmpeg trước khi xử lý audio.")
        if parsed.separate_drums and not separation_status(settings.models_dir)["ready"]:
            raise HTTPException(409, "Demucs/checkpoint local chưa sẵn sàng.")
        manager: JobManager = request.app.state.jobs
        try:
            job = manager.create(parsed)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        path = manager.directory(job["id"]) / "input.audio"
        try:
            size = 0
            with path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "File vượt quá giới hạn 200 MB.")
                    destination.write(chunk)
            if size == 0:
                raise HTTPException(422, "File audio rỗng.")
            manager.update(job["id"], source_filename=Path(file.filename or "audio").name)
            manager.enqueue(job["id"])
            return manager.get(job["id"])
        except Exception as exc:
            path.unlink(missing_ok=True)
            manager.update(job["id"], status="failed", stage="Không nhận được file", error=str(exc))
            raise
        finally:
            await file.close()

    @app.post("/api/demo", status_code=202)
    def demo(request: Request):
        manager: JobManager = request.app.state.jobs
        try:
            job = manager.create(
                JobOptions(
                    title="Demo ký âm • 4 ô nhịp",
                    rhythm_mode="manual",
                    bpm=120,
                    meter="4/4",
                    first_downbeat_seconds=0,
                    clip_duration_seconds=8,
                ),
                kind="demo",
            )
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        manager.enqueue(job["id"])
        return job

    @app.post("/api/jobs/{job_id}/rescore", status_code=202)
    def rescore(job_id: str, options: ScoreOptions, request: Request):
        try:
            return request.app.state.jobs.create_rescore(job_id, options)
        except KeyError:
            raise HTTPException(404, "Không tìm thấy job.") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/jobs/{job_id}/events")
    def events(job_id: str, request: Request):
        try:
            return request.app.state.jobs.read_events(job_id).model_dump()
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "Sự kiện chưa có hoặc job không tồn tại.") from None

    @app.get("/api/jobs/{job_id}/files/{filename}")
    def download(job_id: str, filename: str, request: Request):
        manager: JobManager = request.app.state.jobs
        try:
            job = manager.get(job_id)
        except KeyError:
            raise HTTPException(404, "Không tìm thấy job.") from None
        allowed = {url.rsplit("/", 1)[-1] for url in job.get("files", {}).values()}
        if filename not in allowed or filename != Path(filename).name:
            raise HTTPException(404, "Không tìm thấy file kết quả.")
        path = manager.directory(job_id) / filename
        if not path.is_file():
            raise HTTPException(404, "File kết quả không còn trên máy.")
        mime = {
            ".wav": "audio/wav",
            ".mid": "audio/midi",
            ".musicxml": "application/vnd.recordare.musicxml+xml",
            ".pdf": "application/pdf",
            ".json": "application/json",
        }.get(path.suffix)
        return FileResponse(
            path, media_type=mime, filename=filename, content_disposition_type="inline"
        )

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
