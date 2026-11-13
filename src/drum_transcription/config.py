"""Local paths and executable discovery; no model is downloaded here."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def find_executable(configured: str | None, candidates: tuple[str, ...]) -> str | None:
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() else shutil.which(configured)
    for name in candidates:
        if found := shutil.which(name):
            return found
    return None


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    models_dir: Path
    max_upload_bytes: int = 200 * 1024 * 1024
    max_pending_jobs: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DRUM_DATA_DIR", "data")).expanduser().resolve(),
            models_dir=Path(os.environ.get("DRUM_MODELS_DIR", "models")).expanduser().resolve(),
        )

    @property
    def ffmpeg(self) -> str | None:
        return find_executable(os.environ.get("FFMPEG_BIN"), ("ffmpeg",))

    @property
    def musescore(self) -> str | None:
        return find_executable(
            os.environ.get("MUSESCORE_BIN"),
            ("MuseScore4.exe", "MuseScore4", "mscore", "musescore", "musescore4", "musescore3"),
        )
