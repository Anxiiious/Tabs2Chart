"""Stage 3 — SyncTrack: convert IR tempo/TS events to .chart SyncTrack entries.

.chart SyncTrack grammar (per tick position):
  <tick> = B <millibpm>          — tempo event (BPM × 1000, integer)
  <tick> = TS <num> [<denom_exp>]— time signature; denom_exp = log₂(denominator),
                                   defaults to 2 (i.e., /4 time)

Linear tempo ramps:
  Guitar Pro 6+ (.gpx) supports gradual tempo automations; .gp5 only has
  step-wise changes.  When a TempoEvent carries a ``linear_ramp_to`` value
  (populated by future Route-B ingest), this module discretises the glide at
  one B event per beat so that it plays back smoothly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from .ir import IRSong, TempoEvent


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

@dataclass
class SyncEvent:
    tick: int
    kind: str   # 'B' | 'TS'
    values: tuple


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_synctrack(ir: IRSong) -> List[SyncEvent]:
    """Return a sorted list of :class:`SyncEvent` objects for the [SyncTrack]
    section, derived from *ir*.

    A ``B 120000`` event is always emitted at tick 0 even when the first
    measure's tempo event sits at tick > 0 (which never happens in practice
    but is defensive).
    """
    events: List[SyncEvent] = []

    # ── Tempo events ─────────────────────────────────────────────────────────
    tempo_list = sorted(ir.tempo_events, key=lambda e: e.tick)

    # Guarantee a B event at tick 0 so CH always has an anchor.
    if not tempo_list or tempo_list[0].tick != 0:
        default_bpm = tempo_list[0].bpm if tempo_list else 120.0
        events.append(SyncEvent(tick=0, kind="B", values=(_bpm_to_millibpm(default_bpm),)))

    for i, te in enumerate(tempo_list):
        if te.linear_ramp_to is not None:
            # Determine ramp end tick: the next TempoEvent's tick, or
            # (for the last event) ramp for one extra beat.
            if i + 1 < len(tempo_list):
                ramp_end_tick = tempo_list[i + 1].tick
            else:
                ramp_end_tick = te.tick + ir.resolution

            _emit_ramp(events, te, ramp_end_tick, ir.resolution)
        else:
            events.append(
                SyncEvent(
                    tick=te.tick,
                    kind="B",
                    values=(_bpm_to_millibpm(te.bpm),),
                )
            )

    # ── Time-signature events ─────────────────────────────────────────────────
    ts_list = sorted(ir.time_signatures, key=lambda e: e.tick)

    # Guarantee a TS event at tick 0.
    if not ts_list or ts_list[0].tick != 0:
        events.append(SyncEvent(tick=0, kind="TS", values=(4, 2)))

    for ts in ts_list:
        denom_exp = _denom_exp(ts.denominator)
        events.append(
            SyncEvent(tick=ts.tick, kind="TS", values=(ts.numerator, denom_exp))
        )

    # Sort: by tick, then B before TS at the same tick (CH convention).
    events.sort(key=lambda e: (e.tick, 0 if e.kind == "B" else 1))

    # Deduplicate: keep last B / last TS per tick (later entries win).
    events = _deduplicate(events)

    return events


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bpm_to_millibpm(bpm: float) -> int:
    """Convert BPM to the integer milliBPM value used in .chart B events."""
    return round(bpm * 1000)


def _denom_exp(denominator: int) -> int:
    """Return floor(log₂(denominator)), clamped to [1, 5].

    Examples: denom=4 → 2, denom=8 → 3, denom=2 → 1.
    The .chart format default (when omitted) is 2 (quarter-note denominator).
    """
    if denominator <= 0:
        return 2
    exp = int(math.floor(math.log2(denominator)))
    return max(1, min(5, exp))


def _emit_ramp(
    events: List[SyncEvent],
    ramp: TempoEvent,
    end_tick: int,
    resolution: int,
) -> None:
    """Discretise a linear tempo glide from *ramp.bpm* to *ramp.linear_ramp_to*
    by emitting one B event per beat across the span."""
    start_bpm = ramp.bpm
    end_bpm = ramp.linear_ramp_to  # type: ignore[assignment]
    start_tick = ramp.tick

    span_ticks = end_tick - start_tick
    if span_ticks <= 0:
        events.append(
            SyncEvent(tick=start_tick, kind="B", values=(_bpm_to_millibpm(start_bpm),))
        )
        return

    num_steps = max(1, span_ticks // resolution)
    for step in range(num_steps):
        frac = step / num_steps
        bpm = start_bpm + (end_bpm - start_bpm) * frac
        tick = start_tick + round(step * span_ticks / num_steps)
        events.append(SyncEvent(tick=tick, kind="B", values=(_bpm_to_millibpm(bpm),)))


def _deduplicate(events: List[SyncEvent]) -> List[SyncEvent]:
    """When two events of the same kind land on the same tick, keep the last."""
    seen: dict = {}  # (tick, kind) → index in result
    result: List[SyncEvent] = []
    for ev in events:
        key = (ev.tick, ev.kind)
        if key in seen:
            result[seen[key]] = ev  # overwrite with the later one
        else:
            seen[key] = len(result)
            result.append(ev)
    return result
