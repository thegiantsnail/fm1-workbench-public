package com.fm1.workbench.core

/** Drum pattern model (same shape and limits as the web app's seq.js, so patterns/kits can be shared). */
const val MAX_STEPS = 64
/** Step length per rate, in quarter notes (same keys as the web app). */
val RATES = linkedMapOf("1/4" to 1.0, "1/8" to 0.5, "1/16" to 0.25, "1/32" to 0.125, "1/8T" to 1 / 3.0, "1/16T" to 1 / 6.0, "1/32T" to 1 / 12.0)
val ROLES = listOf("kick", "snare", "hat", "tom", "perc")

/** gate: null = the track's gate (ms), else % of the step; acc: full velocity; slide: legato into the next note (tie over empty steps). */
data class Step(var on: Boolean = false, var vel: Int = 100, var prob: Int = 100, var rat: Int = 1,
                var nudge: Int = 0, var pitch: Int = 0, var pl: Int? = null,
                var gate: Int? = null, var acc: Boolean = false, var slide: Boolean = false)

class TrackVoice(var name: String, var vced: IntArray, var macros: MutableMap<String, Int?> = mutableMapOf(),
                 var measured: Map<String, Double>? = null) {
    @Volatile private var cacheKey: String? = null
    @Volatile private var cacheBase: IntArray? = null
    @Volatile private var cache: IntArray? = null

    /** Parameters with the drum macros applied (cached until macros or base change). */
    fun effective(): IntArray {
        val key = macros.toSortedMap().toString()
        if (cache == null || key != cacheKey || cacheBase !== vced) {
            cache = DrumMacros.apply(vced, macros); cacheKey = key; cacheBase = vced
        }
        return cache!!
    }
}

class Track(var name: String, var role: String, var note: Int, var len: Int = 16, var gate: Int = 120,
            var mode: String = "fixed", var mute: Boolean = false, var solo: Boolean = false, var ratPitch: Int = 0,
            var plock: Int = -1, val voices: MutableList<TrackVoice> = mutableListOf(),
            val steps: Array<Step> = Array(MAX_STEPS) { Step() })

class FxLane(var cc: Int = -1, var len: Int = 16, val vals: Array<Int?> = arrayOfNulls(MAX_STEPS))

class Pattern(var bpm: Int = 112, var swing: Int = 0, var human: Int = 0, var glitch: Int = 0,
              val fx: FxLane = FxLane(), val tracks: MutableList<Track> = mutableListOf(),
              var rate: String = "1/16", var len: Int = 16)

/** Per-step parameter locks: label, VCED index, max value (step values 0..127 are scaled to max). */
val PLOCK_PARAMS: List<Triple<String, Int, Int>> = buildList {
    add(Triple("none", -1, 0)); add(Triple("Algorithm", 134, 31)); add(Triple("Feedback", 135, 7)); add(Triple("Transpose", 144, 48))
    for (n in 1..6) {
        add(Triple("OP$n level", Dx7.paramIndex(n, "OL"), 99)); add(Triple("OP$n coarse", Dx7.paramIndex(n, "FC"), 31))
        add(Triple("OP$n decay R2", Dx7.paramIndex(n, "R2"), 99))
    }
    add(Triple("PEG L1 (pitch hit)", 130, 99)); add(Triple("LFO pitch depth", 139, 99))
}

object StarterKit {
    /** Built-in drum voices (port of seq.js drumVoice) so the sequencer works before any library is loaded. */
    fun drumVoice(role: String): TrackVoice {
        val v = Dx7.initVoice(role.uppercase().take(10))
        fun op(n: Int, f: String, x: Int) { v[Dx7.paramIndex(n, f)] = x }
        fun g(f: String, x: Int) { v[Dx7.paramIndex(null, f)] = x }
        for (n in 1..6) { op(n, "OL", 0); op(n, "R1", 99); op(n, "R2", 60); op(n, "R3", 99); op(n, "R4", 70); op(n, "L1", 99); op(n, "L2", 0); op(n, "L3", 0); op(n, "L4", 0) }
        when (role) {
            "kick" -> { g("ALG", 0); op(1, "OL", 99); op(1, "FC", 0); op(1, "R2", 45); op(2, "OL", 72); op(2, "FC", 1); op(2, "R2", 92)
                g("PR1", 70); g("PL4", 78); g("PL1", 50); g("PL2", 50); g("PL3", 50); g("PR4", 99) }
            "snare" -> { g("ALG", 31); g("FB", 7); op(1, "OL", 88); op(1, "FC", 1); op(1, "R2", 72); op(6, "OL", 95); op(6, "FC", 7); op(6, "R2", 66)
                g("PR1", 90); g("PL4", 60); g("PL1", 50); g("PL2", 50); g("PL3", 50) }
            "hat" -> { g("ALG", 31); g("FB", 7); op(6, "OL", 97); op(6, "FC", 15); op(6, "R2", 82); op(5, "OL", 80); op(5, "FC", 11); op(5, "FF", 37); op(5, "R2", 85) }
            "tom" -> { g("ALG", 0); op(1, "OL", 99); op(1, "FC", 1); op(1, "R2", 52); op(2, "OL", 55); op(2, "FC", 1); op(2, "R2", 85)
                g("PR1", 60); g("PL4", 62); g("PL1", 50); g("PL2", 50); g("PL3", 50) }
            else -> { g("ALG", 4); op(1, "OL", 95); op(1, "FC", 1); op(1, "R2", 62); op(2, "OL", 70); op(2, "FC", 2); op(2, "FF", 48); op(2, "R2", 70)
                op(3, "OL", 90); op(3, "FC", 1); op(3, "FF", 48); op(3, "R2", 62) }
        }
        return TrackVoice(Dx7.name(v).trim(), Dx7.clamp(v))
    }

    fun defaultPattern(): Pattern {
        fun mk(name: String, role: String, note: Int, hits: List<Int>, len: Int = 16, gate: Int = 120) =
            Track(name, role, note, len, gate, voices = mutableListOf(drumVoice(role))).also { t -> hits.forEach { t.steps[it].on = true } }
        val p = Pattern(tracks = mutableListOf(
            mk("Kick", "kick", 48, listOf(0, 7, 10)),
            mk("Snare", "snare", 60, listOf(4, 12)),
            mk("Hat", "hat", 72, listOf(0, 2, 4, 6, 8, 10, 12, 14), gate = 60),
            mk("Open hat", "hat", 72, listOf(15), gate = 250),
            mk("Tom", "tom", 55, listOf(14), len = 12),
            mk("Perc", "perc", 67, listOf(3, 11), len = 7)))
        p.tracks[2].steps.forEachIndexed { i, s -> if (s.on && i % 4 == 2) s.vel = 70 }
        val oh = p.tracks[3].voices[0].vced.copyOf()
        oh[Dx7.paramIndex(6, "R2")] = 55; oh[Dx7.paramIndex(5, "R2")] = 58
        p.tracks[3].voices[0] = TrackVoice("OPEN HAT", oh)
        return p
    }
}
