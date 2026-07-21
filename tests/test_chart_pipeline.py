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


def _pf(pitches):
    """(pitch, fret) pairs for grouping/lane tests that don't care about
    string - fret stands in for pitch (a single-string melody has
    fret == pitch - open-string offset, so using pitch itself as the
    fret keeps these synthetic examples' hand-position math unchanged)."""
    return [(p, p) for p in pitches]


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

    def test_pitch_bonus_favors_established_higher_lead_over_busier_chug(self):
        # A low, busy rhythm chug (more raw notes) against a higher,
        # sparser lead line (fewer notes, both well above the min-notes
        # floor) - the lead should win despite fewer notes, matching a
        # real file ("Still Searching" section F/bar 33): guitar 1 chugs
        # low with more raw notes than guitar 2's higher riff, so
        # count-only scoring wrongly picked the chug for the section.
        rhythm = [_note(t, pitch=42) for t in range(0, IR_QUARTER * 40, 240)]  # busy, low
        lead = [_note(t, pitch=66) for t in range(0, IR_QUARTER * 40, 960)]  # sparser, high
        sections = [{"tick": 0, "bar": 0, "name": "F"}]
        _, choices = blend_tracks({0: rhythm, 1: lead}, [0, 1], sections)
        assert all(c["track"] == 1 for c in choices)

    def test_pitch_bonus_ignores_single_outlier_note(self):
        # A single stray high note shouldn't out-leverage its pitch gap
        # against an established, busier rhythm part - the bonus only
        # applies once a track has PITCH_BONUS_MIN_NOTES notes.
        rhythm = [_note(t) for t in (0, 960, 1920, 2880)]  # low (default pitch 40)
        lead = [_note(0, pitch=70)]  # one high note, well under the floor
        sections = [{"tick": 0, "bar": 0, "name": "Verse"}]
        _, choices = blend_tracks({0: rhythm, 1: lead}, [0, 1], sections)
        assert all(c["track"] == 0 for c in choices)

    def test_override_pins_track_from_given_tick(self):
        # Track 0 has the only notes in the first window, so it wins there
        # by default scoring; both tracks have notes from the override
        # tick onward, where track 0 is busier and would normally win, but
        # the override should force track 1 there instead.
        bar = 960 * 4
        win = bar * 2
        a = [_note(t) for t in range(0, win * 3, 240)]  # busy throughout
        b = [_note(t, pitch=70) for t in range(win, win * 3, 960)]  # only from window 1
        sections = [{"tick": 0, "bar": 0, "name": "K"}]
        _, choices = blend_tracks({0: a, 1: b}, [0, 1], sections, overrides=[(win, 1)])
        assert choices[0]["track"] == 0  # before the override: normal scoring
        assert all(c["track"] == 1 for c in choices if c["start_tick"] >= win)

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

    def test_mid_section_switch_on_sub_window_boundary(self):
        # A single section spanning 4 bars (0-15360 ticks): track 0 has more
        # notes in the first 2 bars, track 1 in the last 2 - confirms a
        # switch can happen mid-section, not only at section markers.
        bar = 960 * 4
        a = [_note(t) for t in range(0, bar * 2, 240)]  # busy in bars 0-1
        b = [_note(t) for t in range(bar * 2, bar * 4, 240)]  # busy in bars 2-3
        sections = [{"tick": 0, "bar": 0, "name": "Intro"}]
        _, choices = blend_tracks({0: a, 1: b}, [0, 1], sections)
        tracks_seen = {c["track"] for c in choices}
        assert tracks_seen == {0, 1}

    def test_sustained_tie_run_sticks_with_same_track(self):
        # Two harmonized tracks tied on every window for a whole passage
        # (same rhythm/technique count, different pitches) - real case:
        # "Still Searching" guitars 2/3 play identical rhythm in harmony
        # through the whole intro, BOTH audible in the mix continuously.
        # A run of consecutive ties is one continuous harmony passage, not
        # call-and-response, so the winner should stay fixed rather than
        # hopping every window - confirmed against the real file, where
        # the old always-alternate rule flipped tracks 4 times in ~40s of
        # unchanging harmonized rhythm.
        bar = 960 * 4
        num_windows = 4
        a = []
        b = []
        for w in range(num_windows):
            base = w * bar * 2  # 2-bar sub-windows
            a += [_note(base + t, pitch=54) for t in range(0, bar * 2, 480)]
            b += [_note(base + t, pitch=57) for t in range(0, bar * 2, 480)]
        sections = [{"tick": 0, "bar": 0, "name": "Intro"}]
        _, choices = blend_tracks({0: a, 1: b}, [0, 1], sections)
        winners = [c["track"] for c in choices]
        assert winners == [winners[0]] * len(winners)  # stays on one track

    def test_isolated_tie_still_alternates(self):
        # A single tied window sandwiched between windows with a clear
        # winner is genuine call-and-response, not a sustained harmony
        # passage - it should still alternate away from whichever track
        # most recently won, same as before.
        bar = 960 * 4
        win = bar * 2
        a = (
            [_note(t) for t in range(0, win, 240)]  # window 0: a wins outright
            + [_note(win + t, pitch=54) for t in range(0, win, 480)]  # window 1: tie
        )
        b = [_note(win + t, pitch=57) for t in range(0, win, 480)]  # window 1: tie
        sections = [{"tick": 0, "bar": 0, "name": "Intro"}]
        _, choices = blend_tracks({0: a, 1: b}, [0, 1], sections)
        winners = [c["track"] for c in choices]
        assert winners[0] == 0  # window 0: a wins outright
        assert winners[1] == 1  # window 1: isolated tie -> alternates away from a


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

    def test_chord_lanes_interval_spread_max_three_wide(self):
        # Root-to-2nd interval is 7 semitones (P5) -> gap=2, so the first
        # two distinct pitches skip a lane. A 3rd distinct pitch only ever
        # adds 1 more lane beyond that (not another full interval gap) -
        # matches a real charter's convention of compressing power-chord
        # voicings with octave doublings to a 2-gap shape, not spreading
        # every distinct pitch by its own interval.
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
        assert lanes[1] - lanes[0] == 2 and lanes[2] - lanes[1] == 1
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
    """_group_into_hand_positions: splits a (pitch, fret) note sequence
    into runs where every note stays within HAND_POSITION_SEMITONES of
    the PREVIOUS note's fret (see mapper.py module docstring)."""

    def test_notes_within_one_hand_position_form_one_group(self):
        assert _group_into_hand_positions(_pf([60, 62, 64])) == [_pf([60, 62, 64])]

    def test_leap_beyond_hand_position_starts_a_new_group(self):
        assert _group_into_hand_positions(_pf([60, 72])) == [_pf([60]), _pf([72])]

    def test_boundary_delta_of_exactly_four_stays_in_one_group(self):
        assert _group_into_hand_positions(_pf([60, 64])) == [_pf([60, 64])]

    def test_empty_input_returns_no_groups(self):
        assert _group_into_hand_positions([]) == []

    def test_groups_by_fret_not_pitch(self):
        # The actual bug fix: a low pedal tone on a different string
        # (big pitch delta, small fret delta) must NOT force a new hand
        # position - confirmed on a real lead lick ("Still Searching"
        # track 1 ticks 92000-110880): pitch 61 (string 3, fret 11)
        # alternates against notes on strings 4-6 whose pitches are
        # 7-17 semitones away but whose frets stay within a 6-fret span.
        notes = [(61, 11), (73, 14), (61, 11), (69, 14)]
        assert _group_into_hand_positions(notes) == [notes]


