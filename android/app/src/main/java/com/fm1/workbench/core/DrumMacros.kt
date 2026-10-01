package com.fm1.workbench.core

import kotlin.math.roundToInt

/**
 * Drum macros (port of drums.js): musical controls applied as offsets to a voice's own parameters.
 * Verified on the FM-1: Level -20 steps = -15.2 dB; Decay/Release act on carriers only (on all operators,
 * "longer" made hits sound shorter); Tone = modulator levels; Punch/Sweep = pitch EG L4/R1; Grit = feedback.
 */
object DrumMacros {
    data class Def(val id: String, val label: String, val min: Int, val max: Int, val keep: Int? = null, val help: String)

    val DEFS = listOf(
        Def("level", "Level", -40, 20, help = "Carrier output levels (~0.75 dB per step)"),
        Def("decay", "Decay", -40, 40, help = "How long the hit rings (carrier R2/R3)"),
        Def("release", "Release", -40, 40, help = "Ring after note-off (carrier R4)"),
        Def("tone", "Tone", -40, 40, help = "Modulator levels: brighter / darker"),
        Def("punch", "Punch", -40, 40, help = "Pitch-envelope start: drop (+) or rise (-)"),
        Def("sweep", "Sweep time", -1, 99, keep = -1, help = "Pitch sweep length (own = keep the voice's)"),
        Def("grit", "Grit", -7, 7, help = "Operator feedback"),
        Def("dyn", "Dynamics", -1, 7, keep = -1, help = "Carrier velocity sensitivity (own = keep)"),
    )

    fun format(id: String, v: Int): String = when (id) {
        "level" -> (if (v > 0) "+" else "") + "%.1f dB".format(v * 0.75)
        "decay", "release" -> if (v == 0) "0" else (if (v > 0) "longer " else "shorter ") + kotlin.math.abs(v)
        "tone" -> if (v == 0) "0" else (if (v > 0) "brighter " else "darker ") + kotlin.math.abs(v)
        "punch" -> if (v == 0) "0" else (if (v > 0) "drop " else "rise ") + kotlin.math.abs(v)
        "sweep", "dyn" -> if (v < 0) "own" else v.toString()
        "grit" -> (if (v > 0) "+" else "") + v
        else -> v.toString()
    }

    private fun oi(op: Int, f: String) = (6 - op) * 21 + Dx7.OP_FIELDS.indexOf(f)
    private fun c(v: Double, a: Int, b: Int) = v.roundToInt().coerceIn(a, b)

    fun isEdited(m: Map<String, Int?>?): Boolean = m != null && m.values.any { it != null && it != 0 }

    /** Returns [base] itself when no macro is active (like the JS), else a new 155-value array. */
    fun apply(base: IntArray, m: Map<String, Int?>?): IntArray {
        if (!isEdited(m)) return base
        m!!
        val v = base.copyOf(Dx7.SIZE)
        val car = Dx7.ALGS[v[Dx7.ALG]].carriers
        val level = m["level"] ?: 0; val tone = m["tone"] ?: 0; val decay = m["decay"] ?: 0
        val release = m["release"] ?: 0; val punch = m["punch"] ?: 0; val grit = m["grit"] ?: 0
        val dyn = m["dyn"]; val sweep = m["sweep"]
        for (op in 1..6) {
            val isCar = op in car
            val ol = oi(op, "OL")
            if (v[ol] > 0) {                                   // leave silent operators silent
                if (level != 0 && isCar) v[ol] = c((v[ol] + level).toDouble(), 1, 99)
                if (tone != 0 && !isCar) v[ol] = c((v[ol] + tone).toDouble(), 0, 99)
            }
            if (decay != 0 && isCar) for (f in listOf("R2", "R3")) v[oi(op, f)] = c((v[oi(op, f)] - decay).toDouble(), 0, 99)
            if (release != 0 && isCar) v[oi(op, "R4")] = c((v[oi(op, "R4")] - release).toDouble(), 0, 99)
            if (dyn != null && isCar) v[oi(op, "KVS")] = dyn.coerceIn(0, 7)
        }
        if (punch != 0) v[133] = c((v[133] + punch).toDouble(), 0, 99)        // PL4
        if (sweep != null) v[126] = (99 - sweep).coerceIn(0, 99)             // PR1
        if (grit != 0) v[Dx7.FB] = c((v[Dx7.FB] + grit).toDouble(), 0, 7)
        return v
    }
}
