package com.fm1.workbench.core

/**
 * DX7 voice codec, ported from the web app's dx7.js (the hardware-verified reference).
 * A voice is a VCED parameter array: 155 values (OP6 first, 21 per operator; globals from 126; name 145..154).
 * Parity with dx7.js is enforced by unit tests generated from the JS code (tools/make_vectors.cjs).
 */
object Dx7 {
    val OP_FIELDS = listOf("R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4", "BP", "LD", "RD", "LC", "RC",
        "RS", "AMS", "KVS", "OL", "MODE", "FC", "FF", "DT")
    val OP_MAX = intArrayOf(99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 3, 3, 7, 3, 7, 99, 1, 31, 99, 14)
    val G_FIELDS = listOf("PR1", "PR2", "PR3", "PR4", "PL1", "PL2", "PL3", "PL4", "ALG", "FB", "OKS",
        "LFS", "LFD", "LPMD", "LAMD", "LFKS", "LFW", "LPMS", "TRNP")
    val G_MAX = intArrayOf(99, 99, 99, 99, 99, 99, 99, 99, 31, 7, 1, 99, 99, 99, 99, 1, 5, 7, 48)

    const val SIZE = 155
    const val ALG = 134
    const val FB = 135
    const val OPMASK = 155

    /** VCED index of an operator field (op = 1..6) or of a global field (op = null). */
    fun paramIndex(op: Int?, field: String): Int =
        if (op == null) 126 + G_FIELDS.indexOf(field).also { require(it >= 0) { "unknown field $field" } }
        else {
            require(op in 1..6) { "operator must be 1..6" }
            (6 - op) * 21 + OP_FIELDS.indexOf(field).also { require(it >= 0) { "unknown field $field" } }
        }

    /** Maximum value of VCED parameter [i] (names: 127, op mask: 63). */
    fun maxOf(i: Int): Int = when {
        i < 126 -> OP_MAX[i % 21]
        i < 145 -> G_MAX[i - 126]
        i < 155 -> 127
        else -> 63
    }

    fun paramName(i: Int): String = when {
        i < 126 -> "OP${6 - i / 21}.${OP_FIELDS[i % 21]}"
        i < 145 -> G_FIELDS[i - 126]
        i == 155 -> "OPMASK"
        else -> "NAME${i - 145}"
    }

    fun cleanName(s: String): String =
        s.map { if (it.code in 0x20..0x7e) it else ' ' }.joinToString("").take(10).padEnd(10, ' ')

    fun name(v: IntArray): String = String(CharArray(10) { v[145 + it].toChar() })

    fun setName(v: IntArray, n: String) {
        cleanName(n).forEachIndexed { i, c -> v[145 + i] = c.code }
    }

    /** DX7 "INIT VOICE", as dx7.js initVoice(): OP1 at full level, everything else neutral. */
    fun initVoice(name: String = "INIT VOICE"): IntArray {
        val v = IntArray(SIZE)
        val op = intArrayOf(99, 99, 99, 99, 99, 99, 99, 0, 39, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 7)
        for (n in 0 until 6) op.copyInto(v, n * 21)
        v[paramIndex(1, "OL")] = 99
        intArrayOf(99, 99, 99, 99, 50, 50, 50, 50, 0, 0, 1, 35, 0, 0, 0, 1, 0, 3, 24).copyInto(v, 126)
        setName(v, name)
        return v
    }

    /** Clamp every parameter into its DX7 range and clean the name (dx7.js clampVoice). */
    fun clamp(v: IntArray): IntArray {
        val out = IntArray(SIZE)
        for (i in 0 until 145) out[i] = v[i].coerceIn(0, maxOf(i))
        setName(out, String(CharArray(10) { (v[145 + it] and 0xFFFF).toChar() }))
        return out
    }

