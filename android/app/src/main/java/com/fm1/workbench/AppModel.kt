package com.fm1.workbench

import android.app.Application
import android.content.Intent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.fm1.workbench.audio.AudioLooper
import com.fm1.workbench.core.Dx7
import com.fm1.workbench.core.Looper
import com.fm1.workbench.core.MidiPlayer
import com.fm1.workbench.core.Pattern
import com.fm1.workbench.core.Sequencer
import com.fm1.workbench.core.StarterKit
import com.fm1.workbench.core.Track
import com.fm1.workbench.core.TrackVoice
import com.fm1.workbench.core.VoiceGen
import com.fm1.workbench.data.Assets
import com.fm1.workbench.data.Kit
import com.fm1.workbench.data.LibVoice
import com.fm1.workbench.midi.Fm1Midi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import android.content.ContentValues
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.provider.MediaStore
import com.fm1.workbench.core.Fm1Synth
import com.fm1.workbench.core.Speech
import com.fm1.workbench.core.SpeechPlayer
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** App state: MIDI connection + engine, drum sequencer, loopers, voice editor, MIDI file player, library and kits. */
class AppModel(app: Application) : AndroidViewModel(app) {
    var status by mutableStateOf<String?>(null); private set
    var library by mutableStateOf<List<LibVoice>>(emptyList()); private set
    var myVoices by mutableStateOf<List<LibVoice>>(emptyList()); private set
    var kits by mutableStateOf<List<Kit>>(emptyList()); private set
    var loading by mutableStateOf(true); private set
    var revision by mutableIntStateOf(0); private set          // bump to recompose after model edits
    var activeTrack by mutableIntStateOf(0)
    var kitName by mutableStateOf("Starter kit"); private set
    var message by mutableStateOf<String?>(null)

    private val main = android.os.Handler(android.os.Looper.getMainLooper())
    // MIDI callbacks arrive on the MIDI thread; UI state is only written on the main thread.
    val midi = Fm1Midi(app, onStatus = { s -> main.post { status = s } }, onKey = ::onFm1Key)
    val engine get() = midi.engine
    private val patDir = File(app.filesDir, "patterns").apply { mkdirs() }
    val seq = Sequencer(engine, midi.clock, loadPatternFile(File(patDir, "current.json")) ?: StarterKit.defaultPattern())
    /** The pattern being played/edited (the chain can swap it while playing). */
    val pattern: Pattern get() = seq.pattern
    val looper = Looper(engine, midi.clock)
    val audioLooper = AudioLooper(app)
    val player = MidiPlayer(engine, midi.clock) { seq.trackForGm(it) }.also { p -> p.onEnded = { main.post { touched() } } }

    private val debug = DebugControl(this).also { it.register(app) }
    private val myFile = File(app.filesDir, "my_voices.json")

    init {
        seq.chainNext = ::nextInChain
        seq.onPatternChanged = { main.post { touched() } }
        viewModelScope.launch {
            val (lib, k) = withContext(Dispatchers.IO) { Assets.library(app) to Assets.kits(app) }
            library = lib; kits = k; loading = false
            myVoices = withContext(Dispatchers.IO) { loadMine() }
            loadSpeech()                                   // speech data (dictionary, units, tagger) in the background
            if (midi.usingSoft) withContext(Dispatchers.Default) { midi.soft.warmUp() }
        }
        viewModelScope.launch { keepAliveWhilePlaying() }
    }

    fun touched() { revision++; scheduleAutosave() }

