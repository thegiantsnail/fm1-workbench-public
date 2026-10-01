package com.fm1.workbench.core

import kotlin.math.*

/**
 * Software FM-1 (port of app/fm1-synth.js): a DX7-compatible 6-operator engine that understands the MIDI the app sends
 * the hardware (notes, DX7 parameter changes, single-voice and bank dumps, program change, sustain, the effect CCs), so
 * every screen can be played without the unit. Like the FM-1, a parameter change only affects the NEXT note.
 *
 * Fitted to measurements of the real unit (FINDINGS.md, test_soft_probe.py): 0.75 dB per output-level step, modulation
 * index 4π at full level, feedback 1/4 of the DX7-emulation strength, velocity never boosting above the stored level,
 * a free-running LFO (stock firmware: fixed 5.8 Hz), a 5.6 kHz one-pole output roll-off. [profile] "va" = Baud Girl's
 * FM-1+VA firmware: narrower detune, LFO speed from the patch / CC 76, CC 7 master volume.
 * Pure Kotlin (no Android types) so JVM tests can compare it sample by sample with the JS engine.
 */
class Fm1Synth(val sr: Int) {
    companion object T {
        const val SIN_N = 4096
        val SIN = FloatArray(SIN_N + 1) { sin(2 * PI * it / SIN_N).toFloat() }
        val EXP = FloatArray(4097) { 2.0.pow((it - 3840) / 256.0).toFloat() }      // level units -> amplitude
        private val LOW = intArrayOf(0, 5, 9, 13, 17, 20, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 42, 43, 45, 46)
        fun scaleOut(x: Int) = if (x >= 20) 28 + x else LOW[x]
        val VEL = intArrayOf(0, 70, 86, 97, 106, 114, 121, 126, 132, 138, 142, 148, 152, 156, 160, 163, 166, 170, 173, 174, 178, 181,
            184, 186, 189, 190, 194, 196, 198, 200, 202, 205, 206, 209, 211, 214, 216, 218, 220, 222, 224, 225, 227, 229, 230,
            232, 233, 235, 237, 238, 240, 241, 242, 243, 244, 246, 246, 248, 249, 250, 251, 252, 253, 254)
        val EXPSCALE = intArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 16, 19, 23, 27, 33, 39, 47, 56, 66, 80, 94, 110, 126, 142, 158,
            174, 190, 206, 222, 238, 250)
        val PLV = intArrayOf(-128, -116, -104, -95, -85, -76, -68, -61, -56, -52, -49, -46, -43, -41, -39, -37, -35, -33, -32, -31,
            -30, -29, -28, -27, -26, -25, -24, -23, -22, -21, -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8, -7,
            -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
            25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 38, 40, 43, 46, 49, 53, 58, 65, 73, 82, 92, 103, 115, 127)
        val PRATE = intArrayOf(1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
            24, 25, 26, 27, 28, 30, 31, 33, 34, 36, 37, 38, 39, 41, 42, 44, 46, 47, 49, 51, 53, 54, 56, 58, 60, 62, 64, 66, 68,
            70, 72, 74, 76, 79, 82, 85, 88, 91, 94, 98, 102, 106, 110, 115, 120, 125, 130, 135, 141, 147, 153, 159, 165, 171,
            178, 185, 193, 202, 211, 232, 243, 254, 255, 255, 255, 255, 255, 255, 255, 255)
        /** Vibrato depth (semitones at LPMD 99) by LPMS: DX7 table x0.88 (measured). */
        val PMS = doubleArrayOf(0.0, 10.0, 20.0, 33.0, 55.0, 92.0, 153.0, 255.0).map { it / 255 * 12 * 0.88 }.toDoubleArray()
        /** Tremolo depth (level units p-p at LAMD 99) by AMS: 2.1 / 5.3 dB, AMS 3 silences (measured). */
        val AMS = doubleArrayOf(0.0, 2.1, 5.3, 130.0).map { it * 256 / 6.02 }.toDoubleArray()
        const val LFO_HZ = 5.8
        const val FB_SCALE = 0.25
        const val OUT_LP_HZ = 5600.0

