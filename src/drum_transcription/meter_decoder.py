"""Conservative global barline decoder using Beat This! framewise evidence.

Only estimates boundaries on the observed beat grid. Numerator/denominator and
sub-beat odd meters remain separate, ambiguous inference problems. Never use
this as a substitute for reviewing the recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite
from typing import Sequence


@dataclass(frozen=True)
class DecodeResult:
    indices: tuple[int, ...]
    skipped_raw_markers: int
    inserted_markers: int
    score: float
    mode: str


def _beat_scores(beats: Sequence[float], logits: Sequence[float], fps: int) -> list[float]:
    if fps <= 0 or len(logits) < 2:
        raise ValueError("Invalid downbeat frame evidence.")
    # Reject corrupted model evidence before max-pooling: Python's max() can
    # silently choose a finite neighbor over NaN in the local frame window.
    if not all(isfinite(float(value)) for value in logits):
        raise ValueError("Downbeat frame evidence contains NaN/Inf.")
    scores: list[float] = []
    for when in beats:
        frame = max(0, min(len(logits) - 1, int(floor(when * fps + 0.5))))
        # +/- one 20-ms frame absorbs frame quantization. No broad smoothing.
        score = max(logits[max(0, frame - 1): min(len(logits), frame + 2)])
        if not isfinite(float(score)):
            raise ValueError("Downbeat frame evidence contains NaN/Inf.")
        scores.append(max(-8.0, min(8.0, float(score))))
    return scores


def decode_barlines(
    beats: Sequence[float],
    raw_indices: Sequence[int],
    frame_logits: Sequence[float],
    *,
    fps: int = 50,
) -> DecodeResult:
    """Viterbi over candidate beat indices, penalizing false-short measures.

    A model peak is evidence, not an unconditional barline. This decoder can
    suppress weak interior peaks, but keeps the first and last observed anchors
    and refuses sweeping edits. Unlike a fixed 4/4 filter, the path may change
    between 2–12 beats per bar. The returned score is *not* confidence.
    """
    markers = tuple(int(i) for i in raw_indices)
    if len(markers) < 3:
        return DecodeResult(markers, 0, 0, 0.0, "raw_insufficient_anchors")
    if (
        markers[0] < 0 or markers[-1] >= len(beats)
        or any(b <= a for a, b in zip(markers, markers[1:]))
    ):
        raise ValueError("Downbeat indices are not increasing beat indices.")
    logits = _beat_scores(beats, frame_logits, fps)
    raw = set(markers)
    interval = logits[markers[0]: markers[-1] + 1]
    if max(interval) - min(interval) < 1.0:
        return DecodeResult(markers, 0, 0, 0.0, "raw_flat_evidence")

    # Only a positive frame prediction can introduce a boundary. The decoder
    # never constructs an arbitrary timestamp between observed beats.
    candidates = [
        i for i in range(markers[0], markers[-1] + 1)
        if i in raw or logits[i] > 0.3
    ]
    max_length = 12
    length_prior = {
        1: -3.1, 2: 0.15, 3: 0.35, 4: 0.5,
        5: 0.0, 6: -0.15, 7: -0.2, 8: -0.35,
        9: -0.45, 10: -0.65, 11: -0.75, 12: -0.85,
    }
    state: dict[tuple[int, int], tuple[float, tuple[int, int] | None]] = {
        (markers[0], 0): (0.0, None)
    }
    candidate_set = set(candidates)
    for end in candidates[1:]:
        for length in range(1, max_length + 1):
            start = end - length
            if start not in candidate_set:
                continue
            earlier_lengths = [0] if start == markers[0] else [
                previous for previous in range(1, max_length + 1)
                if (start, previous) in state
            ]
            if not earlier_lengths:
                continue
            evidence = max(-4.0, min(4.0, logits[end]))
            if end not in raw:
                evidence -= 1.15
            bar_score = evidence - 0.8 + length_prior[length]
            for previous_length in earlier_lengths:
                change_penalty = 0.0 if previous_length in (0, length) else 0.9
                if previous_length and (
                    2 * previous_length == length or 2 * length == previous_length
                ):
                    change_penalty += 0.25
                value = state[(start, previous_length)][0] + bar_score - change_penalty
                key = (end, length)
                if key not in state or value > state[key][0]:
                    state[key] = (value, (start, previous_length))

    finals = [
        (score, key) for key, (score, _parent) in state.items()
        if key[0] == markers[-1] and key[1] != 0
    ]
    if not finals:
        return DecodeResult(markers, 0, 0, 0.0, "raw_no_path")
    score, current = max(finals)
    selected = [markers[-1]]
    while current[0] != markers[0]:
        parent = state[current][1]
        if parent is None:
            break
        selected.append(parent[0])
        current = parent
    chosen = tuple(reversed(selected))
    skipped = len(raw - set(chosen))
    inserted = len(set(chosen) - raw)
    # Unusual odd-meter audio may legitimately vary every measure. Do not
    # silently replace most model downbeats without independent evidence.
    if skipped > max(2, len(markers) // 2) or inserted > max(2, len(markers) // 2):
        return DecodeResult(markers, 0, 0, float(score), "raw_excessive_correction")
    return DecodeResult(
        chosen, skipped, inserted, float(score),
        "decoded" if chosen != markers else "raw_agreement",
    )