    // ---------------------------------------------------------------- pattern files, slots, chain (web-compatible JSON)
    private fun loadPatternFile(f: File): Pattern? = runCatching { com.fm1.workbench.core.PatternJson.parse(f.readText()) }.getOrNull()
    private var autosave: kotlinx.coroutines.Job? = null
    private fun scheduleAutosave() {
        autosave?.cancel()
        autosave = viewModelScope.launch(Dispatchers.IO) { delay(800); runCatching { File(patDir, "current.json").writeText(com.fm1.workbench.core.PatternJson.write(pattern)) } }
    }
    fun slotFile(n: Int) = File(patDir, "slot$n.json")
    fun slotFilled(n: Int) = slotFile(n).exists()
    fun saveSlot(n: Int) { slotFile(n).writeText(com.fm1.workbench.core.PatternJson.write(pattern)); toast("Pattern saved to slot $n"); touched() }
    fun loadSlot(n: Int) {
        val p = loadPatternFile(slotFile(n)) ?: return toast("Slot $n is empty")
        seq.pattern = p; activeTrack = 0; syncTempo(); touched(); toast("Loaded slot $n")
    }
    var chainOn by mutableStateOf(false)
    var chainSlots by mutableStateOf(listOf<Int>())
    var chainPos by mutableIntStateOf(0); private set
    private fun nextInChain(): Pattern? {
        if (!chainOn || chainSlots.isEmpty()) return null
        val next = (chainPos + 1) % chainSlots.size
        val p = loadPatternFile(slotFile(chainSlots[next])) ?: return null
        main.post { chainPos = next }
        return p
    }
    fun exportPattern(): File = File(getApplication<Application>().getExternalFilesDir(null), "fm1-pattern-${System.currentTimeMillis()}.json")
        .apply { writeText(com.fm1.workbench.core.PatternJson.write(pattern)) }
    fun importPattern(text: String, name: String) {
        runCatching { com.fm1.workbench.core.PatternJson.parse(text) }
            .onSuccess { seq.pattern = it; activeTrack = 0; syncTempo(); touched(); toast("Imported $name") }
            .onFailure { toast("Not a pattern file: ${it.message}") }
    }
    private fun syncTempo() { looper.bpm = pattern.bpm; audioLooper.bpm = pattern.bpm }
    private var lastBusy = false
    fun touchIfBusyChanged() { val b = busy; if (b != lastBusy) { lastBusy = b; touched() } }
    fun toast(s: String) { message = s }

    // ---------------------------------------------------------------- background playback
    /** Run the foreground service (screen-off playback, wakelock) while anything is playing or recording. */
    private suspend fun keepAliveWhilePlaying() {
        var idleFor = 0
        var on = false
        var micOn = false
        while (true) {
            delay(500)
            val busy = seq.playing || player.playing || looper.state in setOf(Looper.State.PLAYING, Looper.State.RECORDING, Looper.State.OVERDUBBING) ||
                audioLooper.state in setOf(AudioLooper.State.RECORDING, AudioLooper.State.PLAYING, AudioLooper.State.OVERDUBBING) || speechPlayer.playing
            idleFor = if (busy) 0 else idleFor + 1
            val mic = audioLooper.state != AudioLooper.State.EMPTY && audioLooper.state != AudioLooper.State.STOPPED
            // (re)start with the microphone type when the audio looper becomes active: Android silences background
            // capture unless the foreground service includes it
            if (busy && (!on || mic != micOn)) { startService(mic); on = true; micOn = mic }
            else if (on && idleFor >= 4) { getApplication<Application>().stopService(Intent(getApplication(), PlaybackService::class.java)); on = false; micOn = false }
        }
    }

    /** Call from user actions (Play/Record) while the app is visible: Android only grants a foreground service
     *  microphone access when it is started from the foreground, and the audio looper needs that to keep recording
     *  with the screen off. The polling loop above is only a fallback (media playback without microphone). */
    fun ensureBackgroundService() {
        val mic = audioLooper.state != AudioLooper.State.EMPTY && audioLooper.state != AudioLooper.State.STOPPED
        startService(mic)
    }

    val busy: Boolean get() = seq.playing || player.playing || looper.state in setOf(Looper.State.PLAYING, Looper.State.RECORDING, Looper.State.OVERDUBBING) ||
        audioLooper.state in setOf(AudioLooper.State.RECORDING, AudioLooper.State.PLAYING, AudioLooper.State.OVERDUBBING) || speechPlayer.playing

    fun audioRecord() { audioLooper.bpm = pattern.bpm; audioLooper.record(); ensureBackgroundService(); touched() }
    fun audioPlay() { audioLooper.play(); ensureBackgroundService(); touched() }
    fun looperRecord() { looper.record(); ensureBackgroundService(); touched() }
    fun looperPlay() { looper.play(); ensureBackgroundService(); touched() }

