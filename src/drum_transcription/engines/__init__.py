"""Local inference adapters; importing this module never downloads model files."""

import importlib.util
from pathlib import Path

from drum_transcription.domain import EngineUnavailable, Transcription

ADTOF_REVISION = "85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9"
ADT_STR_REVISION = "77dbef225e7029478ebfb916f20c7a00274f0f12"
ADT_STR_MODEL_REVISION = "a33c5c6b191a4ca1e0f6dc22140947485eb36ce8"
ADT_STR_VARIANT = "setting-tau-0.8"


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def get_engine_status(models_dir: Path) -> list[dict]:
    """Check local files/dependencies only; readiness does not claim model accuracy."""
    models_dir = Path(models_dir)
    specs = [
        (
            "adtof",
            "ADTOF · 5 groups",
            5,
            ["torch", "adtof_pytorch", "librosa", "pretty_midi"],
            [models_dir / "adtof" / "weights.pth"],
        ),
        (
            "adt_str",
            "ADT_STR · extended kit",
            26,
            [
                "torch",
                "torchaudio",
                "transformers",
                "safetensors",
                "omegaconf",
                "huggingface_hub",
                "pretty_midi",
            ],
            [
                models_dir / "adt_str" / "source" / "adt_transcriber.py",
                models_dir / "adt_str" / "checkpoint" / ADT_STR_VARIANT / "adt_config.yaml",
                models_dir / "adt_str" / "checkpoint" / ADT_STR_VARIANT / "config.json",
                models_dir / "adt_str" / "checkpoint" / ADT_STR_VARIANT / "model.safetensors",
            ],
        ),
    ]
    result = []
    for engine, label, classes, modules, files in specs:
        missing_modules = [name for name in modules if not _has_module(name)]
        missing_files = [p.name for p in files if not p.is_file() or p.stat().st_size == 0]
        missing = []
        if missing_modules:
            missing.append("Missing dependencies: " + ", ".join(missing_modules))
        if missing_files:
            missing.append("Missing local model files: " + ", ".join(missing_files))
        result.append(
            {
                "id": engine,
                "label": label,
                "classes": classes,
                "ready": not missing,
                "detail": "; ".join(missing)
                if missing
                else "Local files found; run a short clip to verify inference.",
            }
        )
    return result


def transcribe(
    audio_path: Path,
    engine: str,
    *,
    device: str = "auto",
    threshold: float = 0.25,
    models_dir: Path,
) -> Transcription:
    """Run an explicit local engine. There is no model download or cloud fallback."""
    if engine not in {"adtof", "adt_str"}:
        raise EngineUnavailable(f"Unknown transcription engine: {engine}")
    state = next(item for item in get_engine_status(models_dir) if item["id"] == engine)
    if not state["ready"]:
        extra = "adt-str" if engine == "adt_str" else engine
        raise EngineUnavailable(
            f"{state['detail']}. Install the '{extra}' extra, then run "
            f"drum-transcription setup-models {engine} --models-dir {Path(models_dir)}."
        )
    if not Path(audio_path).is_file():
        raise FileNotFoundError(audio_path)
    if engine == "adtof":
        from .adtof import transcribe_adtof

        return transcribe_adtof(
            Path(audio_path), device=device, threshold=threshold, models_dir=Path(models_dir)
        )
    from .adt_str import transcribe_adt_str

    return transcribe_adt_str(Path(audio_path), device=device, models_dir=Path(models_dir))


def prepare_model(engine: str, models_dir: Path) -> dict:
    """Explicit setup operation: the ADT_STR branch downloads source and weights."""
    from .prepare import prepare_model as prepare

    return prepare(engine, Path(models_dir))
