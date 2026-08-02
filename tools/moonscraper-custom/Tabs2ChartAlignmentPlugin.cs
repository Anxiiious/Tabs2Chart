// Runtime plugin used by the no-Unity, BepInEx-based custom MoonScraper copy.
using System;
using BepInEx;
using BepInEx.Logging;
using UnityEngine;
using MoonscraperEngine.Audio;
using MoonscraperChartEditor.Song;

[BepInPlugin("dev.tabs2chart.alignment", "Tabs2Chart Alignment Guide", "1.0.0")]
public class Tabs2ChartAlignmentPlugin : BaseUnityPlugin
{
    void Start()
    {
        string[] args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length; ++i)
        {
            if (args[i] == "--tabs2chart-manifest")
            {
                gameObject.AddComponent<Tabs2ChartAlignmentGuide>();
                Logger.LogInfo("Tabs2Chart manifest detected; alignment guide enabled.");
                return;
            }
        }

        gameObject.AddComponent<Tabs2ChartMeasureCounter>();
        Logger.LogInfo("Tabs2Chart measure counter enabled.");
    }
}

public class Tabs2ChartAlignmentGuide : MonoBehaviour
{
    // Not cached in Start(): BepInEx plugins can initialize before
    // ChartEditor.Instance is assigned by the game's own scene bootstrap.
    // A one-time capture would freeze this at null forever, silently
    // no-opping every button through AdjustOffset's null guard with no
    // error logged - re-reading the singleton live avoids that entirely.
    ChartEditor editor { get { return ChartEditor.Instance; } }
    static readonly ManualLogSource log = BepInEx.Logging.Logger.CreateLogSource("Tabs2Chart Alignment Guide");
    bool visible = true;
    bool waveformEnabled;
    bool analysisRequested;
    bool transientFound;
    float transientSeconds;
    string analysisStatus = "Waiting for chart audio...";
    Rect windowRect = new Rect(340, 52, 360, 255);

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.F8))
            visible = !visible;

        if (!waveformEnabled)
        {
            WaveformDraw waveform = FindObjectOfType<WaveformDraw>();
            if (waveform != null && waveform.waveformSelect != null)
            {
                waveform.waveformSelect.value = 1;
                waveformEnabled = true;
            }
        }

        if (!analysisRequested && editor != null && editor.currentSongAudio != null)
        {
            SampleData sample = editor.currentSongAudio.GetSampleData(Song.AudioInstrument.Song);
            if (sample != null && !sample.IsLoading && sample.dataLength > 0)
            {
                analysisRequested = true;
                sample.ReadData(FindFirstTransient);
            }
        }
    }

    void FindFirstTransient(float[] data, float length)
    {
        if (data == null || data.Length < 4 || length <= 0)
        {
            analysisStatus = "No waveform data is available.";
            return;
        }

        float maximum = 0;
        for (int i = 1; i < data.Length; i += 2)
            maximum = Mathf.Max(maximum, Mathf.Abs(data[i]));

        float threshold = Mathf.Max(0.025f, maximum * 0.16f);
        float previous = 0;
        for (int i = 1; i < data.Length; i += 2)
        {
            float level = Mathf.Abs(data[i]);
            if (level >= threshold && level - previous >= threshold * 0.30f)
            {
                transientSeconds = ((float)i / data.Length) * length;
                transientFound = true;
                analysisStatus = "Detected audio attack";
                return;
            }
            previous = Mathf.Max(level, previous * 0.86f);
        }

        analysisStatus = "No clear audio attack was detected.";
    }

    float FirstNoteTime()
    {
        if (editor == null || editor.currentSong == null || editor.currentChart == null ||
            editor.currentChart.notes.Count == 0)
            return -1;
        return editor.currentSong.TickToTime(
            editor.currentChart.notes[0].tick,
            editor.currentSong.resolution
        );
    }

    void AdjustOffset(float seconds)
    {
        if (editor == null || editor.currentSong == null)
        {
            log.LogWarning("Button click ignored: " + (editor == null ? "ChartEditor.Instance is null" : "currentSong is null") + ".");
            return;
        }
        float before = editor.currentSong.offset;
        editor.currentSong.offset += seconds;
        ChartEditor.isDirty = true;
        log.LogInfo("Offset adjusted by " + (seconds * 1000).ToString("0") + " ms: " +
            (before * 1000).ToString("0") + " -> " + (editor.currentSong.offset * 1000).ToString("0") + " ms.");
    }

    void OnGUI()
    {
        DrawMeasureCounter();

        if (!visible)
        {
            GUI.Box(new Rect(340, 52, 225, 27), "Tabs2Chart alignment: F8 to show");
            return;
        }
        windowRect = GUI.Window(GetInstanceID(), windowRect, DrawWindow, "Tabs2Chart Audio Alignment");
    }

    void DrawMeasureCounter()
    {
        if (editor == null || editor.currentSong == null)
            return;

        GUIStyle style = new GUIStyle(GUI.skin.label);
        style.fontSize = 20;
        style.normal.textColor = Color.yellow;
        GUI.Label(
            new Rect(20, 20, 330, 28),
            "Measure " + GetMeasureNumber(editor.currentSong, editor.currentTickPos) +
                "  (tick " + editor.currentTickPos + ")",
            style
        );
    }

    // Counts the same measure starts DrawBeatLines renders. A time-signature
    // change itself is always a new visible measure line, including malformed
    // charts that place it mid-measure, so completed segments use ceiling
    // division while the current segment uses floor division.
    internal static int GetMeasureNumber(Song song, uint tick)
    {
        var timeSignatures = song.timeSignatures;
        if (timeSignatures.Count == 0)
            return 1;

        int measureNumber = 1;
        for (int i = 0; i < timeSignatures.Count; ++i)
        {
            TimeSignature ts = timeSignatures[i];
            if (tick < ts.tick)
                break;

            uint measureLength = ts.GetMeasureInfo().measureLine.tickGap;
            if (measureLength == 0)
                continue;

            uint nextTSTick = i + 1 < timeSignatures.Count
                ? timeSignatures[i + 1].tick
                : uint.MaxValue;
            if (tick < nextTSTick)
            {
                measureNumber += (int)((tick - ts.tick) / measureLength);
                break;
            }

            uint segmentLength = nextTSTick - ts.tick;
            measureNumber += (int)((segmentLength + measureLength - 1) / measureLength);
        }
        return measureNumber;
    }

    void DrawWindow(int id)
    {
        float firstNote = FirstNoteTime();
        GUILayout.Label("Waveform is enabled automatically. Match its first strong attack to the first playable note.");
        GUILayout.Space(4);
        GUILayout.Label(firstNote >= 0
            ? "First playable note: " + firstNote.ToString("0.000") + " s"
            : "First playable note: unavailable");
        GUILayout.Label(transientFound
            ? analysisStatus + ": " + transientSeconds.ToString("0.000") + " s"
            : analysisStatus);
        GUILayout.Label(editor != null && editor.currentSong != null
            ? "Current chart offset: " + (editor.currentSong.offset * 1000).ToString("0") + " ms"
            : "Current chart offset: unavailable");

        if (transientFound && firstNote >= 0)
        {
            float delta = transientSeconds - editor.currentSong.offset - firstNote;
            GUILayout.Label("Guide delta: " + (delta * 1000).ToString("+0;-0;0") + " ms");
            if (GUILayout.Button("Apply detected alignment"))
                AdjustOffset(delta);
        }

        GUILayout.Space(4);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("-100 ms")) AdjustOffset(-0.100f);
        if (GUILayout.Button("-10 ms")) AdjustOffset(-0.010f);
        if (GUILayout.Button("+10 ms")) AdjustOffset(0.010f);
        if (GUILayout.Button("+100 ms")) AdjustOffset(0.100f);
        GUILayout.EndHorizontal();
        GUILayout.Label("F8 hides/shows this guide. Save the chart to keep the offset.");
        GUI.DragWindow(new Rect(0, 0, 10000, 22));
    }
}

// The alignment guide activates only for manifest-backed Tabs2Chart imports,
// but the counter is useful when manually comparing any chart with a tab.
public class Tabs2ChartMeasureCounter : MonoBehaviour
{
    void OnGUI()
    {
        ChartEditor editor = ChartEditor.Instance;
        if (editor == null || editor.currentSong == null)
            return;

        GUIStyle style = new GUIStyle(GUI.skin.label);
        style.fontSize = 20;
        style.normal.textColor = Color.yellow;
        GUI.Label(
            new Rect(20, 20, 330, 28),
            "Measure " + Tabs2ChartAlignmentGuide.GetMeasureNumber(editor.currentSong, editor.currentTickPos) +
                "  (tick " + editor.currentTickPos + ")",
            style
        );
    }
}