    private fun startService(mic: Boolean) {
        PlaybackService.onStop = { main.post { stopAll() } }
        val i = Intent(getApplication(), PlaybackService::class.java).putExtra(PlaybackService.EXTRA_MIC, mic)
        runCatching { ContextCompat.startForegroundService(getApplication(), i) }
    }

    fun stopAll() { seq.stop(); player.stop(); looper.stop(); audioLooper.stop(); speechPlayer.stop(); touched() }

    // ---------------------------------------------------------------- output, firmware, volume (persisted)
    private val prefs = app.getSharedPreferences("fm1", android.content.Context.MODE_PRIVATE)
    var output by mutableStateOf(runCatching { Fm1Midi.Output.valueOf(prefs.getString("output", "AUTO")!!) }.getOrDefault(Fm1Midi.Output.AUTO)); private set
    var firmware by mutableStateOf(prefs.getString("firmware", "stock")!!); private set
    var volume by mutableIntStateOf(prefs.getInt("volume", 100)); private set
    fun setOutputMode(o: Fm1Midi.Output) { stopAll(); output = o; midi.output = o; prefs.edit().putString("output", o.name).apply() }
    fun setFirmwareMode(fw: String) {
        firmware = fw; engine.firmware = fw; engine.forgetDevice(); midi.soft.synth.profile = fw
        prefs.edit().putString("firmware", fw).apply()
        toast(if (fw == "va") "FM-1+VA: voice changes send CC 76/78 (LFO), Volume sends CC 7" else "Firmware: M-VAVE")
    }
    /** Master volume: CC 7 on the key channel (FM-1+VA firmware, and the Software FM-1 in that profile). */
    fun setMasterVolume(v: Int) { volume = v; prefs.edit().putInt("volume", v).apply(); if (firmware == "va") engine.send(intArrayOf(0xB0 or engine.ch, 7, v)) }
    private fun applySettings() {
        midi.output = output
        engine.firmware = firmware
        midi.soft.synth.profile = firmware
    }

    // ---------------------------------------------------------------- speech
    var speech: Speech? by mutableStateOf(null); private set
    var speechLoading by mutableStateOf(false); private set
    var speechText by mutableStateOf("Hello world. I am a synthesizer, made of sine waves. Can you hear me?")
    var speechOpts by mutableStateOf(Speech.Opts(diph = "sweep", diphEdge = 0.15))      // diphthongs as one gliding note
    var speechCharacter by mutableStateOf("natural")
    var speechSource by mutableStateOf("text")                           // "text" or "rec"
    var speechResult by mutableStateOf<Speech.Result?>(null); private set
    var speechRecInfo by mutableStateOf<String?>(null); private set
    var speechRecording by mutableStateOf(false); private set
    private var recFrames: List<Speech.Frame>? = null
    val speechPlayer = SpeechPlayer(engine, midi.clock).also { it.onEnded = { main.post { touched() } } }

    fun loadSpeech() {
        if (speech != null || speechLoading) return
        speechLoading = true
        viewModelScope.launch {
            speech = withContext(Dispatchers.IO) { runCatching { Assets.speech(getApplication()) }.onFailure { toast("Speech data: ${it.message}") }.getOrNull() }
            speechLoading = false
            speechBuild()
        }
    }

    /** Build the plan for the current text or recording and options; returns the MIDI events. */
    fun speechBuild(): List<Speech.Ev> {
        val sp = speech ?: return emptyList()
        // the character preset goes on top of the sliders; "follow" only means something for a recording
        val o = sp.character(speechCharacter, speechOpts.let { if (speechSource == "text" && it.tone == "follow") it.copy(tone = "grammar") else it })
        val r = if (speechSource == "rec") {
            val fr = recFrames ?: return emptyList()
            sp.framesFromRecording(fr, if (o.tone == "flat") "flat" else "follow", o.speed)
        } else sp.speak(speechText, o)
        speechResult = r
        return sp.toEvents(sp.plan(r.frames, r.emax, o), engine.ch, fx = o.fx, fxCh = 1, sync = o.sync)
    }

