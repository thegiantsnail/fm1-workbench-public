package com.fm1.workbench.core

/**
 * MIDI file player (port of player.js). Each channel can have its own voice; the FM-1 has one voice at a time, so
 * voices are switched between notes with parameter diffs (held notes keep their sound). Channel 10 can play through
 * the drum kit (GM note -> track by role). Timing: song ms -> engine time via [rate] (tempo %).
 */
class MidiPlayer(private val engine: Engine, private val now: () -> Double, private val kit: (Int) -> Track?) : Engine.Producer {
    class Chan(val ch: Int, var on: Boolean = true, var voice: IntArray? = null, var voiceName: String? = null, var kit: Boolean = false)

    var song: Smf.Song? = null; private set
    var name = ""; private set
    val chans = LinkedHashMap<Int, Chan>()
    @Volatile var playing = false; private set
    var loop = false
    var transpose = 0
    var velPct = 100
    var passBend = false
    var tempoPct = 100
        set(v) { val cur = if (playing) songAt(now()) else pos; field = v.coerceIn(25, 200); rate = field / 100.0; if (playing) t0 = now() - cur / rate }
    private var rate = 1.0
    private var t0 = 0.0
    private var pos = 0.0
    private var ni = 0
    private var ci = 0
    var onEnded: (() -> Unit)? = null

    val duration get() = song?.duration ?: 0.0
    fun position(): Double = if (playing) songAt(now()).coerceAtMost(duration) else pos

    private fun songAt(t: Double) = (t - t0) * rate
    private fun engineAt(ms: Double) = t0 + ms / rate

    fun load(bytes: ByteArray, fileName: String) {
        stop()
        val s = Smf.parse(bytes)
        require(s.notes.isNotEmpty()) { "no notes in file" }
        song = s; name = fileName; pos = 0.0
        chans.clear()
        for (c in s.chans.values) chans[c.ch] = Chan(c.ch, on = true, kit = c.ch == 9)
    }

    private fun seekIndex(ms: Double) {
        val s = song ?: return
        ni = s.notes.indexOfFirst { it.t >= ms }.let { if (it < 0) s.notes.size else it }
        ci = s.ctrl.indexOfFirst { it.t >= ms }.let { if (it < 0) s.ctrl.size else it }
    }

    fun start() = synchronized(engine) {
        val s = song ?: return
        if (playing) return
        if (pos >= s.duration) pos = 0.0
        rate = tempoPct / 100.0
        t0 = now() + 60 - pos / rate
        seekIndex(pos)
        playing = true
        engine.start(this)
    }

    fun stop(ended: Boolean = false) = synchronized(engine) {
        if (!playing) return
        playing = false
        pos = if (ended) 0.0 else songAt(now()).coerceIn(0.0, duration)
        engine.stop(this)
        engine.send(intArrayOf(0xB0 or engine.ch, 64, 0))
        if (passBend) engine.send(intArrayOf(0xE0 or engine.ch, 0, 64))
    }

    fun seek(ms: Double) {
        val was = playing
        if (was) stop()
        pos = ms.coerceIn(0.0, duration)
        if (was) start()
    }

    override fun schedule(horizon: Double) {
        val s = song ?: return
        var guard = 0
        while (guard++ < 4) {
            val songH = songAt(horizon)
            while (ci < s.ctrl.size && s.ctrl[ci].t < songH) {
                val c = s.ctrl[ci++]; val cs = chans[c.ch]
                if (cs == null || !cs.on || cs.kit) continue
                val t = engineAt(c.t)
                if (c.kind == "cc" && (c.a == 64 || c.a == 1)) engine.planSend(intArrayOf(0xB0 or engine.ch, c.a, c.b), t)
                else if (c.kind == "bend" && passBend) engine.planSend(intArrayOf(0xE0 or engine.ch, c.a, c.b), t)
            }
            while (ni < s.notes.size && s.notes[ni].t < songH) {
                val n = s.notes[ni++]; val cs = chans[n.ch]
                if (cs == null || !cs.on) continue
                val t = engineAt(n.t); val gate = n.dur!! / rate
                val vel = Math.round(n.vel * velPct / 100.0).toInt().coerceIn(1, 127)
                if (cs.kit) {
                    val tr = kit(n.note) ?: continue
                    engine.planHit(tr.voices[0].effective(), tr.note, vel, t, minOf(gate, tr.gate.toDouble()))
                } else {
                    val note = n.note + transpose
                    if (note in 0..127) engine.planHit(cs.voice, note, vel, t, gate)
                }
            }
            if (ni >= s.notes.size && songH >= s.duration) {
                if (loop) { t0 = engineAt(s.duration); ni = 0; ci = 0; continue }
                // The last notes were planned up to LOOKAHEAD ago: stop only once the song has really finished.
                if (now() >= engineAt(s.duration) + 50) { stop(ended = true); onEnded?.invoke() }
            }
            break
        }
    }
}
