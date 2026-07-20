"""Tests for the blend -> map -> emit pipeline (M2/M3)."""
from __future__ import annotations

from shred2chart.blend import blend_tracks
from shred2chart.chart_writer import add_lead_in, build_chart
from shred2chart.mapper import (
    CHART_RESOLUTION,
    IR_TICKS_PER_QUARTER,
    ChartNote,
    _assign_lanes,
    _LaneContour,
    map_notes,
)

IR_QUARTER = 960  # IR ticks per quarter note


def _note(tick, pitch=40, string=1, fret=0, duration=IR_QUARTER // 4, **flags):
    base = {
        "tick": tick, "duration_ticks": duration, "pitch": pitch, "string": string,
        "fret": fret, "chord_id": None, "hammer_on": False, "pull_off": False,
        "slide_in": False, "slide_out": False, "palm_mute": False, "dead_note": False,
        "bend": False, "tap": False, "vibrato": False, "tremolo_picked": False,
        "let_ring": False, "tied": False, "accent": False, "ghost_note": False,
    }
    base.update(flags)
    return base


class TestBlend:
    def test_lead_wins_technique_heavy_section_rhythm_wins_busy_one(self):
        # Section 1 (ticks 0-3840): rhythm has 4 plain notes, lead has 2
        # tapped notes (2 + 2*2*2 = wait: 2 notes + 2 flags * 2 weight = 6) -> lead wins.
        # Section 2 (3840+): rhythm has 4 plain notes, lead has 1 plain -> rhythm wins.
        rhythm = [_note(t) for t in (0, 960, 1920, 2880)] + [_note(t) for t in (3840, 4800, 5760, 6720)]
        lead = [_note(t, pitch=70, tap=True) for t in (0, 1920)] + [_note(3840, pitch=70)]
        sections = [
            {"tick": 0, "bar": 0, "name": "Verse"},
            {"tick": 3840, "bar": 4, "name": "Chorus"},
        ]
        blended, choices = blend_tracks({0: rhythm, 1: lead}, [1, 0], sections)
        assert [c["track"] for c in choices] == [1, 0]
        assert [c["section"] for c in choices] == ["Verse", "Chorus"]
        # Verse notes come from lead (2 notes), chorus from rhythm (4 notes)
        assert len(blended) == 6

    def test_no_sections_means_single_span(self):
        rhythm = [_note(0), _note(960)]
        blended, choices = blend_tracks({0: rhythm}, [0], sections=[])
        assert len(blended) == 2
        assert choices[0]["section"] == "(whole song)"

    def test_priority_breaks_ties(self):
        a = [_note(0), _note(960)]
        b = [_note(0), _note(960)]
        _, choices = blend_tracks({0: a, 1: b}, [1, 0], sections=[])
        assert choices[0]["track"] == 1


class TestMapper:
    def test_tick_conversion_and_first_note_anchors(self):
        # First note still seeds the anchor from absolute pitch (pitch % 5),
        # matching the old baseline for exactly one note; the second note's
        # lane is RELATIVE to that anchor, not its own absolute pitch % 5.
        notes = map_notes([_note(0, pitch=62, fret=5), _note(960, pitch=63, fret=6)])
        assert notes[0].tick == 0 and notes[0].lanes == [62 % 5]
        assert notes[1].tick == CHART_RESOLUTION
        assert notes[1].lanes == [(62 % 5) + 1]  # +1 semitone, in-box step, anchor unmoved

    def test_open_chug_on_lowest_string(self):
        # String 1 tuned to pitch 36 (fret 0), string 2 higher.
        ir = [
            _note(0, pitch=36, string=1, fret=0),
            _note(960, pitch=45, string=2, fret=0),
            _note(1920, pitch=38, string=1, fret=2),
        ]
        notes = map_notes(ir)
        assert notes[0].lanes == [7]  # open note: fret 0 on the lowest-tuned string
        assert notes[1].lanes != [7]  # fret 0 on a higher string is NOT open
        assert notes[2].lanes != [7]  # fretted note on the low string is NOT open

    def test_tied_note_merges_into_sustain(self):
        ir = [
            _note(0, pitch=40, string=1, fret=5, duration=IR_QUARTER),
            _note(IR_QUARTER, pitch=40, string=1, fret=5, duration=IR_QUARTER, tied=True),
        ]
        notes = map_notes(ir)
        assert len(notes) == 1
        assert notes[0].sustain == 2 * CHART_RESOLUTION  # two quarters, no trim needed

    def test_chord_lanes_adjacent_max_three_wide(self):
        chord = [
            _note(0, pitch=40, string=1, fret=5, chord_id=0),
            _note(0, pitch=47, string=2, fret=5, chord_id=0),
            _note(0, pitch=52, string=3, fret=5, chord_id=0),
            _note(0, pitch=59, string=4, fret=5, chord_id=0),
        ]
        notes = map_notes(chord)
        assert len(notes) == 1
        lanes = notes[0].lanes
        assert len(lanes) == 3  # capped
        assert lanes == list(range(lanes[0], lanes[0] + 3))  # adjacent
        assert max(lanes) <= 4

    def test_flags_and_sustain_threshold(self):
        ir = [
            _note(0, pitch=41, fret=6, duration=IR_QUARTER // 4, hammer_on=True),
            _note(960, pitch=43, fret=8, duration=IR_QUARTER // 4, tap=True),
        ]
        notes = map_notes(ir)
        assert notes[0].forced is True and notes[0].tap is False
        assert notes[1].tap is True
        assert notes[0].sustain == 0  # sixteenth note -> below sustain threshold


class TestLaneContour:
    """M4 contour mapping: _assign_lanes(group, chug_string, contour) with a
    threshold-based static anchor (see mapper.py module docstring)."""

    def _single(self, pitch, fret=5):
        return [{"pitch": pitch, "fret": fret, "string": 1}]

    def test_notes_within_one_hand_position_stay_one_step_from_anchor(self):
        # Anchor at 60 (lane 0). Every later note in this test is within the
        # 4-semitone box, so the anchor is static and each note's lane is a
        # PURE function of (anchor_lane, direction from anchor) - not an
        # incremental walk. Same direction from the same anchor -> same lane,
        # which is exactly what gives pattern stability its guarantee.
        contour = _LaneContour()
        lanes = [
            _assign_lanes(self._single(p), None, contour)[0] for p in (60, 62, 64)
        ]
        assert lanes == [0, 1, 1]  # 62 and 64 are both "+1 from the static anchor"
        assert contour.anchor_pitch == 60  # anchor never moved

    def test_ascending_run_crossing_hand_positions_is_monotonic(self):
        # A run that crosses hand-position boundaries (leaps > 4 semitones
        # each step) re-centers the anchor each time and climbs in
        # proportion to the leap size - here each +6 semitone leap is
        # round(6/3)=2 lanes, so it climbs 0 -> 2 -> 4 -> clamped at 4.
        contour = _LaneContour()
        lanes = [
            _assign_lanes(self._single(p), None, contour)[0] for p in (60, 66, 72, 78)
        ]
        assert lanes == [0, 2, 4, 4]
        assert contour.anchor_pitch == 78  # re-centered on every leap

    def test_descending_run_clamps_at_zero(self):
        contour = _LaneContour()
        contour.anchor_pitch, contour.anchor_lane = 60, 0
        lanes = [
            _assign_lanes(self._single(p), None, contour)[0] for p in (58, 56, 54)
        ]
        assert lanes == [0, 0, 0]  # clamped: can't go below lane 0

    def test_repeated_pitch_stays_on_same_lane(self):
        contour = _LaneContour()
        first = _assign_lanes(self._single(60), None, contour)[0]
        second = _assign_lanes(self._single(60), None, contour)[0]
        assert first == second

    def test_pattern_stability_same_riff_maps_identically(self):
        # A short riff within one hand-position box (anchor+0/+2/+4), played
        # twice in a row through the same contour, must produce identical
        # lanes both times - proof the anchor is static, not drifting.
        contour = _LaneContour()
        riff = [60, 62, 64, 60, 62, 64]
        lanes = [_assign_lanes(self._single(p), None, contour)[0] for p in riff]
        assert lanes[0:3] == lanes[3:6]
        assert contour.anchor_pitch == 60  # never moved - all deltas were <=4

    def test_in_box_move_does_not_recenter_anchor(self):
        contour = _LaneContour()
        _assign_lanes(self._single(60), None, contour)
        _assign_lanes(self._single(64), None, contour)  # delta=4, inclusive boundary
        assert contour.anchor_pitch == 60

    def test_leap_beyond_hand_position_recenters_anchor(self):
        contour = _LaneContour()
        _assign_lanes(self._single(60), None, contour)  # anchor=60, lane=0
        lane = _assign_lanes(self._single(72), None, contour)[0]  # delta=12
        assert contour.anchor_pitch == 72  # re-centered on the new pitch
        # round(12/3)=4 lanes of movement, proportional to the leap size -
        # not a flat +-1 (that was the v1 bug that caused "stuck high"
        # oscillation on real wide-ranging lead lines).
        assert lane == 4

    def test_open_note_bypasses_contour_and_does_not_touch_anchor(self):
        contour = _LaneContour()
        _assign_lanes(self._single(60), None, contour)
        group = [{"pitch": 36, "fret": 0, "string": 1}]
        assert _assign_lanes(group, chug_string=1, contour=contour) == [7]
        assert contour.anchor_pitch == 60  # untouched by the open note

    def test_real_lead_lick_regression_spreads_across_lanes(self):
        # Regression guard for the M4 v1 "stuck high" bug: a real wide-
        # ranging lead lick (Still Searching, track 1, ticks 92000-110880,
        # spanning 17 semitones) got jammed onto just 2 adjacent lanes
        # under v1's flat +-1 step. The proportional formula must spread
        # it across at least 3 distinct lanes.
        pitches = [
            68, 61, 73, 61, 69, 69, 61, 68, 66, 66, 61, 76, 78, 78, 76, 73, 61, 73,
            61, 69, 69, 61, 68, 66, 66, 73, 74, 76, 76, 74, 73, 69, 61, 73, 61, 69,
            69, 61, 68, 66,
        ]
        contour = _LaneContour()
        lanes = [_assign_lanes(self._single(p), None, contour)[0] for p in pitches]
        assert len(set(lanes)) >= 3, f"squashed to {sorted(set(lanes))}: {lanes}"
        # Pitch 61 (the recurring low pedal tone) settles onto one lane
        # once its own anchor is established - its very first occurrence
        # is still relative to whatever anchor preceded it, but every
        # occurrence after that should land on the same lane, not oscillate.
        sixty_one_indices = [i for i, p in enumerate(pitches) if p == 61]
        lanes_after_first_occurrence = [lanes[i] for i in sixty_one_indices[1:]]
        assert len(set(lanes_after_first_occurrence)) == 1, (
            f"61 landed on inconsistent lanes after settling: {lanes_after_first_occurrence}"
        )


class TestChordDisjoint:
    def _chord(self, pitches):
        return [{"pitch": p, "string": i + 1, "fret": 5} for i, p in enumerate(pitches)]

    def test_close_chord_stays_contiguous(self):
        # Matches test_chord_lanes_adjacent_max_three_wide's real spacing.
        contour = _LaneContour()
        lanes = _assign_lanes(self._chord([40, 47, 52]), None, contour)
        assert lanes == list(range(lanes[0], lanes[0] + 3))

    def test_exactly_one_octave_apart_is_not_disjoint(self):
        contour = _LaneContour()
        lanes = _assign_lanes(self._chord([40, 52]), None, contour)  # gap == 12
        assert lanes[1] - lanes[0] == 1  # still adjacent, not gapped

    def test_more_than_one_octave_apart_is_disjoint(self):
        contour = _LaneContour()
        lanes = _assign_lanes(self._chord([40, 53]), None, contour)  # gap == 13
        assert lanes[1] - lanes[0] == 2  # one empty lane between them


class TestChartWriter:
    def test_full_chart_output(self):
        ir = [
            _note(0, pitch=40, fret=5, duration=IR_QUARTER * 2),
            _note(IR_QUARTER * 2, pitch=41, fret=6, hammer_on=True),
        ]
        chart_notes = map_notes(ir)
        text = build_chart(
            title="Test Song",
            artist="Test Artist",
            tempo_events=[
                {"tick": 0, "type": "time_signature", "numerator": 4, "denominator": 4},
                {"tick": 0, "type": "tempo", "bpm": 123},
                {"tick": 3840, "type": "time_signature", "numerator": 6, "denominator": 8},
            ],
            sections=[{"tick": 0, "bar": 0, "name": "Intro"}],
            chart_notes=chart_notes,
            offset_ms=250,
        )
        assert 'Name = "Test Song"' in text
        assert "Resolution = 192" in text
        assert "Offset = 0.25" in text
        assert "0 = B 123000" in text
        assert "0 = TS 4\n" in text  # /4 -> exponent omitted
        assert "768 = TS 6 3" in text  # 6/8 -> exponent 3, tick 3840/5
        assert '0 = E "section Intro"' in text
        # pitch 40 % 5 = lane 0; 2-quarter sustain (384) trimmed by the
        # 1/32 gap (24) because the next note starts right at its end.
        assert "0 = N 0 360" in text
        assert "384 = N 1 0" in text  # pitch 41 % 5 = lane 1
        assert "384 = N 5 0" in text  # forced flag at same tick


class TestLeadIn:
    def test_zero_bars_is_a_no_op(self):
        tempo = [{"tick": 0, "type": "tempo", "bpm": 120}]
        sections = [{"tick": 0, "bar": 0, "name": "Intro"}]
        notes = [ChartNote(tick=0, lanes=[0])]
        st, ss, sn, delay = add_lead_in(tempo, sections, notes, bars=0)
        assert (st, ss, sn, delay) == (tempo, sections, notes, 0)

    def test_shifts_every_tick_by_whole_bars(self):
        # 4/4: one bar = 4 quarters. tempo_events/sections are IR-scale
        # (960/quarter); chart_notes are chart-scale (192/quarter) - same
        # musical shift, different tick units.
        tempo = [
            {"tick": 0, "type": "time_signature", "numerator": 4, "denominator": 4},
            {"tick": 0, "type": "tempo", "bpm": 120},
        ]
        sections = [{"tick": 0, "bar": 0, "name": "Intro"}]
        notes = [ChartNote(tick=0, lanes=[0], sustain=96)]
        st, ss, sn, delay = add_lead_in(tempo, sections, notes, bars=2)
        ir_bar = IR_TICKS_PER_QUARTER * 4
        chart_bar = CHART_RESOLUTION * 4
        assert [e["tick"] for e in st] == [2 * ir_bar, 2 * ir_bar]
        assert ss == [{"tick": 2 * ir_bar, "bar": 0, "name": "Intro"}]
        assert sn[0].tick == 2 * chart_bar
        assert sn[0].sustain == 96  # sustain length is untouched, only position shifts

    def test_lead_in_ms_matches_bar_length_at_tempo(self):
        # 2 bars of 4/4 at 120 bpm = 8 quarters = 4 seconds = 4000ms exactly.
        tempo = [
            {"tick": 0, "type": "time_signature", "numerator": 4, "denominator": 4},
            {"tick": 0, "type": "tempo", "bpm": 120},
        ]
        _, _, _, delay = add_lead_in(tempo, [], [], bars=2)
        assert delay == 4000

    def test_uses_time_signature_in_effect_at_tick_zero(self):
        # 3/4 instead of 4/4 changes bar length: 3 quarters/bar at 120bpm.
        tempo = [
            {"tick": 0, "type": "time_signature", "numerator": 3, "denominator": 4},
            {"tick": 0, "type": "tempo", "bpm": 120},
        ]
        _, _, _, delay = add_lead_in(tempo, [], [], bars=2)
        assert delay == 3000  # 2 bars * 3 quarters * 500ms/quarter