class TestSingleNoteLanes:
    """_single_note_lanes: rank-order-within-group + proportional leap
    between groups + memoized exact repeats (see mapper.py module
    docstring for the full design and why it replaced the v1-v5 static-
    anchor contour)."""

    def test_first_group_seeds_centered(self):
        lanes = _single_note_lanes(_pf([60]))
        assert lanes == [2]

    def test_distinct_pitches_in_one_group_get_distinct_rank_ordered_lanes(self):
        # Ascending pitches within one hand position rank in the same
        # order: lowest pitch gets the lowest lane of the spread.
        lanes = _single_note_lanes(_pf([60, 62, 64]))
        assert lanes[0] < lanes[1] < lanes[2]
        assert len(set(lanes)) == 3

    def test_repeated_pitch_within_a_group_reuses_its_rank_lane(self):
        lanes = _single_note_lanes(_pf([60, 62, 60, 62]))
        assert lanes[0] == lanes[2]
        assert lanes[1] == lanes[3]

    def test_leap_starts_a_new_group_centered_by_default(self):
        lanes = _single_note_lanes(_pf([60, 72]))
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
        lanes = _single_note_lanes(_pf(pitches))
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
        lanes = _single_note_lanes(_pf(pitches))
        for i in range(1, len(pitches)):
            assert lanes[i] != lanes[i - 1], (
                f"note {i} (pitch {pitches[i]}) repeated lane {lanes[i]} "
                f"from note {i-1} (pitch {pitches[i-1]})"
            )

    def test_real_lead_lick_regression_spreads_across_lanes(self):
        # Regression guard for the M4 v1 "stuck high" bug, later found to
        # still be broken under v6/v7: a real wide-ranging lead lick
        # ("Still Searching" track 1, ticks 92000-110880) alternates a
        # low pedal tone (pitch 61, string 3, fret 11) against a moving
        # voice on strings 4-6 (frets 9-15) - pitch deltas up to 17
        # semitones, but the whole phrase sits in a 6-fret span. Grouping
        # by pitch (rather than fret) fragmented this into ~20 one/two-
        # note groups, each leaping independently from a reset center
        # lane, so several distinct high notes collapsed onto the same
        # clamped lane - the phrase read as only two buttons repeating
        # despite touching 8 distinct pitches. Grouping by fret keeps it
        # one hand position, so the whole phrase's distinct pitches
        # rank-order together across up to 5 lanes.
        notes = [
            (68, 9), (61, 11), (73, 14), (61, 11), (69, 14), (69, 14), (61, 11),
            (68, 13), (66, 11), (66, 11), (61, 11), (76, 12), (78, 14), (78, 14),
            (76, 12), (73, 14), (61, 11), (73, 14), (61, 11), (69, 14), (69, 14),
            (61, 11), (68, 13), (66, 11), (66, 11), (73, 14), (74, 15), (76, 12),
            (76, 12), (74, 15), (73, 14), (69, 14), (61, 11), (73, 14), (61, 11),
            (69, 14), (69, 14), (61, 11), (68, 13), (66, 11),
        ]
        lanes = _single_note_lanes(notes)
        assert len(set(lanes)) >= 4, f"squashed to {sorted(set(lanes))}: {lanes}"

        # Grouping now keeps the whole phrase in a handful of wide
        # hand-position groups (split only when a group would need more
        # than 5 distinct pitches, not on every big pitch leap) - within
        # EACH such group, every distinct pitch must get its own lane.
        groups = _group_into_hand_positions(notes)
        idx = 0
        for group in groups:
            group_lanes = lanes[idx:idx + len(group)]
            by_pitch: dict[int, set[int]] = {}
            for (pitch, _fret), lane in zip(group, group_lanes):
                by_pitch.setdefault(pitch, set()).add(lane)
            collisions = {p: ls for p, ls in by_pitch.items() if len(ls) > 1}
            assert not collisions, f"in-group collision: {collisions}"
            idx += len(group)

    def test_real_section_g_regression_descending_run_spreads_out(self):
        # Regression guard for the bug found by playtest after v4/v5: a
        # descending single-string chug run (frets 7/5/4/5/4/2, pitches
        # 52/50/49/50/49/47, "Still Searching" track 1 section [G]) still
        # clustered onto just green/red under the old per-note
        # proportional-distance formula, because deltas -2 and -3 both
        # rounded to the same lane step. Rank-ordering the group's
        # distinct pitches fixes this by construction.
        notes = [(52, 7), (50, 5), (49, 4), (50, 5), (49, 4), (47, 2)]
        lanes = _single_note_lanes(notes)
        assert len(set(lanes)) >= 3, f"squashed to {sorted(set(lanes))}: {lanes}"


