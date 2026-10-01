package com.fm1.workbench.core

import kotlin.random.Random

/** Voice generators for the editor (ports of randomVoice / mutateVoice / morphVoice in app.js), on VCED arrays. */
object VoiceGen {
    val STYLES = listOf("any" to "Anything", "keys" to "Keys / EP", "bass" to "Bass", "pad" to "Pad", "bell" to "Bell / metallic", "pluck" to "Pluck")

    private fun op(v: IntArray, n: Int, f: String) = Dx7.paramIndex(n, f)

    fun random(style: String, rnd: Random = Random.Default): IntArray {
        fun r(a: Int, b: Int) = a + rnd.nextInt(b - a + 1)
        fun <T> pick(l: List<T>) = l[rnd.nextInt(l.size)]
        val v = Dx7.initVoice()
        val algPool = when (style) {
            "keys" -> listOf(4, 5, 0, 1, 20, 21); "bass" -> listOf(0, 1, 15, 16, 17); "pad" -> listOf(4, 5, 21, 22, 30)
            "bell" -> listOf(4, 5, 28, 29, 30); "pluck" -> listOf(0, 1, 2, 4, 15); else -> (0..31).toList()
        }
        v[Dx7.ALG] = pick(algPool)
        v[Dx7.FB] = if (style == "bass") r(3, 7) else r(0, 6)
        val car = Dx7.ALGS[v[Dx7.ALG]].carriers
        val ratios = if (style == "bell") listOf(1, 2, 3, 3, 4, 5, 7, 9, 11, 14) else listOf(1, 1, 1, 2, 2, 3, 4, 0, 5, 6, 8)
        for (n in 1..6) {
            val isCar = n in car
            v[op(v, n, "FC")] = if (isCar) pick(listOf(1, 1, 1, 2, if (style == "bass") 0 else 1)) else pick(ratios)
            v[op(v, n, "FF")] = if (style == "bell" && rnd.nextDouble() < 0.4) r(0, 60) else if (rnd.nextDouble() < 0.15) r(0, 20) else 0
            v[op(v, n, "DT")] = r(4, 10)
            v[op(v, n, "OL")] = if (isCar) r(88, 99) else r(if (style == "pad") 40 else 55, 92)
            v[op(v, n, "KVS")] = r(1, 5); v[op(v, n, "RS")] = r(0, 4)
            val sustain = when (style) { "keys" -> r(0, 70); "bass" -> r(0, 80); "pad" -> r(80, 99); "bell", "pluck" -> 0; else -> r(0, 99) }
            v[op(v, n, "R1")] = if (style == "pad") r(30, 70) else r(80, 99)
            v[op(v, n, "R2")] = if (style == "pluck") r(55, 80) else r(25, 80)
            v[op(v, n, "R3")] = r(20, 70)
            v[op(v, n, "R4")] = when (style) { "pad" -> r(30, 60); "bell" -> r(20, 45); else -> r(45, 80) }
            v[op(v, n, "L1")] = 99
            v[op(v, n, "L2")] = if (isCar) r(maxOf(sustain, 60), 99) else r(40, 99)
            v[op(v, n, "L3")] = if (isCar) sustain else r(0, 90)
            v[op(v, n, "L4")] = 0
        }
        v[137] = r(20, 45); v[139] = if (style == "pad") r(0, 8) else 0; v[143] = r(1, 3)       // LFS, LPMD, LPMS
        Dx7.setName(v, (mapOf("any" to "RANDOM", "keys" to "RND KEYS", "bass" to "RND BASS", "pad" to "RND PAD", "bell" to "RND BELL", "pluck" to "RND PLUCK")[style] ?: "RANDOM") + " " + r(10, 99))
        return Dx7.clamp(v)
    }

    fun mutate(src: IntArray, amount: Int, rnd: Random = Random.Default): IntArray {
        val v = src.copyOf(Dx7.SIZE); val k = amount / 100.0
        fun nudge(i: Int) { val max = Dx7.maxOf(i); v[i] = Math.round(v[i] + (rnd.nextDouble() * 2 - 1) * max * k).toInt().coerceIn(0, max) }
        for (n in 1..6) {
            for (f in listOf("OL", "R1", "R2", "R3", "R4", "L2", "L3", "FF", "KVS", "DT")) if (rnd.nextDouble() < 0.5) nudge(Dx7.paramIndex(n, f))
            if (rnd.nextDouble() < k * 0.5) { val i = Dx7.paramIndex(n, "FC"); v[i] = (v[i] + if (rnd.nextBoolean()) 1 else -1).coerceIn(0, 31) }
        }
        if (rnd.nextDouble() < 0.5) nudge(Dx7.FB)
        if (rnd.nextDouble() < k * 0.3) v[Dx7.ALG] = rnd.nextInt(32)
        return Dx7.clamp(v)
    }

    /** Interpolate every continuous parameter; switch-like ones (algorithm, sync, waves, mode, coarse, curves) snap at 50%. */
    fun morph(a: IntArray, b: IntArray, t: Double): IntArray {
        val snapOp = setOf("MODE", "FC", "LC", "RC"); val snapG = setOf(134, 136, 141, 142)
        val v = IntArray(Dx7.SIZE) { i ->
            val snap = if (i < 126) Dx7.OP_FIELDS[i % 21] in snapOp else i in snapG
            if (i >= 145) a[i] else if (snap) (if (t < 0.5) a[i] else b[i]) else Math.round(a[i] + (b[i] - a[i]) * t).toInt()
        }
        Dx7.setName(v, "MORPH " + Math.round(t * 100))
        return Dx7.clamp(v)
    }
}
