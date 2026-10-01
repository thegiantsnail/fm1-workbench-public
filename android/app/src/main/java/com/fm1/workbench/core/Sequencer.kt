package com.fm1.workbench.core

import kotlin.random.Random

/**
 * Multi-voice drum sequencer (port of seq.js scheduling). Each hit loads its voice via parameter diffs right after
 * the previous note-on (Engine.hit), so ringing tails keep their own sound.
 */
class Sequencer(private val engine: Engine, private val now: () -> Double, var pattern: Pattern,
                private val rnd: Random = Random.Default, private val fxChannel: () -> Int = { 1 }) : Engine.Producer {
    /** Chain: called at the end of each pattern (pattern.len steps); returns the next pattern to play, or null to keep going. */
    var chainNext: (() -> Pattern?)? = null
    @Volatile var onPatternChanged: ((Pattern) -> Unit)? = null
    private var base = 0
    data class Mark(val t: Double, val k: Int)
    data class Hit(val t: Double, val vced: IntArray?, val note: Int, val vel: Int, val gate: Double, val ti: Int, val cc: Int = -1, val ccVal: Int = 0)

    @Volatile var playing = false; private set
    private var k = 0
    private var next = 0.0
    private val pending = ArrayList<Hit>()
    private val cyc = HashMap<Int, Int>()
    val marks = ArrayDeque<Mark>()          // step times for the UI playhead
    val flashes = ArrayDeque<Pair<Double, Int>>()   // (time, track) for pad flashes

    private fun stepMs() = 60000.0 / pattern.bpm * (RATES[pattern.rate] ?: 0.25)

    fun start() = synchronized(engine) {
        if (playing) return
        playing = true; k = 0; base = 0; next = now() + 60; pending.clear(); cyc.clear(); marks.clear(); flashes.clear()
        engine.start(this)
    }

    fun stop() = synchronized(engine) {
        if (!playing) return
        playing = false; pending.clear()
        engine.stop(this)
    }

    private fun pickVoice(tr: Track, ti: Int): TrackVoice? {
        if (tr.voices.isEmpty()) return null
        if (pattern.glitch > 0 && rnd.nextDouble() * 100 < pattern.glitch) {
            val all = pattern.tracks.flatMap { it.voices }
            return all[rnd.nextInt(all.size)]
        }
        return when (tr.mode) {
            "random" -> tr.voices[rnd.nextInt(tr.voices.size)]
            "cycle" -> { val i = (cyc[ti] ?: -1) + 1; cyc[ti] = i; tr.voices[i % tr.voices.size] }
            else -> tr.voices[0]
        }
    }

    /** How long a hit sounds: slide = legato into the next note (tie across empty steps), else step gate %, else track gate. */
    private fun gateMs(tr: Track, kp: Int, st: Step, sd: Double, j: Int): Double {
        val sub = sd / st.rat
        if (st.slide && j == st.rat - 1) {
            var n = 1
            while (n < tr.len && !tr.steps[(kp + n) % tr.len].on) n++
            return sub + (n - 1) * sd + 15                 // overlap the next note by 15 ms
        }
        st.gate?.let { return maxOf(5.0, sub * it / 100) }
        return if (st.rat > 1) minOf(tr.gate.toDouble(), sub * 0.9) else tr.gate.toDouble()
    }

    private fun genStep(k: Int, tBase: Double) {
        if (k > base && k - base >= pattern.len) chainNext?.invoke()?.let { np ->
            pattern = np; base = k; cyc.clear(); onPatternChanged?.invoke(np)
        }
        val kp = k - base                                      // step within the current pattern
        val p = pattern
        val sd = stepMs()
        val t0 = tBase + if (kp % 2 == 1) p.swing / 100.0 * sd else 0.0
        synchronized(marks) { marks.addLast(Mark(t0, kp)) }
        if (p.fx.cc >= 0) p.fx.vals[kp % p.fx.len]?.let { pending += Hit(t0 - 1, null, 0, 0, 0.0, -1, p.fx.cc, it) }
        val soloing = p.tracks.any { it.solo }
        p.tracks.forEachIndexed { ti, tr ->
            if (tr.mute || (soloing && !tr.solo)) return@forEachIndexed
            val st = tr.steps[kp % tr.len]
            if (!st.on || rnd.nextDouble() * 100 >= st.prob) return@forEachIndexed
            val voice = pickVoice(tr, ti)
            var vced = voice?.effective()
            if (vced != null && tr.plock >= 0 && st.pl != null) {
                PLOCK_PARAMS.firstOrNull { it.second == tr.plock }?.let { (_, idx, max) ->
                    vced = vced!!.copyOf().also { it[idx] = Math.round(st.pl!! / 127.0 * max).toInt() }
                }
            }
            val t = t0 + st.nudge / 100.0 * sd + if (p.human > 0) (rnd.nextDouble() * 2 - 1) * p.human else 0.0
            for (j in 0 until st.rat) {
                val vel = if (st.acc) 127 else st.vel
                pending += Hit(t + j * sd / st.rat, vced, (tr.note + st.pitch + j * tr.ratPitch).coerceIn(0, 127),
                    Math.round(vel * (if (j > 0 && !st.acc) 0.8 else 1.0)).toInt(), gateMs(tr, kp, st, sd, j), ti)
            }
        }
    }

    override fun schedule(horizon: Double) {
        val sd = stepMs()
        while (next < horizon + 2 * sd + 40) { genStep(k, next); k++; next += sd }
        pending.sortBy { it.t }
        var n = 0
        while (n < pending.size && pending[n].t < horizon) n++
        val due = pending.subList(0, n).toList(); pending.subList(0, n).clear()
        for (h in due) {
            if (h.cc >= 0) engine.planSend(intArrayOf(0xB0 or fxChannel(), h.cc, h.ccVal), h.t)
            else engine.planHit(h.vced, h.note, h.vel, h.t, h.gate) { at -> synchronized(flashes) { flashes.addLast(at to h.ti) } }
        }
    }

    /** Hit one track now (pads). */
    fun hitPad(ti: Int, vel: Int = 110): Boolean {
        val tr = pattern.tracks.getOrNull(ti) ?: return false
        val v = pickVoice(tr, ti) ?: return false
        engine.hit(v.effective(), tr.note, vel, now() + 3, tr.gate.toDouble())
        return true
    }

    /** GM drum note -> this kit's track by role (used by the MIDI player for channel 10). */
    fun trackForGm(n: Int): Track? {
        val role = when (n) {
            35, 36 -> "kick"; 37, 38, 40 -> "snare"; 42, 44, 46, 49, 51, 52, 53, 55, 57, 59 -> "hat"
            41, 43, 45, 47, 48, 50 -> "tom"; else -> "perc"
        }
        val cands = pattern.tracks.filter { it.role == role && it.voices.isNotEmpty() }
        if (cands.isEmpty()) return null
        if (role == "hat" && n in listOf(46, 49, 55, 57)) return cands.firstOrNull { Regex("open|crash|cym", RegexOption.IGNORE_CASE).containsMatchIn(it.name) } ?: cands.last()
        return cands[0]
    }
}
