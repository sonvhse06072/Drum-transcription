# SheetSage2-Refined beat/bar-head — optional local adapter

**Status:** integrated as an opt-in **experimental meter-aware rhythm detector**.
Beat This! remains the default; this is not an automatic odd-meter accuracy PASS.

The app loads only the frozen encoder and small beat/bar head, **not** the
full SheetSage2 decoder (melody, chords, or sections). The independent upstream
implementation explicitly predicts beat timestamps, downbeats, and per-bar
numerator/denominator; this differs from deriving a 7/8 signature from Beat
This!'s raw beat spacing.

- [Upstream source (pinned)](https://github.com/anime-song/SheetSage2-Refined/tree/6b966e2e495b51024753c29b4bef8fa82935b51a)
- [Model publisher](https://huggingface.co/anime-song/SheetSage2-Refined)
- **Code**: MIT; **model weights**: CC BY-NC 4.0, **non-commercial**. No
  weights or upstream code copied into this repo.
- Hardware: CUDA recommended for the encoder; CPU may be slow and use
  substantial RAM. Actual resource usage requires measurement on the target PC.

## Install/prepare once, while online

In your project's Python 3.11/3.12 virtual environment:

```bash
git pull --ff-only
python -m pip install -e '.[adtof,meter-aware]'
drum-transcription setup-models sheetsage2_refined
drum-transcription doctor
drum-transcription serve
```

For CPU-only installation, install a matching Torch and TorchAudio CPU pair
before installing the optional extras, as documented in the root README.
The existing ADTOF model must be installed separately for drum recognition.

The setup command resolves the publisher's Hugging Face commit SHA **once**
and fetches precisely `config.json`, the required **base encoder SafeTensors**
and the beat/bar head SafeTensors. Manifest records that SHA and artifact hashes.
A re-run with valid files reuses the existing model instead of silently moving
to a newer `main`. To deliberately upgrade, move the previous local model
directory aside, then run setup again.

The **upload/inference path never calls model APIs**, never auto-downloads
weights, and gives the upstream tracker explicit local `weights` and `heads`
paths. The Hugging Face offline flags are set before the upstream import. The
existing MP3 decoding, optional Demucs, drum model, MIDI/MusicXML and sheet
workflow remain local.

## Select the engine

Open the local app at `http://127.0.0.1:8765`.
Under **Tempo & phách đầu**, keep automatic detection and select
**SheetSage2-Refined · nhịp lẻ (thử nghiệm)**. Drum recognition is a separate
setting (ADTOF or ADT_STR).

The result JSON records `engine=sheetsage2_refined`, full model provenance,
the raw predicted beats/downbeats and original per-bar meter predictions.
A separate notation grid maps each bar's *actual* meter to quarter durations,
e.g., 4/4 = 4, 7/8 = 3.5, 5/16 = 1.25. Short bars need not be interpreted
as 5/4 or 7/4. Per-bar grouping (e.g. 2+2+3) is still an engraving *assumption*,
not an audio-model claim.

Beat This! remains available and unchanged as the default. A meter hint
supplied in the app is not silently substituted for the new model's prediction.

## Quality limitations and acceptance

- A model returning valid per-bar meter labels does **not** mean it is accurate
  on Room 34 or any other real recording. That comparison is not yet validated.
- Compare with [Room 34 composer reference](REAL_MUSIC_VALIDATION.md)
  (7 bars: 6/8 → 7/8 → 2/4 → 2/4 → 7/8 → 9/8 → 5/16)
  and stable 3/4/4/4 control recordings *without* passing reference meters
  into model inference. Record exact bar counts and meter labels, plus timing
  error only where genuine downbeat timestamps are available.
- Run the same audio through both rhythm engines and compare sheet editing time,
  meter sequence, and per-bar beat grid. For real odd meters, prioritize
  missing/extra bars and denominator confusions.
- Source controls, float time offsets and original drum hits are preserved.
  Suspect bars are explicitly estimated; manual corrections are separate.
- MPS is not currently validated for this adapter; use CPU or CUDA.

See [Issue #2](https://github.com/sonvhse06072/Drum-transcription/issues/2).

## Real Song Validation — Room 34, 14/11/2026

The optional model has now been **executed with its actual official weights**
on the same CC0 Room 34 OGG used to test Beat This!. On GitHub-hosted Linux,
the explicit model setup succeeded; the test ran on CPU with Python socket and
DNS calls blocked during inference (**zero network attempts**).

- [Actual model run and JSON artifact](https://github.com/sonvhse06072/Drum-transcription/actions/runs/43504035486).
- [Machine-readable baseline](validation/room34-sheetsage2.json).
- Audio SHA-256: `93b965383f397943d7526c7a150f3beff80ca42b544432659889a99f4defa4eb`.
- HF model commit: `5e9c494941b8c0bb322d9f17f365a6f886d5e562`.
- Reference: 7 bars `6/8 → 7/8 → 2/4 → 2/4 → 7/8 → 9/8 → 5/16`.
- Model: **8** bars, predicted `7/4 → 4/4 → 4/4 → 7/4 → 7/4 → 4/4 → 4/4 → 1/4`.
- First predicted downbeat: **2.027 s**; representative BPM **179.75**.
- Composer sequence exact match: **FALSE**. No ground-truth downbeat timestamp
  annotations are available; do not report a timing precision or recall score.

The result is **offline inference PASS; odd/mixed-meter quality FAIL**.
Eight bars is numerically closer to seven than Beat This!'s 12 postprocessed bars
but still a musically wrong transcription. Neither model should become the
default for difficult mixed meter without further evaluation. Keep the manual
boundary review as a fallback and preserve model provenance.
