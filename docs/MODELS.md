# Local models

The web app never installs packages, downloads weights, uploads audio or calls an
inference API. Download dependencies/models explicitly during installation, then
disconnect the network and run a short clip to verify the local environment.

`ready` in the UI means required files and Python modules are present. It does not
mean the engine has passed an inference test, nor does it measure transcription
accuracy. Missing weights fail the job; there is no random-model or cloud fallback.

## ADTOF: start here

Install Python 3.11 or 3.12, Git and FFmpeg. Activate the app's virtual environment.
Install a PyTorch build appropriate for CPU/CUDA first if you want to choose its
download size/device support. Then:

```bash
python -m pip install -e '.[adtof]'
drum-transcription setup-models adtof --models-dir ./models
```

The dependency is pinned to
[`xavriley/ADTOF-pytorch` at `85c192e…`](https://github.com/xavriley/ADTOF-pytorch/tree/85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9).
Its bundled pretrained checkpoint is copied to `models/adtof/weights.pth`. Setup
records the SHA-256 digest in `models/adtof/manifest.json`. There is no additional
weight download during inference. The adapter loads parameters with `strict=True`
because the upstream convenience function otherwise permits absent weights and
partial/random initialization.

ADTOF predicts only **five groups**: kick, snare, toms, hi-hats and cymbals. This
prototype displays the three grouped classes as `tom_mid`, `hihat_closed` and
`crash`; those specific subtypes have **not** been detected. It cannot establish
the quality of full-kit articulation recognition. It provides frame activations,
not calibrated confidence or playing velocity. The raw result keeps velocity
unset; any constant playback velocity belongs to the export layer.

The threshold control applies to ADTOF's peak picker. A useful initial uniform
threshold is 0.25; the upstream per-class defaults range from 0.22 to 0.32. Lower
values can add false positives and higher values can omit quiet hits. Compare on
the same audio segment rather than assuming one threshold works for all music.

`auto` uses CUDA when available and otherwise CPU. This pinned ADTOF wrapper does
not advertise MPS support. Selecting an unavailable explicit device fails instead
of silently changing the benchmark device.

## ADT_STR: detailed comparison

This adapter has passed the local checkpoint load/inference smoke test described
below. Quality on real mixed music and detailed drum techniques still needs a
representative benchmark.

This engine is heavier. Start with a 10–30-second clip, especially on CPU:

```bash
# CPU only: install matching CPU builds before the extra.
python -m pip install 'torch==2.8.0' 'torchaudio==2.8.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[adt-str]'
drum-transcription setup-models adt_str --models-dir ./models
```

For NVIDIA GPU use matching Torch/TorchAudio 2.8.0 CUDA builds instead of the CPU
index command. Mixing a CPU Torch installation with a CUDA TorchAudio wheel can
fail to import due to missing shared libraries; the two packages must use the
same device build.

Explicit setup downloads:

| Item | Pinned version | Local destination |
| --- | --- | --- |
| [ADT_STR source](https://github.com/pier-maker92/ADT_STR/tree/77dbef225e7029478ebfb916f20c7a00274f0f12) | `77dbef225e7029478ebfb916f20c7a00274f0f12` | `models/adt_str/source` |
| [Official checkpoint](https://huggingface.co/Pierfrancesco/adt-str/tree/a33c5c6b191a4ca1e0f6dc22140947485eb36ce8) | `a33c5c6b191a4ca1e0f6dc22140947485eb36ce8`, `setting-tau-0.8` | `models/adt_str/checkpoint/setting-tau-0.8` |

Only the selected variant's config/weights and model README are downloaded, not
all three checkpoints. The app uses a child process because upstream imports
generic top-level names such as `model` and `utils`. It reads local JSON/YAML and
SafeTensors directly, loads all parameters strictly, sets Hugging Face offline
flags, and releases its model memory when the process exits. Audio is decoded
with the app's SoundFile dependency, so the upstream TorchCodec audio reader is
not required. The worker's waveform helper is tied to the pinned source revision.

The initial adapter uses batch size 1. CUDA, MPS and CPU selection follow device
availability. CPU inference can be slow; no runtime/VRAM guarantee is implied.

### Vocabulary mapping

Upstream outputs a custom 35–60 vocabulary. Writing these numbers directly as
General MIDI causes incorrect instruments: custom 44 is **open hi-hat**, custom
46 is **crash**, and custom 47 is **high tom**. This app converts to semantic names
before notation/MIDI export and also keeps the original rows in result metadata.

| Custom IDs | Internal instrument |
| --- | --- |
| 35, 36 | kick |
| 37 | side_stick |
| 38, 40 | snare |
| 39 | clap |
| 41, 45, 47 | tom_low, tom_mid, tom_high |
| 42, 43, 44 | hihat_closed, hihat_pedal, hihat_open |
| 46, 48, 49, 51 | crash, ride, china, splash |
| 50, 52, 53 | tambourine, cowbell, vibraslap |
| 54–60 | conga, shaker, whistle, guiro, claves, cuica, triangle |

The upstream model groups ride bell with ride; class 54 combines
bongos/congas/timbales. This adapter also merges bass-drum and snare variants for
drum-kit notation. A 26-class model therefore does not yield 26 uniquely named
staff instruments, nor all playing techniques. Predicted velocity remains an
estimate; the app does not label quiet predictions as ghost notes automatically.
The ADTOF threshold does not affect ADT_STR's greedy token decoding. Inspect
2.56-second chunk boundaries and fills when evaluating quality.

## What is not included in the source repository

Weights, downloaded upstream source, input audio and generated output are ignored
by Git. A fresh checkout needs the install/setup steps above. A manifest records
what was prepared; it is provenance for your experiment, not an accuracy score.

Source and model licensing must be considered separately. The ADTOF PyTorch port
does not include a clear standalone LICENSE at the pinned revision; its original
[ADTOF model](https://github.com/MZehren/ADTOF) is published with
CC-BY-NC-SA-4.0 terms. ADT_STR's repository declares CC-BY-SA-4.0; check the model
publisher's terms for the downloaded weights. This project does not relicense,
embed or redistribute either checkpoint. Review upstream permissions before
commercial distribution.

## Checking the offline claim

1. Complete package/model setup while online.
2. Disconnect networking or block outbound access for the Python process.
3. Launch the local app and transcribe a short WAV on each installed engine.
4. Confirm original audio, events, MIDI and MusicXML are produced locally.
5. If PDF export is requested, ensure a local MuseScore executable is available.

### Execution checks completed on 2026-11-13

Both adapters were exercised on synthesized percussion, using Linux, Python 3.12
and CPU inference with **two Torch threads**. These checks verify that real model
weights load and execute; they are **not accuracy benchmarks** and are not timing
promises for another PC. The two input clips have different durations, so the
numbers below are not a controlled speed comparison.

| Engine | Input duration | Elapsed | Finite, in-range output hits | Network attempts |
| --- | --- | --- | --- | --- |
| ADTOF, uniform threshold 0.25 | 8.0 seconds | 3.169 seconds | 46 | 0 |
| ADT_STR, tau 0.8, batch size 1 | 2.56 seconds | 14.866 seconds | 15 | 0 |

The ADT_STR elapsed time includes child-process startup and loading its model.
Its test used PyTorch/TorchAudio `2.8.0+cpu`, Transformers `4.57.6`, Hugging Face
Hub `0.36.2`, SafeTensors `0.8.0` and OmegaConf `2.4.0`. No dependency-version change
was needed to run the adapter with this checkpoint.

During inference, socket connections were patched to raise; the ADT_STR child
process inherited the same block through a temporary `sitecustomize.py`, with
DNS resolution also blocked. Setup/download had already completed. No attempted
connections occurred. The temporary test hooks are not part of the application.

Observed checkpoint SHA-256 digests:

```text
ADTOF:   1bc986e596ec47ba0b44916f87cd4a39f0b2bec23596df3fb5d0e87749217320
ADT_STR: f94d1773d929166e0a3f63d305975aa6667512f8a71d7706872e1f8b23ca930b
```

## Optional Demucs separation

Install the project's `demucs` extra and explicitly prepare the local checkpoint:

```bash
python -m pip install -e '.[demucs]'
drum-transcription setup-models demucs --models-dir ./models
```

Enable drum separation when comparing a full mix with its isolated drum stem.
Separation is another local model stage and may remove quiet hits or introduce
artifacts. Evaluate both paths on the same clips. The Demucs code is MIT, while
the author's current README says the pretrained weights' licensing is unclear;
do not assume the checkpoint inherits the code's license. See
[the maintained author repository](https://github.com/adefossez/demucs).

The tests cover missing-file failure, strict-loading calls, semantic mapping and
network-free readiness. A true quality benchmark requires representative audio
with manually checked drum events; synthetic demo output is not that benchmark.