        class Alg(val mods: Array<IntArray>, val fb: Int, val carriers: IntArray)
        val ALG: List<Alg> = listOf("2>1,6>5,5>4,4>3|6", "2>1,6>5,5>4,4>3|2", "3>2,2>1,6>5,5>4|6", "3>2,2>1,6>5,5>4|6", "2>1,4>3,6>5|6",
            "2>1,4>3,6>5|6", "2>1,4>3,5>3,6>5|6", "2>1,4>3,5>3,6>5|4", "2>1,4>3,5>3,6>5|2", "3>2,2>1,5>4,6>4|3",
            "3>2,2>1,5>4,6>4|6", "2>1,4>3,5>3,6>3|2", "2>1,4>3,5>3,6>3|6", "2>1,4>3,5>4,6>4|6", "2>1,4>3,5>4,6>4|2",
            "2>1,3>1,4>3,5>1,6>5|6", "2>1,3>1,4>3,5>1,6>5|2", "2>1,3>1,4>1,5>4,6>5|3", "3>2,2>1,6>4,6>5|6", "3>1,3>2,5>4,6>4|3",
            "3>1,3>2,6>4,6>5|3", "2>1,6>3,6>4,6>5|6", "3>2,6>4,6>5|6", "6>3,6>4,6>5|6", "6>4,6>5|6", "3>2,5>4,6>4|6",
            "3>2,5>4,6>4|3", "2>1,5>4,4>3|5", "4>3,6>5|6", "5>4,4>3|5", "6>5|6", "|6").map { s ->
            val (e, fb) = s.split('|')
            val edges = if (e.isEmpty()) emptyList() else e.split(',').map { p -> p.split('>').map(String::toInt) }
            val mods = Array(7) { t -> edges.filter { it[1] == t }.map { it[0] }.toIntArray() }
            Alg(mods, fb.toInt(), (1..6).filter { o -> edges.none { it[0] == o } }.toIntArray())
        }
        val INIT: IntArray = IntArray(156).also { d ->
            for (n in 6 downTo 1) intArrayOf(99, 99, 99, 99, 99, 99, 99, 0, 39, 0, 0, 0, 0, 0, 0, 0, if (n == 1) 99 else 0, 0, 1, 0, 7).copyInto(d, (6 - n) * 21)
            intArrayOf(99, 99, 99, 99, 50, 50, 50, 50, 0, 0, 1, 35, 0, 0, 0, 1, 0, 3, 24).copyInto(d, 126)
            "INIT VOICE".forEachIndexed { i, c -> d[145 + i] = c.code }
            d[155] = 63
        }
        fun vmemToVced(b: IntArray, off: Int): IntArray {
            val d = IntArray(156)
            for (n in 6 downTo 1) {
                val s = off + (6 - n) * 17; val o = (6 - n) * 21
                for (i in 0 until 11) d[o + i] = b[s + i] and 127
                d[o + 11] = b[s + 11] and 3; d[o + 12] = (b[s + 11] shr 2) and 3
                d[o + 13] = b[s + 12] and 7; d[o + 20] = (b[s + 12] shr 3) and 15
                d[o + 14] = b[s + 13] and 3; d[o + 15] = (b[s + 13] shr 2) and 7
                d[o + 16] = b[s + 14] and 127; d[o + 17] = b[s + 15] and 1; d[o + 18] = (b[s + 15] shr 1) and 31; d[o + 19] = b[s + 16] and 127
            }
            for (i in 0 until 8) d[126 + i] = b[off + 102 + i] and 127
            d[134] = b[off + 110] and 31; d[135] = b[off + 111] and 7; d[136] = (b[off + 111] shr 3) and 1
            d[137] = b[off + 112] and 127; d[138] = b[off + 113] and 127; d[139] = b[off + 114] and 127; d[140] = b[off + 115] and 127
            d[141] = b[off + 116] and 1; d[142] = (b[off + 116] shr 1) and 7; d[143] = (b[off + 116] shr 4) and 7; d[144] = b[off + 117] and 127
            for (i in 0 until 10) d[145 + i] = b[off + 118 + i] and 127
            d[155] = 63
            return d
        }
    }

    private class Op(val on: Boolean, val R: IntArray, val L: IntArray, val olUnits: Int, val rs: Int, val ams: Double,
                     val fixed: Double, val ratio: Double) {
        var phase = 0.0; var dph = 0.0; var level = 0.0; var target = 0.0; var inc = 0.0; var rising = false; var ix = 0; var amod = 0.0
    }
    private class Peg(var level: Double, val R: IntArray, val L: IntArray) { var ix = 0; var target = 0.0; var inc = 0.0; var rising = false }
    private class Lfo(var f: Double, var ph: Double, val wave: Int, val delay: Double, val ramp: Double, var pmd: Double, val amd: Double) {
        var sh = 0.0; var t = 0.0
    }
    private class Voice(val key: Int, val alg: Alg, val baseHz: Double, val fbAmt: Double) {
        var down = true; var held = false
        val ops = arrayOfNulls<Op>(7)
        val y = DoubleArray(7)
        var fb1 = 0.0; var fb2 = 0.0; var ctr = 0
        lateinit var peg: Peg; var pegActive = false
        lateinit var lfo: Lfo; var lfoActive = false
    }

    var buf: IntArray = INIT.copyOf(); private set          // edit buffer: VCED 0..154 + operator mask 155
    private var bank: List<IntArray>? = null
    private val voices = ArrayList<Voice>()
    private class Msg(val f: Long, val b: IntArray, val s: Long)
    private val q = ArrayList<Msg>()
    private var qs = 0L
    private var sustain = false
    var maxVoices = 16
    var volume = 0.8
    private val rateK = 44100.0 / sr
    val fx = Fm1Fx(sr)
    var fxCh = 1
    /** "stock" (M-VAVE V15) or "va" (Baud Girl's FM-1+VA). */
    var profile = "stock"
        set(v) { field = v; if (v != "va") master = 1.0 }
    private var master = 1.0
    private var now = 0L
    private var lpZ = 0.0
    val activeVoices get() = voices.size

    /** Schedule a MIDI message at an absolute sample frame (thread-safe). */
    @Synchronized fun queue(bytes: IntArray, frame: Long) {
        val item = Msg(frame, bytes, qs++)
        var lo = 0; var hi = q.size
        while (lo < hi) { val m = (lo + hi) ushr 1; if (q[m].f <= frame) lo = m + 1 else hi = m }
        q.add(lo, item)
    }
    @Synchronized fun reset() { voices.clear(); q.clear() }

    private fun midi(b: IntArray) {
        val st = b[0]; val type = st and 0xF0
        if (st == 0xF0) return sysex(b)
        if (type == 0x90 && b.size > 2 && b[2] > 0) return noteOn(b[1], b[2])
        if (type == 0x80 || type == 0x90) return noteOff(b[1])
        if (type == 0xB0 && (st and 15) == fxCh && b[1] < 24) return fx.set(b[1], b[2])
        if (type == 0xB0 && profile == "va") {                      // FM-1+VA controllers on the MIDI channel
            val v = b[2]; val to99 = (v * 99 / 127.0).roundToInt()
            when (b[1]) {
                7 -> { master = (v / 127.0).pow(1.09); return }    // measured: 64 = -6.5 dB, 32 = -12 dB
                76 -> { buf[137] = to99; for (x in voices) x.lfo.f = lfoHz(to99); return }
                77 -> { buf[139] = to99; for (x in voices) { x.lfo.pmd = to99 / 99.0 * PMS[buf[143]]; x.lfoActive = true }; return }
                78 -> { buf[138] = to99; return }
            }
        }
        if (type == 0xB0 && b[1] == 64) { sustain = b[2] >= 64; if (!sustain) voices.filter { it.held && !it.down }.forEach(::release); return }
        if (type == 0xC0) bank?.let { buf = it[b[1] and 31].copyOf() }
    }
    private fun sysex(b: IntArray) {
        if (b.size < 4 || b[1] != 0x43) return
        if ((b[2] and 0xF0) == 0x10 && b.size >= 7) {
            val p = ((b[3] and 3) shl 7) or b[4]
            if (p <= 155) buf[p] = b[5] and 127
        } else if ((b[2] and 0xF0) == 0 && b[3] == 0 && b.size >= 6 + 155) {
            for (i in 0 until 155) buf[i] = b[6 + i] and 127; buf[155] = 63
        } else if ((b[2] and 0xF0) == 0 && b[3] == 9 && b.size >= 6 + 4096) {
            bank = (0 until 32).map { vmemToVced(b, 6 + it * 128) }
        }
    }

    /** FM-1+VA LFO speed (measured with CC 76): LFS 31 = 5.0 Hz, 62 = 10.4, 86 = 33.7, 99 = 50.7 (log-interpolated). */
    fun lfoHz(lfs: Int): Double {
        val p = arrayOf(0.0 to 0.06, 31.0 to 5.0, 62.0 to 10.4, 86.0 to 33.7, 99.0 to 50.7)
        for (i in 1 until p.size) if (lfs <= p[i].first) {
            val (a, fa) = p[i - 1]; val (b, fb) = p[i]; val t = (lfs - a) / (b - a)
            return fa * (fb / fa).pow(t)
        }
        return 50.7
    }

    private fun noteOn(key: Int, vel: Int) {
        val p = buf
        if (voices.size >= maxVoices) {
            var k = voices.indexOfFirst { !it.down && !it.held }
            if (k < 0) k = 0
            voices.removeAt(k)
        }
        val note = (key + p[144] - 24).coerceIn(0, 127)
        val alg = ALG[p[134] and 31]
        val v = Voice(key, alg, 440.0 * 2.0.pow((note - 69) / 12.0), if (p[135] != 0) FB_SCALE * 2.0.pow(p[135] - 8.0) else 0.0)
        val rateScale = { sens: Int -> (min(31, max(0, note / 3 - 7)) * sens) shr 3 }
        for (n in 1..6) {
            val o = (6 - n) * 21
            val off = note - p[o + 8] - 17
            fun curve(group: Int, depth: Int, c: Int): Int {
                val s = if (c == 0 || c == 3) (group * depth * 329) shr 12 else (EXPSCALE[min(group, 32)] * depth * 329) shr 15
                return if (c < 2) -s else s
            }
            val kls = if (off >= 0) curve((off + 1) / 3, p[o + 10], p[o + 12]) else curve(-(off - 1) / 3, p[o + 9], p[o + 11])
            var olUnits = (scaleOut(p[o + 16]) + kls).coerceIn(0, 127) shl 5
            // velocity: DX7 table, but the FM-1 never boosts above the stored level (0.87 dB/KVS step lower: 257, not 239)
            olUnits += ((p[o + 15] * (VEL[min(127, vel) shr 1] - 257) + 7) shr 3) shl 4
            val mode = p[o + 17]; val fc = p[o + 18]; val ff = p[o + 19]; val dt = p[o + 20]
            val op = Op(((p[155] shr (6 - n)) and 1) == 1, intArrayOf(p[o], p[o + 1], p[o + 2], p[o + 3]), intArrayOf(p[o + 4], p[o + 5], p[o + 6], p[o + 7]),
                olUnits, rateScale(p[o + 13]), AMS[p[o + 14]],
                if (mode != 0) 10.0.pow(fc and 3) * 10.0.pow(ff / 100.0) else 0.0,
                (if (fc == 0) 0.5 else fc.toDouble()) * (1 + ff / 100.0) * 2.0.pow((dt - 7) * (if (profile == "va") 0.9 else 2.8) / 1200))
            v.ops[n] = op
            advance(op, 0)
        }
        v.peg = Peg(PLV[p[133]].toDouble(), intArrayOf(p[126], p[127], p[128], p[129]), intArrayOf(p[130], p[131], p[132], p[133]))
        v.pegActive = v.peg.L.any { it != 50 }
        pegAdvance(v.peg, 0)
        val va = profile == "va"; val lf = if (va) lfoHz(p[137]) else LFO_HZ
        val d = (p[138] / 99.0).pow(2)
        v.lfo = Lfo(lf, (now * lf / sr) % 1.0, if (va) p[142] else 4, if (va) d * 4 * sr else 0.0, if (va) max(1.0, d * 2 * sr) else 1.0,
            p[139] / 99.0 * PMS[p[143]], p[140] / 99.0)
        v.lfoActive = v.lfo.pmd > 0 || v.lfo.amd > 0
        modUpdate(v, 0)
        voices.add(v)
    }
    private fun noteOff(key: Int) {
        for (v in voices) if (v.key == key && v.down) { if (sustain) { v.held = true; v.down = false } else release(v) }
    }
    private fun release(v: Voice) {
        v.down = false; v.held = false
        for (n in 1..6) advance(v.ops[n]!!, 3)
        pegAdvance(v.peg, 3)
    }
    private fun advance(o: Op, ix: Int) {
        o.ix = ix
        if (ix > 3) return
        o.target = max(16, ((scaleOut(o.L[ix]) shr 1) shl 6) + o.olUnits - 4256).toDouble()
        o.rising = o.target > o.level
        val qr = min(63, ((o.R[ix] * 41) shr 6) + o.rs)
        o.inc = (4 + (qr and 3)) * 2.0.pow(2 + (qr shr 2)) / 65536 * rateK
    }
    private fun pegAdvance(g: Peg, ix: Int) {
        g.ix = ix
        if (ix > 3) return
        g.target = PLV[g.L[ix]].toDouble()
        g.rising = g.target > g.level
        g.inc = PRATE[g.R[ix]] * 32 / (29.2 * sr)
    }
    private fun modUpdate(v: Voice, n: Int) {
        val g = v.peg
        if (v.pegActive && (g.ix < 3 || (g.ix < 4 && !v.down))) {
            if (g.rising) { g.level += g.inc * n; if (g.level >= g.target) { g.level = g.target; pegAdvance(g, g.ix + 1) } }
            else { g.level -= g.inc * n; if (g.level <= g.target) { g.level = g.target; pegAdvance(g, g.ix + 1) } }
        }
        var semis = if (v.pegActive) g.level * 12 / 32 else 0.0
        var am = 0.0
        if (v.lfoActive) {
            val l = v.lfo
            l.t += n; val prev = l.ph; l.ph = (l.ph + l.f * n / sr) % 1.0
            if (l.ph < prev) l.sh = Math.random() * 2 - 1
            val x = l.ph
            val value = when (l.wave) { 0 -> if (x < 0.5) 4 * x - 1 else 3 - 4 * x; 1 -> 1 - 2 * x; 2 -> 2 * x - 1; 3 -> if (x < 0.5) 1.0 else -1.0
                4 -> sin(2 * PI * x); 5 -> l.sh; else -> 0.0 }
            val gain = ((l.t - l.delay) / l.ramp).coerceIn(0.0, 1.0)
            semis += value * gain * l.pmd
            am = (1 - value) / 2 * gain * l.amd
        }
        val mul = 2.0.pow(semis / 12)
        for (k in 1..6) {
            val o = v.ops[k]!!
            o.dph = (if (o.fixed != 0.0) o.fixed else v.baseHz * o.ratio * mul) / sr
            o.amod = am * o.ams
        }
    }
    private fun renderVoice(v: Voice, out: FloatArray, i0: Int, i1: Int) {
        val mods = v.alg.mods; val car = v.alg.carriers; val y = v.y
        for (i in i0 until i1) {
            if ((v.ctr++ and 31) == 0) modUpdate(v, 32)
            for (k in 6 downTo 1) {
                val o = v.ops[k]!!
                if (o.ix < 3 || (o.ix < 4 && !v.down)) {
                    if (o.rising) {
                        if (o.level < 1716) o.level = 1716.0
                        o.level += (17 - floor(o.level / 256)) * o.inc
                        if (o.level >= o.target) { o.level = o.target; advance(o, o.ix + 1) }
                    } else {
                        o.level -= o.inc
                        if (o.level <= o.target) { o.level = o.target; advance(o, o.ix + 1) }
                    }
                }
                if (!o.on) { y[k] = 0.0; continue }
                var m = 0.0
                for (src in mods[k]) m += y[src]
                m *= 2
                if (k == v.alg.fb && v.fbAmt != 0.0) m += (v.fb1 + v.fb2) * v.fbAmt
                var ph = o.phase + m; ph -= floor(ph)
                val x = ph * SIN_N; val xi = x.toInt()
                val s = SIN[xi] + (SIN[xi + 1] - SIN[xi]) * (x - xi)
                val lv = o.level - o.amod
                val value = s * EXP[if (lv <= 0) 0 else if (lv >= 4096) 4096 else lv.toInt()]
                y[k] = value
                if (k == v.alg.fb) { v.fb2 = v.fb1; v.fb1 = value }
                o.phase += o.dph; if (o.phase >= 1) o.phase -= 1
            }
            var sum = 0.0
            for (c in car) sum += y[c]
            out[i] = (out[i] + sum).toFloat()
        }
    }
    private fun finished(v: Voice): Boolean {
        if (v.down || v.held) return false
        for (c in v.alg.carriers) { val o = v.ops[c]!!; if (o.on && o.ix < 4 && o.level > 900) return false }
        return true
    }

    /** Render out.size samples starting at absolute frame [start]; messages apply at their exact sample. */
    @Synchronized fun render(out: FloatArray, start: Long) {
        out.fill(0f)
        val n = out.size
        var i = 0
        while (i < n) {
            now = start + i
            while (q.isNotEmpty() && q[0].f <= start + i) midi(q.removeAt(0).b)
            val j = if (q.isNotEmpty()) min(n.toLong(), max(i + 1L, q[0].f - start)).toInt() else n
            for (v in voices) renderVoice(v, out, i, j)
            i = j
        }
        voices.removeAll(::finished)
        val g = 0.2 * volume * master
        val a = 1 - exp(-2 * PI * OUT_LP_HZ / sr)
        for (k in 0 until n) { lpZ += a * (out[k] - lpZ); out[k] = (lpZ * g).toFloat() }
        fx.process(out)
        for (k in 0 until n) out[k] = tanh(out[k].toDouble()).toFloat()
    }
}

