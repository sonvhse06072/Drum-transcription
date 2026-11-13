"""Explicit model setup. This module is never used from inference or health checks."""

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path

from drum_transcription.domain import EngineUnavailable

from . import ADT_STR_MODEL_REVISION, ADT_STR_REVISION, ADT_STR_VARIANT, ADTOF_REVISION
from .common import require_file


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _write_manifest(directory: Path, data: dict) -> dict:
    data["prepared_at"] = datetime.now(timezone.utc).isoformat()
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "manifest.json")
    return data


def prepare_model(engine: str, models_dir: Path) -> dict:
    if engine == "adtof":
        return _prepare_adtof(models_dir)
    if engine == "adt_str":
        return _prepare_adt_str(models_dir)
    raise EngineUnavailable(f"Unknown transcription engine: {engine}")


def _prepare_adtof(models_dir: Path) -> dict:
    spec = find_spec("adtof_pytorch")
    if spec is None or spec.origin is None:
        raise EngineUnavailable(
            "Install the pinned 'adtof' extra first: python -m pip install -e '.[adtof]'"
        )
    bundled = require_file(
        Path(spec.origin).parent / "data" / "adtof_frame_rnn_pytorch_weights.pth"
    )
    target = models_dir / "adtof"
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / "weights.pth.tmp"
    shutil.copyfile(bundled, temporary)
    temporary.replace(target / "weights.pth")
    return _write_manifest(
        target,
        {
            "engine": "adtof",
            "source_revision": ADTOF_REVISION,
            "weights_sha256": _sha256(target / "weights.pth"),
            "source": "Installed adtof-pytorch package's bundled pretrained weights",
        },
    )


def _prepare_adt_str(models_dir: Path) -> dict:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise EngineUnavailable(
            "Install the 'adt-str' extra first: python -m pip install -e '.[adt-str]'"
        ) from exc
    if shutil.which("git") is None:
        raise EngineUnavailable("Git is required to prepare pinned ADT_STR source.")
    target = models_dir / "adt_str"
    source = target / "source"
    target.mkdir(parents=True, exist_ok=True)
    if source.exists():
        current = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if current.returncode or current.stdout.strip() != ADT_STR_REVISION:
            raise EngineUnavailable(
                f"Existing ADT_STR source is not at the required revision. Move {source} aside and rerun setup."
            )
    else:
        staging = target / "source-downloading"
        if staging.exists():
            raise EngineUnavailable(
                f"An incomplete setup exists at {staging}. Remove that directory and rerun setup."
            )
        staging.mkdir()
        try:
            for command in [
                ["git", "init", str(staging)],
                [
                    "git",
                    "-C",
                    str(staging),
                    "fetch",
                    "--depth",
                    "1",
                    "https://github.com/pier-maker92/ADT_STR.git",
                    ADT_STR_REVISION,
                ],
                ["git", "-C", str(staging), "checkout", "--detach", "FETCH_HEAD"],
            ]:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=600)
            staging.rename(source)
        except (subprocess.SubprocessError, OSError) as exc:
            raise EngineUnavailable(
                f"Could not prepare ADT_STR source. Incomplete files remain at {staging}: {exc}"
            ) from exc
    snapshot_download(
        repo_id="Pierfrancesco/adt-str",
        revision=ADT_STR_MODEL_REVISION,
        local_dir=str(target / "checkpoint"),
        allow_patterns=[
            f"{ADT_STR_VARIANT}/adt_config.yaml",
            f"{ADT_STR_VARIANT}/config.json",
            f"{ADT_STR_VARIANT}/model.safetensors",
            "README.md",
        ],
    )
    checkpoint = target / "checkpoint" / ADT_STR_VARIANT
    for name in ("adt_config.yaml", "config.json", "model.safetensors"):
        require_file(checkpoint / name)
    return _write_manifest(
        target,
        {
            "engine": "adt_str",
            "source_revision": ADT_STR_REVISION,
            "model_revision": ADT_STR_MODEL_REVISION,
            "variant": ADT_STR_VARIANT,
            "weights_sha256": _sha256(checkpoint / "model.safetensors"),
        },
    )
