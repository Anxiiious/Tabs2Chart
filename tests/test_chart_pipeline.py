"""Tests for the blend -> map -> emit pipeline (M2/M3)."""
from __future__ import annotations

from shred2chart.blend import blend_tracks
from shred2chart.chart_writer import add_lead_in, build_chart
from shred2chart.mapper import (
    CHART_RESOLUTION,
    IR_TICKS_PER_QUARTER,
    ChartNote,
    _chord_lanes_sequence,
    _group_chords_into_hand_positions,
    _group_into_hand_positions,
    _single_note_lanes,
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
        # The first hand-position group seeds centered (lane 2), regardless
        # of absolute pitch (62 % 5 also happens to be 2, but that's
        # coincidence, not the mechanism - see _single_note_lanes). The
        # second note joins the same group (delta=1, within HAND_POSITION_
        # SEMITONES) and rank-orders above it since it's the higher pitch.
        notes = map_notes([_note(0, pitch=62, fret=5), _note(960, pitch=63, fret=6)])
        assert notes[0].tick == 0 and notes[0].lanes == [2]
        assert notes[1].tick == CHART_RESOLUTION
        assert notes[1].lanes == [3]  # +1 semitone, in-box step, anchor unmoved

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


class TestHandPositionGrouping:
    """_group_into_hand_positions: splits a pitch sequence into runs where
    every note stays within HAND_POSITION_SEMITONES of the run's first
    pitch (see mapper.py module docstring)."""

    def test_notes_within_one_hand_position_form_one_group(self):
        assert _group_into_hand_positions([60, 62, 64]) == [[60, 62, 64]]

    def test_leap_beyond_hand_position_starts_a_new_group(self):
        assert _group_into_hand_positions([60, 72]) == [[60], [72]]

    def test_boundary_delta_of_exactly_four_stays_in_one_group(self):
        assert _group_into_hand_positions([60, 64]) == [[60, 64]]

    def test_empty_input_returns_no_groups(self):
        assert _group_into_hand_positions([]) == []


class TestSingleNoteLanes:
    """_single_note_lanes: rank-order-within-group + proportional leap
    between groups + memoized exact repeats (see mapper.py module
    docstring for the full design and why it replaced the v1-v5 static-
    anchor contour)."""

    def test_first_group_seeds_centered(self):
        lanes = _single_note_lanes([60])
        assert lanes == [2]

    def test_distinct_pitches_in_one_group_get_distinct_rank_ordered_lanes(self):
        # Ascending pitches within one hand position rank in the same
        # order: lowest pitch gets the lowest lane of the spread.
        lanes = _single_note_lanes([60, 62, 64])
        assert lanes[0] < lanes[1] < lanes[2]
        assert len(set(lanes)) == 3

    def test_repeated_pitch_within_a_group_reuses_its_rank_lane(self):
        lanes = _single_note_lanes([60, 62, 60, 62])
        assert lanes[0] == lanes[2]
        assert lanes[1] == lanes[3]

    def test_leap_starts_a_new_group_centered_by_default(self):
        lanes = _single_note_lanes([60, 72])
        assert lanes[0] == 2  # first group, centered
        # second group is a fresh single-pitch group; its target lane is
        # a proportional leap (round(12/3)=4) from the centered anchor,
        # clamped to [0, 4].
        assert lanes[1] == 4

    def test_exact_repeat_group_replays_the_first_occurrences_lanes(self):
        # The core ask this design was built for: an identical riff
        # recurring later (even after a totally different intervening
        # passage) must map to the IDENTICAL lane sequence both times -
        # confirmed necessary because real songs reprise earlier material
        # across section boundaries (e.g. "Still Searching"'s [A'] section
        # replays material from [C]/[D]/[E]), so per-section-only
        # stability isn't enough.
        riff = [60, 62, 64]
        unrelated = [90, 91]  # a distant, unrelated passage in between
        pitches = riff + unrelated + riff
        lanes = _single_note_lanes(pitches)
        assert lanes[0:3] == lanes[-3:]

    def test_consecutive_distinct_pitches_never_share_a_lane(self):
        # Regression guard, generalized from two real bugs found by
        # playtest: (1) a hammer-on/pull-off landing on the same lane as
        # the note before it reads as "hammer onto the same button,"
        # which isn't a real guitar move; (2) a picked descending chug run
        # (frets 7/5/4/5/4/2, "Still Searching" track 1 section [G]) also
        # clustered notes onto one lane under the old proportional-step
        # formula. Neither is HOPO-specific - the rule is general: no two
        # ADJACENT single notes with different pitches may share a lane.
        pitches = [52, 50, 49, 50, 49, 47]
        lanes = _single_note_lanes(pitches)
        for i in range(1, len(pitches)):
            assert lanes[i] != lanes[i - 1], (
                f"note {i} (pitch {pitches[i]}) repeated lane {lanes[i]} "
                f"from note {i-1} (pitch {pitches[i-1]})"
            )

    def test_real_lead_lick_regression_spreads_across_lanes(self):
        # Regression guard for the M4 v1 "stuck high" bug: a real wide-
        # ranging lead lick (Still Searching, track 1, ticks 92000-110880,
        # spanning 17 semitones) got jammed onto just 2 adjacent lanes
        # under v1's flat +-1 step.
        pitches = [
            68, 61, 73, 61, 69, 69, 61, 68, 66, 66, 61, 76, 78, 78, 76, 73, 61, 73,
            61, 69, 69, 61, 68, 66, 66, 73, 74, 76, 76, 74, 73, 69, 61, 73, 61, 69,
            69, 61, 68, 66,
        ]
        lanes = _single_note_lanes(pitches)
        assert len(set(lanes)) >= 3, f"squashed to {sorted(set(lanes))}: {lanes}"

    def test_real_section_g_regression_descending_run_spreads_out(self):
        # Regression guard for the bug found by playtest after v4/v5: a
        # descending single-string chug run (frets 7/5/4/5/4/2, pitches
        # 52/50/49/50/49/47, "Still Searching" track 1 section [G]) still
        # clustered onto just green/red under the old per-note
        # proportional-distance formula, because deltas -2 and -3 both
        # rounded to the same lane step. Rank-ordering the group's
        # distinct pitches fixes this by construction.
        pitches = [52, 50, 49, 50, 49, 47]
        lanes = _single_note_lanes(pitches)
        assert len(set(lanes)) >= 3, f"squashed to {sorted(set(lanes))}: {lanes}"


class TestChordDisjoint:
    def _lanes(self, pitches):
        return _chord_lanes_sequence([tuple(sorted(pitches))])[0]

    def test_close_chord_stays_contiguous(self):
        # Matches test_chord_lanes_adjacent_max_three_wide's real spacing.
        lanes = self._lanes([40, 47, 52])
        assert lanes == list(range(lanes[0], lanes[0] + 3))

    def test_exactly_one_octave_apart_is_not_disjoint(self):
        lanes = self._lanes([40, 52])  # gap == 12
        assert lanes[1] - lanes[0] == 1  # still adjacent, not gapped

    def test_more_than_one_octave_apart_is_disjoint(self):
        lanes = self._lanes([40, 53])  # gap == 13
        assert lanes[1] - lanes[0] == 2  # one empty lane between them

    def test_repeated_chord_shape_reuses_its_lanes(self):
        # Same repeat-stability guarantee as single notes: an identical
        # chord shape recurring later must map to identical lanes.
        lanes_seq = _chord_lanes_sequence([(54, 66), (57, 69), (54, 66)])
        assert lanes_seq[0] == lanes_seq[2]

    def test_real_section_f_regression_nearby_chords_spread_across_lanes(self):
        # Regression guard for a real bug found by playtest: an earlier
        # per-chord leap+memo design (root-to-root leaps, analogous to a
        # v1-era single-note contour) crowded several nearby-but-distinct
        # power-chord shapes onto lanes 3-4 - "Section F feels anchored
        # at blue and orange during most of the chord changes." Real data:
        # "Still Searching" track 1 section [F], 5 power-chord roots
        # (56/57/59/61/62, all root+octave voicings) spanning 6
        # semitones - one hand position. Rank-ordering the group's
        # distinct roots (same mechanism as the single-note section [D]
        # fix) must spread them across most of the lane range instead of
        # crowding into 1-2 lanes.
        chords = [
            (57, 69), (56, 68), (59, 71), (57, 69), (56, 68), (59, 71),
            (61, 73), (59, 71), (62, 74), (61, 73), (59, 71),
        ]
        lanes_seq = _chord_lanes_sequence(chords)
        base_lanes = [lanes[0] for lanes in lanes_seq]
        assert len(set(base_lanes)) >= 4, f"squashed to {sorted(set(base_lanes))}: {base_lanes}"

    def test_gradual_chord_walk_stays_one_group_despite_wide_total_span(self):
        # Regression guard: chord grouping uses a ROLLING anchor (compare
        # each chord's root to the PREVIOUS chord's root), not a fixed
        # first-root anchor. A gradual walk where every step is small but
        # the first-to-last span exceeds HAND_POSITION_SEMITONES must
        # still stay ONE group - confirmed necessary on the real section
        # [F] progression (roots 56-57-59-61-62, each step <=4 semitones,
        # first-to-last span is 6). A fixed-first-root anchor split this
        # partway through, which meant the rank-order-across-the-group
        # fix only ever saw half the progression's roots and still
        # crowded distinct chords onto the same 1-2 lanes.
        chords = [(56, 68), (57, 69), (59, 71), (61, 73), (62, 74)]
        groups = _group_chords_into_hand_positions(chords)
        assert len(groups) == 1

    def test_repeated_chord_in_a_fast_progression_reuses_its_own_lane(self):
        # Companion to the section [F] spread test: within that same
        # cluster, each DISTINCT chord shape must still map consistently
        # to its own lane every time it recurs (not just "spread out
        # overall") - repeat-stability within the group, not just across
        # groups.
        chords = [(57, 69), (56, 68), (59, 71), (57, 69), (56, 68), (59, 71)]
        lanes_seq = _chord_lanes_sequence(chords)
        assert lanes_seq[0] == lanes_seq[3]  # (57,69) both times
        assert lanes_seq[1] == lanes_seq[4]  # (56,68) both times
        assert lanes_seq[2] == lanes_seq[5]  # (59,71) both times


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
        # First note seeds the anchor at the centered lane 2 (not from
        # absolute pitch); 2-quarter sustain (384) trimmed by the 1/32 gap
        # (24) because the next note starts right at its end.
        assert "0 = N 2 360" in text
        assert "384 = N 3 0" in text  # pitch 41 is +1 semitone -> anchor lane +1
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