/** The FM-1's effect section on CC 0-23 of the effect channel (port of FM1Fx in app/fm1-synth.js). */
class Fm1Fx(private val sr: Int) {
    val cc = IntArray(24).also { c ->
        listOf(2 to 80, 3 to 2, 5 to 1, 6 to 50, 7 to 30, 9 to 40, 10 to 40, 11 to 30, 13 to 30, 14 to 50, 15 to 50,
            17 to 30, 18 to 50, 19 to 40, 21 to 30, 22 to 50, 23 to 40).forEach { (k, v) -> c[k] = v }
    }
    private fun buf(s: Double) = FloatArray(ceil(s * sr).toInt() + 2)
    private val svf = DoubleArray(2); private var toneZ = 0.0
    private val dl = buf(1.05); private var dlw = 0
    private val chb = buf(0.04); private var chw = 0; private var chPh = 0.0
    private val ap = DoubleArray(4); private var apA = 0.0; private var phPh = 0.0; private var phFb = 0.0; private var phN = 0
    private class Comb(val b: FloatArray) { var i = 0; var z = 0.0 }
    private class Ap(val b: FloatArray) { var i = 0 }
    private val combs = intArrayOf(1116, 1188, 1277, 1356).map { Comb(FloatArray((it * sr / 44100.0 * 1.25).roundToInt())) }
    private val aps = intArrayOf(556, 441).map { Ap(FloatArray((it * sr / 44100.0).roundToInt())) }
    private var distOn = false; private var drive = 1.0; private var toneA = 1.0; private var level = 1.0
    private var filtOn = false; private var ftype = 0; private var g = 0.0; private var k = 1.0
    private var revOn = false; private var revLen = IntArray(4); private var revFb = 0.0; private var revDamp = 0.0; private var revMix = 0.0
    private var dlOn = false; private var dlFb = 0.0; private var dlLen = 1; private var dlMix = 0.0
    private var chOn = false; private var chRate = 0.0; private var chDepth = 0.0; private var chMix = 0.0
    private var phOn = false; private var phRate = 0.0; private var phDepth = 0.0; private var phMix = 0.0
    init { update() }