    fun toVmem(v: IntArray): IntArray {
        val b = IntArray(128)
        for (n in 6 downTo 1) {
            val o = (6 - n) * 21
            val p = (6 - n) * 17
            for (i in 0 until 11) b[p + i] = v[o + i]
            b[p + 11] = (v[o + 12] shl 2) or v[o + 11]           // RC, LC
            b[p + 12] = (v[o + 20] shl 3) or v[o + 13]           // DT, RS
            b[p + 13] = (v[o + 15] shl 2) or v[o + 14]           // KVS, AMS
            b[p + 14] = v[o + 16]                                 // OL
            b[p + 15] = (v[o + 18] shl 1) or v[o + 17]           // FC, MODE
            b[p + 16] = v[o + 19]                                 // FF
        }
        for (i in 0 until 9) b[102 + i] = v[126 + i]              // PR1..PL4, ALG
        b[111] = (v[136] shl 3) or v[135]                         // OKS, FB
        b[112] = v[137]; b[113] = v[138]; b[114] = v[139]; b[115] = v[140]
        b[116] = (v[143] shl 4) or (v[142] shl 1) or v[141]       // LPMS, LFW, LFKS
        b[117] = v[144]
        for (i in 0 until 10) b[118 + i] = v[145 + i]
        return b
    }

    fun fromVmem(b: IntArray): IntArray {
        val v = IntArray(SIZE)
        for (n in 6 downTo 1) {
            val o = (6 - n) * 21
            val p = (6 - n) * 17
            for (i in 0 until 11) v[o + i] = b[p + i] and 127
            v[o + 11] = b[p + 11] and 3; v[o + 12] = (b[p + 11] shr 2) and 3
            v[o + 13] = b[p + 12] and 7; v[o + 20] = (b[p + 12] shr 3) and 15
            v[o + 14] = b[p + 13] and 3; v[o + 15] = (b[p + 13] shr 2) and 7
            v[o + 16] = b[p + 14] and 127
            v[o + 17] = b[p + 15] and 1; v[o + 18] = (b[p + 15] shr 1) and 31
            v[o + 19] = b[p + 16] and 127
        }
        for (i in 0 until 8) v[126 + i] = b[102 + i] and 127
        v[134] = b[110] and 31; v[135] = b[111] and 7; v[136] = (b[111] shr 3) and 1
        v[137] = b[112] and 127; v[138] = b[113] and 127; v[139] = b[114] and 127; v[140] = b[115] and 127
        v[141] = b[116] and 1; v[142] = (b[116] shr 1) and 7; v[143] = (b[116] shr 4) and 7
        v[144] = b[117] and 127
        for (i in 0 until 10) v[145 + i] = b[118 + i] and 127
        return clamp(v)
    }

    fun checksum(data: IntArray): Int = (128 - (data.sum() and 127)) and 127

    fun vcedSysex(v: IntArray, ch: Int = 0): IntArray {
        val d = v.copyOf(SIZE)
        return intArrayOf(0xF0, 0x43, ch, 0x00, 0x01, 0x1B) + d + intArrayOf(checksum(d), 0xF7)
    }

    fun vmemSysex(voices: List<IntArray>, ch: Int = 0): IntArray {
        val d = IntArray(4096)
        for (i in 0 until 32) toVmem(voices.getOrNull(i) ?: initVoice()).copyInto(d, i * 128)
        return intArrayOf(0xF0, 0x43, ch, 0x09, 0x20, 0x00) + d + intArrayOf(checksum(d), 0xF7)
    }

    fun paramSysex(p: Int, value: Int, ch: Int = 0): IntArray =
        intArrayOf(0xF0, 0x43, 0x10 or ch, (p shr 7) and 3, p and 127, value and 127, 0xF7)

    /** Parameter 155 (operator on/off): bit 5 = OP1 ... bit 0 = OP6. */
    fun opMaskValue(on: BooleanArray): Int = on.foldIndexed(0) { i, m, x -> if (x) m or (1 shl (5 - i)) else m }

    data class Parsed(val vced: IntArray, val kind: String, val slot: Int?)

