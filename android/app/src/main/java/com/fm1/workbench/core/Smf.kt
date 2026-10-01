package com.fm1.workbench.core

/** Standard MIDI File parser -> time-sorted notes/controllers in milliseconds (port of smf.js). */
object Smf {
    data class Note(val t: Double, var dur: Double?, val ch: Int, val note: Int, val vel: Int, val trk: Int)
    data class Ctrl(val t: Double, val ch: Int, val kind: String, val a: Int, val b: Int)
    data class ChanInfo(val ch: Int, var count: Int = 0, var lo: Int = 127, var hi: Int = 0, val trks: MutableSet<Int> = mutableSetOf(),
                        var pc: Int? = null, var name: String = "")
    class Song(val format: Int, val ppq: Int?, val bpm: Double, val duration: Double, val notes: List<Note>, val ctrl: List<Ctrl>,
               val chans: Map<Int, ChanInfo>, val trackNames: List<String>)

    private class Raw(val tick: Long, val order: Int, val trk: Int, val kind: String, val ch: Int,
                      val note: Int = 0, val vel: Int = 0, val a: Int = 0, val b: Int = 0)

    fun parse(bytes: ByteArray): Song {
        val d = IntArray(bytes.size) { bytes[it].toInt() and 0xFF }
        var p = 0
        fun u32(): Long { val v = (d[p].toLong() shl 24) or (d[p + 1].toLong() shl 16) or (d[p + 2].toLong() shl 8) or d[p + 3].toLong(); p += 4; return v }
        fun u16(): Int { val v = (d[p] shl 8) or d[p + 1]; p += 2; return v }
        fun str(n: Int): String { val s = String(CharArray(n) { d[p + it].toChar() }); p += n; return s }
        fun vlq(): Long { var v = 0L; var b: Int; do { b = d[p++]; v = (v shl 7) or (b and 0x7F).toLong() } while (b and 0x80 != 0); return v }

        // Some files (RIFF RMID) wrap the SMF; find the MThd header.
        for (i in 0 until minOf(d.size - 4, 64)) if (d[i] == 0x4D && d[i + 1] == 0x54 && d[i + 2] == 0x68 && d[i + 3] == 0x64) { p = i; break }
        require(d.size >= 14 && str(4) == "MThd") { "Not a MIDI file (no MThd header)" }
        val hlen = u32().toInt(); val format = u16(); val ntrk = u16(); val div = u16()
        p += hlen - 6
        val smpte = div and 0x8000 != 0
        val ppq = if (smpte) null else div
        val msPerTickSmpte = if (smpte) 1000.0 / ((256 - (div shr 8)) * (div and 0xFF)) else 0.0

        val raw = ArrayList<Raw>()
        val tempos = ArrayList<Pair<Long, Int>>()          // tick, microseconds per quarter
        val names = ArrayList<String>()
        var order = 0
        var t = 0
        while (t < ntrk && p < d.size - 8) {
            val id = str(4); val len = u32().toInt(); val end = minOf(p + len, d.size)
            if (id != "MTrk") { p = end; t++; continue }
            var tick = 0L; var status = 0; var tname = ""
            while (p < end) {
                tick += vlq()
                val b = d[p]
                if (b and 0x80 != 0) { status = b; p++ } else if (status == 0) { p++; continue }
                val type = status and 0xF0; val ch = status and 0x0F
                if (status == 0xFF) {
                    val mt = d[p++]; val ml = vlq().toInt()
                    if (mt == 0x51 && ml == 3) tempos += tick to ((d[p] shl 16) or (d[p + 1] shl 8) or d[p + 2])
                    if (mt == 0x03 && tname.isEmpty()) tname = String(CharArray(ml) { d[p + it].toChar() })
                    p += ml; status = 0
                    if (mt == 0x2F) break
                } else if (status == 0xF0 || status == 0xF7) {
                    p += vlq().toInt(); status = 0
                } else if (type == 0xC0 || type == 0xD0) {
                    raw += Raw(tick, order++, t, if (type == 0xC0) "pc" else "at", ch, a = d[p++])
                } else {
                    val a = d[p++]; val c = d[p++]
                    when {
                        type == 0x90 && c > 0 -> raw += Raw(tick, order++, t, "on", ch, note = a, vel = c)
                        type == 0x80 || type == 0x90 -> raw += Raw(tick, order++, t, "off", ch, note = a)
                        type == 0xB0 -> raw += Raw(tick, order++, t, "cc", ch, a = a, b = c)
                        type == 0xE0 -> raw += Raw(tick, order++, t, "bend", ch, a = a, b = c)
                    }
                }
            }
            while (names.size <= t) names += ""
            names[t] = tname.trim()
            p = end; t++
        }

        // tick -> ms with the tempo map
        tempos.sortBy { it.first }
        if (tempos.isEmpty() || tempos[0].first > 0) tempos.add(0, 0L to 500000)
        val segTick = LongArray(tempos.size); val segMs = DoubleArray(tempos.size); val segUs = IntArray(tempos.size)
        var ms = 0.0
        for (i in tempos.indices) {
            if (i > 0) ms += (tempos[i].first - tempos[i - 1].first) * tempos[i - 1].second / 1000.0 / (ppq ?: 1)
            segTick[i] = tempos[i].first; segMs[i] = ms; segUs[i] = tempos[i].second
        }
        fun toMs(tick: Long): Double {
            if (smpte) return tick * msPerTickSmpte
            var lo = 0; var hi = segTick.size - 1
            while (lo < hi) { val m = (lo + hi + 1) / 2; if (segTick[m] <= tick) lo = m else hi = m - 1 }
            return segMs[lo] + (tick - segTick[lo]) * segUs[lo] / 1000.0 / ppq!!
        }

        val sorted = raw.sortedWith(compareBy<Raw> { it.tick }.thenBy { if (it.kind == "off") -1 else 0 }.thenBy { it.order })
        val open = HashMap<Int, ArrayDeque<Note>>()
        val notes = ArrayList<Note>(); val ctrl = ArrayList<Ctrl>()
        for (e in sorted) {
            val tm = toMs(e.tick)
            when (e.kind) {
                "on" -> { val n = Note(tm, null, e.ch, e.note, e.vel, e.trk); notes += n; open.getOrPut(e.ch * 128 + e.note) { ArrayDeque() }.addLast(n) }
                "off" -> open[e.ch * 128 + e.note]?.removeFirstOrNull()?.let { it.dur = maxOf(5.0, tm - it.t) }
                else -> ctrl += Ctrl(tm, e.ch, e.kind, e.a, e.b)
            }
        }
        var last = 0.0                                     // loops, not max(*spread): large files
        for (n in notes) if (n.t + (n.dur ?: 0.0) > last) last = n.t + (n.dur ?: 0.0)
        for (c in ctrl) if (c.t > last) last = c.t
        for (n in notes) if (n.dur == null) n.dur = maxOf(50.0, last - n.t)
        val bpm = 60000000.0 / tempos[0].second
        val chans = sortedMapOf<Int, ChanInfo>()
        for (n in notes) {
            val c = chans.getOrPut(n.ch) { ChanInfo(n.ch) }
            c.count++; c.lo = minOf(c.lo, n.note); c.hi = maxOf(c.hi, n.note); c.trks += n.trk
        }
        for (c in ctrl) if (c.kind == "pc") chans[c.ch]?.let { if (it.pc == null) it.pc = c.a }
        for (c in chans.values) c.name = c.trks.map { names.getOrElse(it) { "" } }.filter { it.isNotEmpty() }.joinToString(" / ")
        return Song(format, ppq, bpm, last, notes, ctrl, chans, names)
    }
}