    fun set(n: Int, v: Int) { if (n < 24) { cc[n] = v; update() } }
    private fun update() {
        val c = cc
        distOn = c[12] > 0; drive = 10.0.pow(c[13] * 0.36 / 20)
        toneA = 1 - exp(-2 * PI * 800 * 15.0.pow(c[14] / 100.0) / sr); level = 10.0.pow((c[15] - 50) * 0.47 / 20)
        filtOn = c[0] > 0; ftype = min(2, c[1])
        g = tan(PI * min(sr * 0.45, 20 * 2.0.pow(min(107, c[2]) / 107.0 * 10)) / sr)
        k = 1 / (0.5 + min(10, c[3]) * 1.2)
        revOn = c[4] > 0; val size = doubleArrayOf(0.55, 1.0, 0.8)[min(2, c[5])]
        revLen = IntArray(4) { max(1, floor(combs[it].b.size * size).toInt()) }
        revFb = 0.7 + min(100, c[6]) / 100.0 * 0.27; revDamp = doubleArrayOf(0.35, 0.25, 0.1)[min(2, c[5])]; revMix = min(100, c[7]) / 100.0
        dlOn = c[8] > 0; dlFb = min(100, c[9]) / 100.0 * 0.9
        dlLen = min(dl.size - 2, ((40 + min(100, c[10]) * 9.6) / 1000 * sr).roundToInt()); dlMix = min(100, c[11]) / 100.0
        chOn = c[16] > 0; chRate = 0.1 * 50.0.pow(c[17] / 100.0); chDepth = c[18] / 100.0 * 0.008 * sr; chMix = min(100, c[19]) / 100.0
        phOn = c[20] > 0; phRate = 0.05 * 80.0.pow(c[21] / 100.0); phDepth = min(100, c[22]) / 100.0; phMix = min(100, c[23]) / 100.0
    }
    fun process(buf: FloatArray) {
        for (i in buf.indices) {
            var x = buf[i].toDouble()
            if (distOn) { x = tanh(x * drive); toneZ += toneA * (x - toneZ); x = toneZ * level }
            if (filtOn) {
                val a1 = 1 / (1 + g * (g + k)); val a2 = g * a1; val a3 = g * a2
                val v3 = x - svf[1]; val v1 = a1 * svf[0] + a2 * v3; val v2 = svf[1] + a2 * svf[0] + a3 * v3
                svf[0] = 2 * v1 - svf[0]; svf[1] = 2 * v2 - svf[1]
                x = when (ftype) { 0 -> v2; 1 -> v1 * k; else -> x - k * v1 - v2 }
            }
            if (chOn) {
                val L = chb.size
                chb[chw] = x.toFloat(); chPh = (chPh + chRate / sr) % 1.0
                var r = chw - (0.012 * sr + chDepth * (0.5 + 0.5 * sin(2 * PI * chPh)))
                while (r < 0) r += L
                val r0 = r.toInt(); val f = r - r0
                val y = chb[r0] * (1 - f) + chb[(r0 + 1) % L] * f
                chw = (chw + 1) % L
                x = x * (1 - chMix * 0.5) + y * chMix * 0.5
            }
            if (phOn) {
                if ((phN++ and 15) == 0) {
                    phPh = (phPh + 16 * phRate / sr) % 1.0
                    val f = 300 * 2.0.pow(phDepth * 3 * (0.5 + 0.5 * sin(2 * PI * phPh))); val t = tan(PI * min(f, sr * 0.45) / sr)
                    apA = (t - 1) / (t + 1)
                }
                var u = x + phFb * 0.5
                for (s in 0 until 4) { val y = apA * u + ap[s]; ap[s] = u - apA * y; u = y }
                phFb = u
                x = x * (1 - phMix * 0.5) + u * phMix * 0.5
            }
            if (dlOn) {
                val L = dl.size
                var r = dlw - dlLen; if (r < 0) r += L
                val y = dl[r].toDouble()
                dl[dlw] = (x + y * dlFb).toFloat(); dlw = (dlw + 1) % L
                x += y * dlMix
            }
            if (revOn) {
                var wet = 0.0
                val inp = x * 0.03
                for (c in 0 until 4) {
                    val cb = combs[c]; val y = cb.b[cb.i].toDouble()
                    cb.z = y * (1 - revDamp) + cb.z * revDamp
                    cb.b[cb.i] = (inp + cb.z * revFb).toFloat()
                    if (++cb.i >= revLen[c]) cb.i = 0
                    wet += y
                }
                for (a in aps) { val y = a.b[a.i].toDouble(); a.b[a.i] = (wet + y * 0.5).toFloat(); wet = y - wet; if (++a.i >= a.b.size) a.i = 0 }
                x += wet * revMix * 3
            }
            buf[i] = x.toFloat()
        }
    }
}
