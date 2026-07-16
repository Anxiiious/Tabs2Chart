"""Round-trip test for tempo/time-signature extraction.

Builds a small song with PyGuitarPro, writes it to a real .gp5 file,
parses it back, and checks that dump_tempo_events recovers what we put in.
"""
from __future__ import annotations

import guitarpro as gp

from shred2chart.tempo import dump_tempo_events


def test_dump_tempo_events_round_trip(tmp_path):
    song = gp.Song()
    song.tempo = 140
    song.title = "Test Song"

    header1 = song.measureHeaders[0]
    header2 = gp.MeasureHeader(number=2, start=header1.end)
    header2.timeSignature = gp.TimeSignature(numerator=3, denominator=gp.Duration(value=4))
    song.addMeasureHeader(header2)

    track = song.tracks[0]
    track.measures.append(gp.Measure(track, header2))

    voice = track.measures[1].voices[0]
    beat = gp.Beat(voice)
    beat.effect.mixTableChange = gp.MixTableChange(tempo=gp.MixTableItem(value=90))
    voice.beats.append(beat)

    out_file = tmp_path / "test.gp5"
    gp.write(song, str(out_file), version=(5, 1, 0))

    events = dump_tempo_events(out_file)

    tempos = [(e["tick"], e["bpm"]) for e in events if e["type"] == "tempo"]
    assert tempos[0] == (0, 140)
    assert (header2.start, 90) in tempos

    time_signatures = [e for e in events if e["type"] == "time_signature"]
    assert any(
        e["tick"] == header2.start and e["numerator"] == 3 and e["denominator"] == 4
        for e in time_signatures
    )
