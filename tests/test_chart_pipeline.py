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
    def test_tick_conversion_and_lane_mod(self):
        notes = map_notes([_note(0, pitch=62, fret=5), _note(960, pitch=63, fret=6)])
        assert notes[0].tick == 0 and notes[0].lanes == [62 % 5]
        assert notes[1].tick == CHART_RESOLUTION and notes[1].lanes == [63 % 5]

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
