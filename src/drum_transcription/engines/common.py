"""Shared model preflight checks, deliberately separate from download code."""

from pathlib import Path

from drum_transcription.domain import EngineUnavailable


def select_device(torch, requested: str, *, allow_mps: bool = True) -> str:
    if requested not in {"auto", "cpu", "cuda", "mps"}:
        raise EngineUnavailable(f"Unsupported device: {requested}")
    cuda = bool(torch.cuda.is_available())
    mps_backend = getattr(torch.backends, "mps", None)
    mps = bool(allow_mps and mps_backend and mps_backend.is_available())
    if requested == "auto":
        return "cuda" if cuda else "mps" if mps else "cpu"
    if requested == "cuda" and not cuda:
        raise EngineUnavailable(
            "CUDA was selected but is unavailable. Select CPU or install matching PyTorch/CUDA."
        )
    if requested == "mps" and not mps:
        raise EngineUnavailable("MPS is unavailable for this engine/device. Select CPU or Auto.")
    return requested


def require_file(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise EngineUnavailable(f"Required local model file is missing or empty: {path}")
    return path