    /** Find DX7 voices in a .syx buffer: 32-voice banks, single voices, or a raw 4096-byte bank. */
    fun parseSyx(bytes: ByteArray): List<Parsed> {
        val u = IntArray(bytes.size) { bytes[it].toInt() and 0xFF }
        val found = mutableListOf<Parsed>()
        var i = 0
        while (i < u.size) {
            if (u[i] == 0xF0 && i + 3 < u.size && u[i + 1] == 0x43) {
                val fmt = u[i + 3]
                if (fmt == 0x09 && u.size >= i + 6 + 4096) {
                    for (k in 0 until 32) found += Parsed(fromVmem(u.copyOfRange(i + 6 + k * 128, i + 6 + (k + 1) * 128)), "bank", k)
                    i += 6 + 4096; continue
                }
                if (fmt == 0x00 && u.size >= i + 6 + 155) {
                    found += Parsed(clamp(u.copyOfRange(i + 6, i + 6 + 155)), "single", null)
                    i += 6 + 155; continue
                }
            }
            i++
        }
        if (found.isEmpty() && u.size == 4096) {
            for (k in 0 until 32) found += Parsed(fromVmem(u.copyOfRange(k * 128, (k + 1) * 128)), "bank", k)
        }
        return found
    }

    // ---- algorithms: modulation edges "from>to" and feedback operator, DX7 algorithms 1..32 ----
    data class Algorithm(val edges: List<Pair<Int, Int>>, val fb: Int, val carriers: List<Int>)

    private val ALG_SRC = listOf(
        "2>1,6>5,5>4,4>3|6", "2>1,6>5,5>4,4>3|2", "3>2,2>1,6>5,5>4|6", "3>2,2>1,6>5,5>4|6",
        "2>1,4>3,6>5|6", "2>1,4>3,6>5|6", "2>1,4>3,5>3,6>5|6", "2>1,4>3,5>3,6>5|4",
        "2>1,4>3,5>3,6>5|2", "3>2,2>1,5>4,6>4|3", "3>2,2>1,5>4,6>4|6", "2>1,4>3,5>3,6>3|2",
        "2>1,4>3,5>3,6>3|6", "2>1,4>3,5>4,6>4|6", "2>1,4>3,5>4,6>4|2", "2>1,3>1,4>3,5>1,6>5|6",
        "2>1,3>1,4>3,5>1,6>5|2", "2>1,3>1,4>1,5>4,6>5|3", "3>2,2>1,6>4,6>5|6", "3>1,3>2,5>4,6>4|3",
        "3>1,3>2,6>4,6>5|3", "2>1,6>3,6>4,6>5|6", "3>2,6>4,6>5|6", "6>3,6>4,6>5|6",
        "6>4,6>5|6", "3>2,5>4,6>4|6", "3>2,5>4,6>4|3", "2>1,5>4,4>3|5",
        "4>3,6>5|6", "5>4,4>3|5", "6>5|6", "|6")

    val ALGS: List<Algorithm> = ALG_SRC.map { s ->
        val (e, fb) = s.split("|")
        val edges = if (e.isEmpty()) emptyList() else e.split(",").map { p -> p.split(">").let { it[0].toInt() to it[1].toInt() } }
        Algorithm(edges, fb.toInt(), (1..6).filter { o -> edges.none { it.first == o } })
    }

    fun freqLabel(v: IntArray, op: Int): String {
        val o = (6 - op) * 21
        val fc = v[o + 18]; val ff = v[o + 19]
        return if (v[o + 17] == 1) {
            val f = Math.pow(10.0, (fc and 3).toDouble()) * Math.pow(10.0, ff / 100.0)
            "%.${if (f < 10) 3 else if (f < 100) 2 else 1}f Hz".format(f)
        } else "×%.2f".format((if (fc == 0) 0.5 else fc.toDouble()) * (1 + ff / 100.0))
    }
}