class TestChordDisjoint:
    def _lanes(self, pitches):
        return _chord_lanes_sequence([tuple(sorted(pitches))])[0]

    def test_tight_interval_chord_stays_adjacent(self):
        # m2/M2/m3/M3 intervals (<=4 semitones) -> gap=1, adjacent lanes.
        lanes = self._lanes([40, 43, 46])  # gaps 3, 3 (both m3)
        assert lanes == list(range(lanes[0], lanes[0] + 3))

    def test_power_chord_interval_is_gapped(self):
        lanes = self._lanes([40, 47])  # gap == 7 semitones (P5)
        assert lanes[1] - lanes[0] == 2  # one empty lane between them

    def test_octave_interval_reduces_mod_12_to_adjacent(self):
        # An exact octave (12 semitones) reduces mod-12 to 0 -> same
        # tight-interval bucket as a unison, so it stays adjacent.
        lanes = self._lanes([40, 52])  # gap == 12 semitones (octave)
        assert lanes[1] - lanes[0] == 1

    def test_minor_seventh_interval_is_gapped_widest(self):
        lanes = self._lanes([40, 51])  # gap == 11 semitones (m7)
        assert lanes[1] - lanes[0] == 4  # widest gap

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

    def test_gradual_chord_walk_stays_one_group_unless_it_would_run_out_of_lanes(self):
        # Regression guard: chord grouping uses a ROLLING anchor (compare
        # each chord's root to the PREVIOUS chord's root), not a fixed
        # first-root anchor. A gradual walk where every step is small but
        # the first-to-last span exceeds HAND_POSITION_SEMITONES must
        # still stay ONE group PROVIDED every root can still get its own
        # lane - confirmed necessary on the real section [F] progression
        # (roots 56-57-59-61-62, each step <=4 semitones, first-to-last
        # span is 6). A fixed-first-root anchor split this partway
        # through, which meant the rank-order-across-the-group fix only
        # ever saw half the progression's roots and still crowded
        # distinct chords onto the same 1-2 lanes.
        #
        # But these 5 roots are all root+octave power chords (width 1
        # lane each), so only 4 of them can get distinct base lanes in a
        # 5-lane highway (5 - 1 = 4) - the group must split once a 5th
        # distinct root would make that impossible, rather than staying
        # one group and silently colliding two roots onto the same lane.
        chords = [(56, 68), (57, 69), (59, 71), (61, 73), (62, 74)]
        groups = _group_chords_into_hand_positions(chords)
        assert len(groups) == 2
        assert sorted({c[0] for c in groups[0]}) == [56, 57, 59, 61]
        assert [c[0] for c in groups[1]] == [62]

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
        assert "Offset = -0.25" in text
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
