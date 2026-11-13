"""Isolate upstream's generic module names and model memory in a child process."""

import argparse
import json
import os
import sys
from pathlib import Path

from .common import require_file, select_device


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("audio", "source", "checkpoint", "output", "device"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    source = Path(args.source)
    checkpoint = Path(args.checkpoint)
    require_file(source / "adt_transcriber.py")
    for name in ("config.json", "adt_config.yaml", "model.safetensors"):
        require_file(checkpoint / name)
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "WANDB_DISABLED": "true",
        }
    )
    sys.path.insert(0, str(source))

    import numpy as np
    import soundfile as sf
    import torch
    from adt_transcriber import ADTTranscriber
    from config import ADTModelConfig
    from model import ADTModel
    from safetensors.torch import load_file
    from utils.config_utils import load_config

    device = select_device(torch, args.device)
    cfg = load_config(str(checkpoint / "adt_config.yaml"))
    if cfg.get("tokenizer", {}).get("ADTOF_mapping", False):
        raise ValueError(
            "Expected the full-vocabulary ADT_STR checkpoint, not the reduced ADTOF mapping."
        )
    # Construct directly from local JSON and load every parameter strictly. This
    # avoids from_pretrained's download path and partial-initialization fallback.
    model_config = ADTModelConfig(
        **json.loads((checkpoint / "config.json").read_text(encoding="utf-8"))
    )
    model = ADTModel(model_config)
    model.load_state_dict(load_file(str(checkpoint / "model.safetensors")), strict=True)
    transcriber = ADTTranscriber(model, cfg, device=device)
    audio, rate = sf.read(args.audio, dtype="float32", always_2d=True)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Audio is empty or contains non-finite samples.")
    waveform = transcriber._prepare_waveform(torch.from_numpy(audio.T.copy()), rate)
    # Batch size one lowers initial VRAM requirements. The inference helper and
    # private waveform API are pinned to ADT_STR_REVISION in the parent adapter.
    with torch.inference_mode():
        notes = transcriber._transcribe_waveform(waveform, batch_size=1).cpu().tolist()
    output = {
        "notes": notes,
        "duration_seconds": len(audio) / rate,
        "velocity_available": bool(cfg.get("tokenizer", {}).get("add_velocity", False)),
        "device": device,
    }
    Path(args.output).write_text(json.dumps(output, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
