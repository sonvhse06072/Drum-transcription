# Notation and listening comparison

The prototype produces **editable MusicXML**, a MIDI file retaining original
event timing (`raw.mid`), a quantized MIDI file (`score.mid`), and a synthesized
percussion audition (`preview.wav`). If a local MuseScore executable is available,
it also produces `score.pdf`. No notation service, font CDN or SoundFont download
is used at run time.

## What is musical input, and what is inferred?

- Automatic jobs use locally detected beat/downbeat times and estimated tempo.
  A local bar map and selected subdivision determine engraving. Manual timing
  remains an optional override; it is not required for the normal upload flow.
- BPM always counts **quarter notes**, including in 6/8, 9/8 and 12/8. A dotted
  quarter pulse of 80 in 6/8 therefore means quarter-note BPM 120 here.
- The first downbeat is relative to the **selected audio clip**, not the whole
  uploaded song. A clip cut at original-file time 1.5 seconds may have its first
  detected downbeat at 0.37 seconds in the clip. Those timestamps stay separate;
  the exporter never rounds either to an integer second or adds the file offset
  to clip-local event times.
- For automatic jobs, piecewise interpolation between detected beats maps each
  event time to its musical position. Unequal detected beat intervals therefore
  do not accumulate the error of one global BPM grid. The nearest beat interval
  is extrapolated to clip edges, with a warning for hits outside the detected range.
- Events before the first downbeat are retained in a pickup or preceding
  measures. A pickup begins at the beat boundary at/before its first quantized
  event, with rests as needed, and is encoded as an implicit measure 0. If the
  first known downbeat occurs after several measures, those earlier measures
  remain present and the result reports which measure contains the anchor.
  Leading silence with no earlier hits does not create a silent pickup.
- Each bar can have its own time signature and grouping: for example 4/4 → 3/4,
  or 5/8 grouped 2+3 followed by 7/8 grouped 2+2+3. The sheet inserts a new
  signature at the affected bar and uses that bar's group boundaries for rests
  and beams. An unequal 2+2+3 pulse is represented by unequal **musical** beat
  lengths; it does not automatically become a spurious tempo change.
- The supported notation range is 1–32 numerator over 2, 4, 8 or 16, including
  11/8 and 13/16. This is an **export capability**, not an accuracy claim that
  the audio model recognizes every supported signature. Automatically proposed
  bars remain estimates. Meter/beat-group review can retain all original
  timestamps and re-export without running the drum model again.
- `score.mid` and MusicXML playback now contain a **local tempo map** from the
  beat-grid slopes. Steady passages have one tempo; changing passages preserve
  the corresponding time between quantized beat positions. The printed opening
  tempo is a local estimate. Further visible marks require a sustained change
  of at least 8% and 4 BPM with agreeing adjacent intervals. Smaller timing
  fluctuations use hidden MusicXML metronome marks. The whole-clip BPM summary
  can therefore differ from the first printed tempo. Raw MIDI/audio retain
  original event times. Swing interpretation, sticking and inferred flam/drag
  notation remain future work.
