# Notation and listening comparison

The prototype produces **editable MusicXML**, a MIDI file retaining original
event timing (`raw.mid`), a quantized MIDI file (`score.mid`), and a synthesized
percussion audition (`preview.wav`). If a local MuseScore executable is available,
it also produces `score.pdf`. No notation service, font CDN or SoundFont download
is used at run time.

## What is musical input, and what is inferred?

- BPM, meter, subdivision and first downbeat are user-confirmed inputs.
- BPM always counts **quarter notes**, including in 6/8, 9/8 and 12/8. A dotted
  quarter pulse of 80 in 6/8 therefore means quarter-note BPM 120 here.
- The first-downbeat field is relative to the **selected audio clip**, not the
  whole uploaded song. Score bar 1 starts at that anchor.
- Events before the anchor remain in raw JSON and raw MIDI. They are omitted from
  the score with a warning; pickup measures are not implemented yet.
- The score uses a constant tempo. Tempo maps, swing interpretation, automatic
  meter changes, sticking and inferred flam/drag notation are future work.
- Durations in the sheet describe rhythm on the selected grid. They do not claim
  to measure how long a drum or cymbal physically rings.
- Raw events are never modified by quantization. Same-instrument events that
  land on the same grid cell collapse in the score only, with a warning and count.
  Listen to `raw.mid` or `preview.wav` to inspect fast strokes before selecting a
  finer subdivision.
- Missing velocity is exported as 100 for audition only. A low or high MIDI
  velocity does not automatically create a ghost note or accent. The exporter
  can represent explicit `ghost`, `accent`, `open`, `closed` and `roll` marks when
  supplied in events, without claiming that the recognition engine detected them.

## Staff convention

The sheet has a five-line percussion staff. Hand parts use voice 1, stems up;
bass drum and pedal hi-hat use voice 2, stems down. Every voice fills its measure
with exact rational durations and rests. Beam grouping follows the supplied time
signature. Grids of three or six subdivisions per quarter produce tuplets.

The display coordinates below describe placement relative to a treble staff;
the generated notes are explicitly **unpitched**.

| Instrument | GM MIDI note | Staff coordinate | Head / extra mark | Voice |
|---|---:|---|---|---:|
| Bass drum | 36 | F4, bottom space | Normal | 2 |
| Snare | 38 | C5, second space from top | Normal | 1 |
| Side stick | 37 | C5 | x | 1 |
| Low / mid / high tom | 43 / 47 / 50 | G4 / D5 / E5 | Normal | 1 |
| Closed hi-hat | 42 | G5, above staff | x | 1 |
| Open hi-hat | 46 | G5 | x plus open circle | 1 |
| Pedal hi-hat | 44 | D4, below staff | x | 2 |
| Crash | 49 | A5 | x | 1 |
| Ride / ride bell | 51 / 53 | F5 | x / diamond | 1 |
| China / splash | 52 / 55 | B5 / E5 | x | 1 |
| Cowbell | 56 | E5 | Triangle | 1 |
| Tambourine | 54 | D5 | x | 1 |

Extended percussion IDs have explicit mappings in `INSTRUMENTS` in
`src/drum_transcription/notation.py`; the result JSON contains a legend for all
instruments used. Grouped model classes retain their grouping: for example,
ADT_STR's grouped hand drums use a conga playback pitch, not a claim that each
conga, timbale or bongo has been distinguished.

## Why there are two MIDI files

`raw.mid` retains original event times relative to the clip start. Its explicit
tempo meta-event simply provides a MIDI timebase; setting it does not turn raw
timestamps into score quantization. `score.mid` begins at score bar 1 and uses the
chosen grid. Both use the General MIDI drum channel (zero-based channel 9).
The short MIDI note gates are audition lengths, not estimated acoustic durations.

`preview.wav` uses a small deterministic synthesizer at original event times. It
helps hear missing, extra or misplaced hits. It is neither the separated audio
stem nor a realistic sample library. Compare it with the original clip or an
actual separated drum stem when judging accuracy.

## MusicXML and PDF implementation notes

music21 creates the rhythm, measures, rests, beams and tuplets. A narrow XML
normalization step assigns each semantic percussion instrument its own
`score-instrument` / `midi-instrument` definition and preserves per-note marks in
chords. This is necessary because music21 9.x groups generic percussion
instruments by Python class. MusicXML `midi-unpitched` values are **GM note + 1**.

PDF conversion runs a local executable with an argument list and a timeout:

```text
MuseScore4 -o score.pdf score.musicxml
```

A missing or failed MuseScore installation does not remove the usable MusicXML
file. Conversion does not use `--force` to ignore score corruption. Native
Windows Python should use the Windows executable; WSL should use a Linux
MuseScore installation to avoid path translation problems.

## Checks

Notation tests cover original timing versus quantized timing, duplicate-hit
collisions, all supported meter/grid combinations, two balanced voices,
percussion channel and pitch mapping, tempo, tuplets, open hats and ghost/accent
marks inside chords, missing-renderer behavior and unknown instrument IDs.

These checks validate export structure. They do not measure AI transcription
accuracy, and MuseScore rendering should still be inspected on the target PC.

The synthetic demo's MusicXML was also rendered and visually inspected with
Verovio 6.4.1 during development: percussion clef, meter, quarter-note tempo,
two voices, rests, beams, ghost parentheses, accents, open hi-hat and tom fill
were visible. Verovio was a temporary QA tool, not an application dependency.
An official MuseScore 4.7.7 AppImage was obtained, but this container could not
start its Qt/GTK interface without a display, even during CLI conversion.
That attempt does **not** constitute MuseScore PDF verification; desktop or
appropriately configured virtual-display execution still needs confirmation.

Reference documentation:

- [music21 percussion objects](https://www.music21.org/music21docs/moduleReference/modulePercussion.html)
- [MusicXML percussion tutorial](https://www.w3.org/2021/06/musicxml40/tutorial/percussion/)
- [MuseScore command-line usage](https://handbook.musescore.org/appendix/command-line-usage)