    fun speak() {
        if (speechPlayer.playing) { speechPlayer.stop(); touched(); return }
        val ev = speechBuild()
        if (ev.isEmpty()) return toast(if (speech == null) "Speech data is still loading" else "Nothing to say")
        seq.stop(); player.stop()
        speechPlayer.play(ev); ensureBackgroundService(); touched()
    }

    @android.annotation.SuppressLint("MissingPermission")          // the Speech screen asks for RECORD_AUDIO first
    fun speechRecordToggle() {
        if (speechRecording) { speechRecording = false; return }
        speechRecording = true
        speechRecInfo = "Recording… (tap again to stop, 10 s max)"
        viewModelScope.launch(Dispatchers.Default) {
            val sr = 16000
            val min = AudioRecord.getMinBufferSize(sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_FLOAT)
            val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_FLOAT, maxOf(min, 4096 * 4))
            val data = FloatArray(sr * 10); var n = 0
            rec.startRecording()
            while (speechRecording && n < data.size) {
                val k = rec.read(data, n, minOf(1024, data.size - n), AudioRecord.READ_BLOCKING)
                if (k <= 0) break
                n += k
            }
            rec.stop(); rec.release()
            main.post { speechRecording = false; speechRecInfo = "Analysing…" }
            val fr = speech?.analyse(data.copyOf(n), sr)
            main.post {
                recFrames = fr; speechSource = "rec"
                speechRecInfo = "Microphone: %.1f s".format(n / sr.toDouble())
                if (speechOpts.tone == "grammar") speechOpts = speechOpts.copy(tone = "follow")
                speechBuild(); touched()
            }
        }
    }

    /** Render with the Software FM-1 and save as a WAV in Music/FM-1 (MediaStore). */
    fun speechSaveWav() {
        val ev = speechBuild()
        if (ev.isEmpty()) return
        val label = if (speechSource == "text") speechText.take(32).replace(Regex("\\W+"), "_") else "recording"
        viewModelScope.launch(Dispatchers.Default) {
            val sr = 44100
            val synth = Fm1Synth(sr).apply { profile = firmware }
            for (e in ev) synth.queue(e.b, Math.round(e.t / 1000 * sr))
            val n = ((ev.last().t / 1000 + 0.4) * sr).toInt()
            val out = FloatArray(n); val blk = FloatArray(256)
            var f = 0
            while (f < n) { synth.render(blk, f.toLong()); blk.copyInto(out, f, 0, minOf(256, n - f)); f += 256 }
            val peak = maxOf(1e-9f, out.maxOf { kotlin.math.abs(it) })
            val wav = ByteBuffer.allocate(44 + n * 2).order(ByteOrder.LITTLE_ENDIAN)
            wav.put("RIFF".toByteArray()).putInt(36 + n * 2).put("WAVEfmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
                .putInt(sr).putInt(sr * 2).putShort(2).putShort(16).put("data".toByteArray()).putInt(n * 2)
            for (x in out) wav.putShort((x / peak * 0.9f * 32767).toInt().toShort())
            val name = "${label}_fm1.wav"
            val ok = runCatching {
                val cv = ContentValues().apply {
                    put(MediaStore.Audio.Media.DISPLAY_NAME, name); put(MediaStore.Audio.Media.MIME_TYPE, "audio/wav")
                    put(MediaStore.Audio.Media.RELATIVE_PATH, "Music/FM-1")
                }
                val res = getApplication<Application>().contentResolver
                val uri = res.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, cv)!!
                res.openOutputStream(uri)!!.use { it.write(wav.array()) }
            }.isSuccess
            main.post { toast(if (ok) "Saved Music/FM-1/$name" else "Could not save the WAV") }
        }
    }
    init { applySettings() }             // after the settings properties above exist


    // ---------------------------------------------------------------- FM-1 keys
    // The unit already sounds them; the looper records them with the loaded voice.
    private fun onFm1Key(on: Boolean, note: Int, vel: Int) {
        if (on) looper.noteOn(note, vel, engine.dev?.copyOf(155), echo = true) else looper.noteOff(note)
    }

    // ---------------------------------------------------------------- drums
    fun hitPad(ti: Int, vel: Int = 110) {
        val tr = pattern.tracks.getOrNull(ti) ?: return
        val v = tr.voices.firstOrNull() ?: return
        seq.hitPad(ti, vel)
        looper.noteOn(tr.note, vel, v.effective())
        viewModelScope.launch { delay(tr.gate.toLong()); looper.noteOff(tr.note) }   // pads are momentary
    }

    fun toggleStep(ti: Int, i: Int) {
        val st = pattern.tracks.getOrNull(ti)?.steps?.getOrNull(i) ?: return
        st.on = !st.on; touched()
    }

    fun togglePlay() {
        if (seq.playing) seq.stop() else {
            if (chainOn && chainSlots.isNotEmpty()) { loadPatternFile(slotFile(chainSlots[0]))?.let { seq.pattern = it }; chainPos = 0 }
            seq.start(); ensureBackgroundService()
        }
        touched()
    }

    fun setBpm(b: Int) { pattern.bpm = b.coerceIn(30, 300); looper.bpm = pattern.bpm; audioLooper.bpm = pattern.bpm; touched() }

    /** Put a kit's voices on the tracks (match by name, then role, else append); steps are kept. */
    fun applyKit(kit: Kit) {
        val used = HashSet<Int>()
        for (kt in kit.tracks) {
            var ti = pattern.tracks.indices.firstOrNull { it !in used && pattern.tracks[it].name.equals(kt.name, true) } ?: -1
            if (ti < 0) ti = pattern.tracks.indices.firstOrNull { it !in used && pattern.tracks[it].role == kt.role } ?: -1
            if (ti < 0) { pattern.tracks += Track(kt.name, kt.role, kt.note); ti = pattern.tracks.size - 1 }
            used += ti
            pattern.tracks[ti].apply {
                name = kt.name; role = kt.role; note = kt.note; gate = kt.gate; mode = "fixed"
                voices.clear(); voices += TrackVoice(kt.voice.name, kt.voice.vced.copyOf(), kt.voice.macros.toMutableMap(), kt.voice.measured)
            }
        }
        kitName = kit.name; touched()
    }

    fun addVoiceToTrack(v: LibVoice, ti: Int = activeTrack) {
        val tr = pattern.tracks.getOrNull(ti) ?: return
        if (tr.voices.size >= 8) { toast("A track holds up to 8 voices"); return }
        tr.voices += TrackVoice(v.name, v.vced.copyOf()); touched(); toast("Added ${v.name} to ${tr.name}")
    }

    fun audition(vced: IntArray, note: Int = 60, ms: Long = 450) {
        engine.hit(vced, note, 110, midi.clock() + 5, ms.toDouble(), withName = true)
        looper.noteOn(note, 110, vced)
        viewModelScope.launch { delay(ms); looper.noteOff(note) }
    }

    fun panic() { stopAll(); engine.panic(); if (midi.usingSoft) midi.soft.reset(); touched() }

    // ---------------------------------------------------------------- editor
    var edit: IntArray = Dx7.initVoice(); private set
    var opOn = BooleanArray(6) { true }; private set
    private val undoStack = ArrayDeque<IntArray>()
    var morphA: IntArray? = null; var morphB: IntArray? = null
    /** Where "Save to …" in the editor writes back (a drum-track voice), or null. */
    var editTarget by mutableStateOf<Pair<String, (IntArray) -> Unit>?>(null)

    fun pushUndo() { undoStack.addLast(edit.copyOf()); if (undoStack.size > 50) undoStack.removeFirst() }
    fun undo() { undoStack.removeLastOrNull()?.let { edit = it; sendEdit() } }
    val canUndo get() = undoStack.isNotEmpty()

    /** Replace the editor voice and load it into the FM-1 (all parameters, via diffs). */
    fun setEdit(v: IntArray, target: Pair<String, (IntArray) -> Unit>? = null, keepTarget: Boolean = false) {
        edit = Dx7.clamp(v); opOn = BooleanArray(6) { true }
        if (!keepTarget) editTarget = target
        sendEdit()
    }

    fun sendEdit() { engine.setVoice(edit, opMask = com.fm1.workbench.core.Dx7.opMaskValue(opOn), withName = true); touched() }

    /** Live single-parameter edit (goes to the FM-1 right away; applies to the next note). */
    fun setParam(i: Int, value: Int) {
        val v = value.coerceIn(0, Dx7.maxOf(i))
        if (edit[i] == v) return
        edit[i] = v; engine.setParam(i, v); touched()
    }

    fun toggleOp(n: Int) { pushUndo(); opOn[n - 1] = !opOn[n - 1]; engine.setParam(155, Dx7.opMaskValue(opOn)); touched() }

    fun randomize(style: String) { pushUndo(); setEdit(VoiceGen.random(style), keepTarget = true); audition(edit) }
    fun mutate(amount: Int) { pushUndo(); setEdit(VoiceGen.mutate(edit, amount), keepTarget = true); audition(edit) }
    fun morph(t: Float) {
        val a = morphA ?: return; val b = morphB ?: return
        edit = VoiceGen.morph(a, b, t.toDouble()); engine.setVoice(edit, opMask = Dx7.opMaskValue(opOn)); touched()
    }

    fun editTrackVoice(tr: Track, v: TrackVoice) {
        setEdit(v.effective(), "${tr.name} · ${v.name}" to { vced: IntArray ->
            v.vced = vced.copyOf(); v.macros.clear(); v.name = Dx7.name(vced).trim(); touched()
        })
    }

    fun saveEditToTarget() { editTarget?.second?.invoke(edit.copyOf()); toast("Saved to ${editTarget?.first}"); editTarget = null }

    // ---------------------------------------------------------------- my patches (persistent)
    private fun loadMine(): List<LibVoice> = runCatching {
        val arr = JSONArray(myFile.readText())
        List(arr.length()) { i ->
            val o = arr.getJSONObject(i)
            val vv = o.getJSONArray("vced")
            LibVoice(-1 - i, o.getString("name"), "My patches", o.optJSONArray("tags")?.let { t -> List(t.length()) { t.getString(it) } } ?: emptyList(),
                null, Dx7.clamp(IntArray(155) { vv.getInt(it) }))
        }
    }.getOrDefault(emptyList())

    fun saveEditToMine() {
        val name = Dx7.name(edit).trim().ifEmpty { "MY PATCH" }
        val list = myVoices + LibVoice(-1 - myVoices.size, name, "My patches", emptyList(), null, edit.copyOf())
        myVoices = list
        viewModelScope.launch(Dispatchers.IO) {
            myFile.writeText(JSONArray(list.map { v -> JSONObject().put("name", v.name).put("tags", JSONArray(v.tags)).put("vced", JSONArray(v.vced.toList())) }).toString())
        }
        toast("Saved \"$name\" to My patches")
    }

    fun exportEditSyx(): File {
        val bytes = Dx7.vcedSysex(edit).map { it.toByte() }.toByteArray()
        val f = File(getApplication<Application>().getExternalFilesDir(null), Dx7.name(edit).trim().replace(Regex("[^A-Za-z0-9_-]+"), "_") + ".syx")
        f.writeBytes(bytes); return f
    }

    // ---------------------------------------------------------------- player
    fun loadMidi(bytes: ByteArray, name: String) {
        runCatching { player.load(bytes, name) }.onSuccess { touched(); toast("Loaded $name: ${player.song!!.notes.size} notes") }
            .onFailure { toast("$name: ${it.message}") }
    }

    fun loadDemo() = loadMidi(getApplication<Application>().assets.open("demo.mid").readBytes(), "demo.mid")

    fun togglePlayer() {
        if (player.playing) player.stop() else { seq.stop(); player.start(); ensureBackgroundService() }
        touched()
    }

    override fun onCleared() {
        debug.unregister(getApplication()); stopAll(); looper.clear(); audioLooper.release(); midi.release()
        getApplication<Application>().stopService(Intent(getApplication(), PlaybackService::class.java))
    }
}