- The first and last incomplete audio bars use the available local meter. A
  written pickup is shortened; the last bar is completed with rests. Bar
  boundaries outside the observed audio must be treated as extrapolated, not
  observed downbeats. A previously saved result without a bar map can still
  export using its legacy single meter and detected beat grid.
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
bass drum and pedal hi-hat use voice 2, stems down. Both voices fill every full
measure, or the same shortened pickup, with exact rational durations and rests.
Beat-aligned rest grouping avoids filling pickups with arbitrary small fragments.
Beam grouping follows each bar's supplied groups; unequal groups also receive a
small label such as `2+2+3`. Without explicit groups, compound meters use groups
of three; odd eighth/sixteenth meters use a conservative grouping of twos followed
by three. That default is a reading aid and can need correction for the music.
Grids of three or six subdivisions per quarter produce tuplets.
Subdivision grids restart at each local barline. Barlines remain exact targets
even when the bar length is not an integer number of grid cells, such as 3/8
or 13/16 with triplets. The final partial grid cell is shortened at the barline;
the next downbeat is never moved to a global thirds/sixths grid. This applies to
both automatic and manual timing, including pickups.

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
timestamps into score quantization. `score.mid` begins at the start of the written
score, including a pickup when present, and follows its local tempo map. For
manual single-tempo jobs it continues to use the chosen constant BPM.
The detected downbeat can therefore occur after time zero in score MIDI. Result
metrics include `score_origin_quarter`, `pickup_quarters`, `preceding_measures`
and `detected_downbeat_measure` to make that shift explicit. Both MIDI files use
the General MIDI drum channel (zero-based channel 9).
The short MIDI note gates are audition lengths, not estimated acoustic durations.
`bpm_initial`, `bpm_min`, `bpm_max`, `tempo_changes` and `meter_changes` expose
the score's tempo labels and signature/grouping changes. MIDI time-signature
events carry numerator/denominator; additive group labels belong to MusicXML.

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
The same normalization explicitly writes `print-object="no"` on playback-only
metronome elements, because music21 does not export that style flag itself.
Tempo sound events stay present even when their visual marks are hidden.

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
collisions, the original meter/grid matrix and targeted extended signatures, two balanced voices,
percussion channel and pitch mapping, tempo, tuplets, open hats and ghost/accent
marks inside chords, missing-renderer behavior and unknown instrument IDs.
Additional regressions cover 0.37/1.5-second anchors, fractional source-clip
offsets, time-shift invariance, retained pickup notes, preceding measures,
compound-meter pickups, edge extrapolation and unequal detected beat intervals.
Variable-map regressions cover hits on both sides of a 4/4 → 3/4 boundary,
same-meter changes between 3+2+2 and 2+2+3 beams, mixed 5/8 → 7/8 → 13/16
with a pickup, a sustained 120 → 90 BPM change, hidden playback tempos and
unequal grouped pulses at a constant quarter-note BPM. Score MIDI is checked
against the annotated performance timing, independently of raw MIDI.
Additional targeted cases verify exact 3/8 and 13/16 downbeats with triplet
grids, retained pickup timing, and no unnecessary extra bar after a shortened
final grid cell.

These checks validate export structure. They do not measure AI transcription
accuracy, and MuseScore rendering should still be inspected on the target PC.

The synthetic demo's MusicXML was also rendered and visually inspected with
Verovio 6.4.1 during development: percussion clef, meter, quarter-note tempo,
two voices, rests, beams, ghost parentheses, accents, open hi-hat and tom fill
were visible. Verovio was a temporary QA tool, not an application dependency.
The automatic-grid update was also checked with a three-beat pickup before a
downbeat at 1.5 seconds: all pickup notes, both voices, their rests and the
transition into the first complete bar rendered correctly.
A further annotated fixture containing a pickup, 4/4 → 3/4 → 5/8 → 7/8 → 13/16,
group labels and 120 → 90 BPM was rendered and visually inspected with Verovio.
All 47 input attacks remained present with no grid collisions. This establishes
notation/export behavior on a known grid, not model recognition on a recording.
An official MuseScore 4.7.7 AppImage was obtained, but this container could not
start its Qt/GTK interface without a display, even during CLI conversion.
That attempt does **not** constitute MuseScore PDF verification; desktop or
appropriately configured virtual-display execution still needs confirmation.

Reference documentation:

- [music21 percussion objects](https://www.music21.org/music21docs/moduleReference/modulePercussion.html)
- [MusicXML percussion tutorial](https://www.w3.org/2021/06/musicxml40/tutorial/percussion/)
- [MusicXML metronome visibility](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/metronome/)
- [MusicXML playback sound and offset](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/sound/)
- [MuseScore command-line usage](https://handbook.musescore.org/appendix/command-line-usage)
