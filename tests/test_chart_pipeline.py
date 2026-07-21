"""Tests for the blend -> map -> emit pipeline (M2/M3)."""
from __future__ import annotations

from shred2chart.blend import blend_tracks
from shred2chart.chart_writer import build_chart
from shred2chart.mapper import CHART_RESOLUTION, map_notes

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
        # tapped notes (2 + 222 = wait: 2 notes + 2 flags * 2 weight = 6) -> lead wins.
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
    def test_tick_conversion_and_contour_lanes(self):
        # With contour-based mapping, two adjacent pitches in the same phrase
        # map to lanes 0 and 1 (the contour spreads them across the neck).
        notes = map_notes([_note(0, pitch=62, fret=5), _note(960, pitch=63, fret=6)])
        assert notes[0].tick == 0 and notes[0].lanes == [0]   # first note anchors to 0
        assert notes[1].tick == CHART_RESOLUTION and notes[1].lanes == [1]  # next pitch up

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

    def test_chord_all_notes_kept_on_distinct_lanes(self):
        # Chord interval-spread voicing was removed: every same-tick note
        # gets its own lane via the distinct-lane guarantee (no capping,
        # no interval-based spreading).
        chord = [
            _note(0, pitch=40, string=1, fret=5, chord_id=0),
            _note(0, pitch=47, string=2, fret=5, chord_id=0),
            _note(0, pitch=52, string=3, fret=5, chord_id=0),
            _note(0, pitch=59, string=4, fret=5, chord_id=0),
        ]
        notes = map_notes(chord)
        assert len(notes) == 1
        lanes = notes[0].lanes
        assert len(lanes) == 4  # no note loss, no 3-lane cap
        assert len(set(lanes)) == 4  # all distinct
        assert all(0 <= lane <= 4 for lane in lanes)

    def test_power_chord_two_distinct_lanes(self):
        # No interval-spread voicing anymore: a power chord is simply two
        # simultaneous notes on two distinct lanes (anchor from the contour
        # cursor, second note nearest-free — adjacency is fine now).
        chord = [
            _note(0, pitch=40, string=1, fret=5, chord_id=0),
            _note(0, pitch=47, string=2, fret=5, chord_id=0),
        ]
        notes = map_notes(chord)
        assert len(notes) == 1
        lanes = notes[0].lanes
        assert len(lanes) == 2
        assert len(set(lanes)) == 2

    def test_different_chords_never_repeat_same_lanes(self):
        # Two power chords a minor 3rd apart (e.g. C5 and Eb5): the contour
        # window naturally places them on different lanes as it tracks the
        # ascending pitch motion.
        notes = []
        for i, root in enumerate([36, 39]):
            notes += [
                _note(i * 960, pitch=root, string=1, fret=0, chord_id=i),
                _note(i * 960, pitch=root + 7, string=2, fret=2, chord_id=i),
            ]
        mapped = map_notes(notes)
        assert len(mapped) == 2
        assert mapped[0].lanes != mapped[1].lanes

    def test_repeated_identical_chord_keeps_lanes(self):
        # Chugging the same chord twice: identical pitches keep identical lanes.
        notes = []
        for i in range(2):
            notes += [
                _note(i * 960, pitch=36, string=1, fret=0, chord_id=i),
                _note(i * 960, pitch=43, string=2, fret=2, chord_id=i),
            ]
        mapped = map_notes(notes)
        assert len(mapped) == 2
        assert mapped[0].lanes == mapped[1].lanes

    def test_wide_chords_no_note_loss(self):
        # Distinct-lane guarantee: every note in a same-tick group keeps
        # its own lane, regardless of chord width (fretted notes only —
        # see the xfail below for the open-chug interaction).
        notes = []
        for i, root in enumerate([36, 38]):
            notes += [
                _note(i * 960, pitch=root, string=1, fret=2, chord_id=i),
                _note(i * 960, pitch=root + 10, string=2, fret=2, chord_id=i),
                _note(i * 960, pitch=root + 20, string=3, fret=2, chord_id=i),
            ]
        mapped = map_notes(notes)
        assert len(mapped) == 2
        assert all(len(n.lanes) == 3 for n in mapped)  # no note loss
        assert all(max(n.lanes) <= 4 for n in mapped)   # all lanes in range

        # 2-note full-width (octave span): both lanes are valid.
        notes = []
        for i, root in enumerate([36, 38]):
            notes += [
                _note((i + 10) * 960, pitch=root, string=1, fret=2, chord_id=i + 10),
                _note((i + 10) * 960, pitch=root + 12, string=3, fret=2, chord_id=i + 10),
            ]
        mapped = map_notes(notes)
        assert len(mapped) == 2
        assert all(len(n.lanes) == 2 for n in mapped)

    def test_ascending_chord_progression_avoids_ceiling_lock(self):
        # Six power chords marching up the neck (fret > 0, not on the
        # chug string, so this exercises real k=2 chord-shape scoring,
        # not the single-note contour path). Two invariants matter here:
        # consecutive shapes must never repeat (the progression keeps
        # moving instead of collapsing onto one pair), and the shapes
        # must not just monotonically pile up and stay pinned at the
        # ceiling for the rest of the run — the concrete "Blue+Orange,
        # Blue+Orange" flattening bug from the handoff.
        notes = []
        for i, root in enumerate([40, 42, 44, 46, 48, 50]):
            notes += [
                _note(i * 960, pitch=root, string=1, fret=5, chord_id=i),
                _note(i * 960, pitch=root + 7, string=2, fret=7, chord_id=i),
            ]
        mapped = map_notes(notes)
        assert len(mapped) == 6
        shapes = [tuple(n.lanes) for n in mapped]
        assert all(shapes[i] != shapes[i - 1] for i in range(1, len(shapes)))
        assert any(max(shape) < 4 for shape in shapes[3:])  # not pinned at the ceiling forever

    def test_real_repeated_chord_keeps_same_shape(self):
        # Same power chord (fret > 0, k=2) struck three times in a row:
        # identical pitch content must keep an identical lane shape, not
        # just for the k=1 chug-bypass case covered elsewhere.
        notes = []
        for i in range(3):
            notes += [
                _note(i * 960, pitch=40, string=1, fret=5, chord_id=i),
                _note(i * 960, pitch=47, string=2, fret=7, chord_id=i),
            ]
        mapped = map_notes(notes)
        assert len(mapped) == 3
        assert mapped[0].lanes == mapped[1].lanes == mapped[2].lanes

    def test_cursor_resync_does_not_drift_over_long_progression(self):
        # Stress test for the resync mechanism in _assign_group_lanes
        # (contour._lane_cursor += chosen_anchor_lane - anchor_preferred_lane):
        # an adversarial ~250-chord progression (long ascending/descending
        # runs, a plateau, a rest >= 1 bar, a section-marker reset, and a
        # zig-zag) that forces frequent scoring overrides of the raw
        # cursor's preferred anchor lane. Compares the actual anchor lane
        # chosen for each chord against a baseline built by running the
        # exact same anchor-pitch/tick/reset sequence through map_notes as
        # single notes (k=1, the byte-for-byte-unchanged path with zero
        # chord-scoring override) -- i.e. what the raw wraparound cursor
        # alone would have produced.
        #
        # If resync compounded (each override nudging the *next* one
        # further in the same direction), the gap between actual and
        # baseline would grow as the run progresses. It structurally can't:
        # future step sizes come only from real pitch intervals, never
        # from the cursor's current absolute value, so every resync is a
        # one-time phase-shift, not an accumulating error term. Assert
        # that empirically: the back half of the run isn't measurably
        # worse than the front half.
        notes = []
        anchor_pitches = []
        ticks = []
        section_ticks = []
        tick = 0
        pitch = 40

        def emit(p, t):
            notes.append(_note(t, pitch=p, string=1, fret=5))
            notes.append(_note(t, pitch=p + 7, string=2, fret=7))
            anchor_pitches.append(p)
            ticks.append(t)

        for _ in range(80):  # long ascending run
            emit(pitch, tick)
            pitch += 2
            tick += 960
        tick += 3840 + 10  # rest >= 1 bar: triggers the existing auto-reset
        for _ in range(60):  # long descending run, bigger steps
            emit(pitch, tick)
            pitch -= 3
            tick += 960
        for _ in range(30):  # plateau: identical chord repeated
            emit(pitch, tick)
            tick += 960
        tick += 960
        section_ticks.append(tick)  # explicit phrase-boundary reset
        for i in range(60):  # ascending run with periodic octave leaps
            emit(pitch, tick)
            pitch += 12 if i % 10 == 9 else 2
            tick += 960

        mapped = map_notes(notes, section_ticks=section_ticks)
        assert len(mapped) == len(anchor_pitches)
        actual_lanes = [min(n.lanes) for n in mapped]

        baseline_notes = [_note(t, pitch=p, string=1, fret=5) for p, t in zip(anchor_pitches, ticks)]
        baseline = map_notes(baseline_notes, section_ticks=section_ticks)
        baseline_lanes = [n.lanes[0] for n in baseline]

        deltas = [a - b for a, b in zip(actual_lanes, baseline_lanes)]
        half = len(deltas) // 2
        front_mean = sum(abs(d) for d in deltas[:half]) / half
        back_mean = sum(abs(d) for d in deltas[half:]) / (len(deltas) - half)
        assert back_mean <= front_mean + 1.0  # no systematic growth over the run

    def test_open_chug_chord_keeps_all_notes(self):
        # Chord containing a fret-0 note on the chug string plus two
        # fretted notes: all three should survive as distinct lanes
        # (one OPEN + two fretted).
        chord = [
            _note(0, pitch=36, string=1, fret=0, chord_id=0),
            _note(0, pitch=46, string=2, fret=0, chord_id=0),
            _note(0, pitch=56, string=3, fret=0, chord_id=0),
        ]
        mapped = map_notes(chord)
        assert len(mapped) == 1
        assert len(mapped[0].lanes) == 3

    def test_flags_and_sustain_threshold(self):
        ir = [
            _note(0, pitch=41, fret=6, duration=IR_QUARTER // 4, hammer_on=True),
            _note(960, pitch=43, fret=8, duration=IR_QUARTER // 4, tap=True),
        ]
        notes = map_notes(ir)
        assert notes[0].forced is True and notes[0].tap is False
        assert notes[1].tap is True
        assert notes[0].sustain == 0  # sixteenth note -> below sustain threshold


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
        # Contour-based mapping: pitch 40 anchors to lane 0; pitch 41 (adjacent
        # semitone within the same phrase) maps to lane 1.
        # 2-quarter sustain (384 ticks) trimmed by 1/32-gap (24) = 360.
        assert "0 = N 0 360" in text
        assert "384 = N 1 0" in text  # pitch 41 -> lane 1 via contour
        assert "384 = N 5 0" in text  # forced flag at same tick

    def test_metadata_escaping(self):
        # Special chars in title/artist are escaped so downstream parsers
        # don't break on backslash or double-quote.
        ir = [_note(0, pitch=40, fret=5)]
        chart_notes = map_notes(ir)
        text = build_chart(
            title='Song "With" Quotes',
            artist="Artist\\With\\Backslash",
            tempo_events=[{"tick": 0, "type": "tempo", "bpm": 120}],
            sections=[],
            chart_notes=chart_notes,
        )
        assert r'Name = "Song \"With\" Quotes"' in text
        assert r'Artist = "Artist\\With\\Backslash"' in text
