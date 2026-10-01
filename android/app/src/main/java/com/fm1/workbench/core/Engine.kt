package com.fm1.workbench.core

import java.util.PriorityQueue

/**
 * Timing + device engine (port of engine.js, just-in-time mode). Pure Kotlin: the clock and the MIDI sink are
 * injected so unit tests can run it on simulated time; on Android a high-priority thread calls [tick] every ~1 ms.
 *
 * Hardware facts it is built on (FINDINGS.md):
 *  - single-parameter SysEx changes apply to the NEXT note with 0 ms latency; ringing notes keep their voice
 *  - full VCED dumps stall the FM-1 for 20-450 ms at random, so real-time voice changes send parameter diffs only
 *  - the FM-1 ignores CC120/CC123 and plays every channel: panic sends explicit note-offs on all 16 channels
 *  - the FM-1 matches a note-off using the transpose (TRNP) in effect at note-OFF time: if TRNP changes while a note is
 *    held, the note hangs forever. So the device's TRNP stays at 24 (none) and each voice's transpose is applied by
 *    shifting the note number instead.
 */
class Engine(private val now: () -> Double, private val sink: (IntArray, Double) -> Unit) {
    interface Producer { fun schedule(horizon: Double) }

    /** Planned work from producers; merged across producers and executed in time order after each planning pass. */
    private class Plan(val t: Double, val seq: Long, val vced: IntArray?, val note: Int, val vel: Int, val gate: Double?,
                       val bytes: IntArray?, val onPlayed: ((Double) -> Unit)?)
    private val plans = ArrayList<Plan>()
    private var planSeq = 0L

    companion object {
        const val LOOKAHEAD = 120.0      // ms planned ahead
        const val PREP_LEAD = 30.0       // how early a voice diff may go out before its note
        const val MS_PER_PARAM = 0.17    // measured transmit+parse cost per 7-byte param message
        const val PLAN_EVERY = 15.0
    }

    var ch = 0
    /** Hand queued messages to the sink this many ms early, with their intended time. On Android the MIDI service then
     *  delivers them at that timestamp, so a late wake-up of our tick thread (screen off) no longer delays notes. */
    var sendAhead = 0.0
    var connected = true                 // false: sends are dropped (no device)
    var dev: IntArray? = null            // mirror of the FM-1 edit buffer (156 values), null = unknown
        private set
    var lastOnAt = 0.0
        private set
    /** When the most recently scheduled voice diff has been sent. The device mirror changes when a diff is *scheduled*,
     *  so a later note that needs no diff of its own must still wait for it (e.g. the 2nd note of a chord). */
    private var voiceReadyAt = 0.0
    private val lastOn = IntArray(128) { -1 }
    private var onSeq = 0
    /** Transpose of the loaded voice (semitones), applied to note numbers. */
    var trn = 0; private set
    private val keyOf = HashMap<Int, Int>()           // note id -> key actually sent, so note-offs match their note-on
    private data class Off(val t: Double, val note: Int, val id: Int)
    private val offQueue = PriorityQueue<Off>(compareBy { it.t })
    private data class Out(val t: Double, val seq: Long, val bytes: IntArray)
    private val outQ = PriorityQueue<Out>(compareBy<Out> { it.t }.thenBy { it.seq })
    private var outSeq = 0L
    private val producers = LinkedHashSet<Producer>()
    private var lastPlan = -1e9
    var switches = 0; private set
    var paramsSent = 0L; private set

    val running get() = synchronized(this) { producers.isNotEmpty() || offQueue.isNotEmpty() || outQ.isNotEmpty() }

    private fun raw(b: IntArray, at: Double = 0.0) { if (connected) sink(b, at) }

    /** Send now, or queue for time [t] (ms on the engine clock). */
    @Synchronized fun send(bytes: IntArray, t: Double? = null) {
        if (t == null || t <= now() + 0.5) raw(bytes) else outQ.add(Out(t, outSeq++, bytes))
    }

    @Synchronized fun forgetDevice() { dev = null; lfoSent.clear() }
    /** "stock" (M-VAVE) or "va" (Baud Girl's FM-1+VA firmware). */
    @Volatile var firmware = "stock"
    private val lfoSent = HashMap<Int, Int>()

    /** Send only the parameters that differ from the device mirror. Returns (count, time when done). */
    @Synchronized fun setVoice(vced: IntArray, t: Double? = null, opMask: Int = 63, withName: Boolean = false): Pair<Int, Double> {
        val want = IntArray(156).also { vced.copyInto(it, 0, 0, 155); it[155] = opMask; it[144] = 24 }   // device TRNP stays neutral
        trn = vced[144] - 24
        val idx = (0 until 145) + (if (withName) (145 until 155) else IntRange.EMPTY) + 155
        var n = 0
        val d = dev
        for (p in idx) {
            if (d != null && d[p] == want[p]) continue
            send(Dx7.paramSysex(p, want[p], ch), t); n++
        }
        dev = want
        // Baud Girl's FM-1+VA firmware: the DX7 LFO speed/delay params never reach the running LFO (measured), but its
        // CC 76 (LFO Speed) and CC 78 (LFO Delay) on the MIDI channel do.
        if (firmware == "va") for ((p, c) in listOf(137 to 76, 138 to 78)) {
            val v = Math.round(want[p] * 127 / 99.0).toInt()
            if (lfoSent[c] != v) { send(intArrayOf(0xB0 or ch, c, v), t); lfoSent[c] = v; n++ }
        }
        val doneAt = (t ?: now()) + n * MS_PER_PARAM
        if (n > 0) { switches++; paramsSent += n; voiceReadyAt = maxOf(voiceReadyAt, doneAt + 0.5) }
        return n to doneAt
    }

    @Synchronized fun setParam(p: Int, v: Int, t: Double? = null) {
        if (p == 144) { trn = v - 24; return }           // transpose = note shift, never the device's TRNP
        send(Dx7.paramSysex(p, v, ch), t)
        dev?.let { it[p] = v }
    }

    /** A note with a specific voice at time [t]; the diff goes out right after the previous note-on (max PREP_LEAD early). */
    @Synchronized fun hit(vced: IntArray?, note: Int, vel: Int, t: Double, gateMs: Double?, opMask: Int = 63, withName: Boolean = false): Int {
        var noteAt = t
        if (vced != null) {
            val prepAt = maxOf(lastOnAt + 0.5, t - PREP_LEAD, now())
            setVoice(vced, prepAt, opMask, withName)
            noteAt = maxOf(t, voiceReadyAt)
        }
        return note(note, vel, noteAt, gateMs)
    }

    /** Note-on with retrigger handling: a newer note on the same key cuts the old one; stale offs are skipped. */
    @Synchronized fun note(note: Int, vel: Int, t: Double, gateMs: Double?): Int {
        val key = (note + trn).coerceIn(0, 127)
        if (lastOn[key] >= 0) send(intArrayOf(0x80 or ch, key, 0), t)
        val id = ++onSeq
        lastOn[key] = id; keyOf[id] = key
        send(intArrayOf(0x90 or ch, key, vel.coerceIn(1, 127)), t)
        lastOnAt = maxOf(lastOnAt, t)
        if (gateMs != null) offQueue.add(Off(t + gateMs, key, id))
        return id
    }

    /** [note] as played (before transpose); with an [id] the key that note-on actually used is looked up. */
    @Synchronized fun noteOff(note: Int, t: Double? = null, id: Int? = null, raw: Boolean = false) {
        val key = id?.let { keyOf.remove(it) } ?: if (raw) note else (note + trn).coerceIn(0, 127)
        if (id != null && lastOn[key] != id) return
        lastOn[key] = -1
        send(intArrayOf(0x80 or ch, key, 0), t)
    }

    /** Time for catch-up note-offs: note-ons may already be out with timestamps up to [sendAhead] ms ahead, so an
     *  untimed note-off could overtake its own note-on and leave the note hanging. Send the offs just after that window. */
    private fun afterSent() = now() + sendAhead + 1

    /** Release everything on every channel (the FM-1 ignores All Notes Off and plays all channels). */
    @Synchronized fun panic() {
        offQueue.clear()
        if (outQ.any { it.bytes[0] == 0xF0 }) dev = null
        outQ.clear()                                        // every key gets a note-off below anyway
        val at = afterSent()
        for (c in 0 until 16) {
            for (n in 0 until 128) raw(intArrayOf(0x80 or c, n, 0), at)
            raw(intArrayOf(0xB0 or c, 64, 0), at)
        }
        lastOn.fill(-1); keyOf.clear()
    }

    @Synchronized fun start(p: Producer) { producers += p; lastPlan = -1e9 }

    /** A producer that has planned everything leaves without [stop]'s cleanup: its queued tail still plays. */
    @Synchronized fun detach(p: Producer) { producers -= p }

    /** Stop a producer. Queued messages are still ours: drop them, release what sounded, resync the voice later. */
    @Synchronized fun stop(p: Producer) {
        producers -= p
        if (producers.isEmpty()) {
            if (outQ.any { it.bytes[0] == 0xF0 }) dev = null
            // Queued note-offs belong to notes that already sounded (their key is no longer marked held): send them now.
            // Dropping them left notes hanging. Queued note-ons and voice diffs are dropped.
            val at = afterSent()
            outQ.filter { it.bytes[0] and 0xF0 == 0x80 }.sortedBy { it.t }.forEach { raw(it.bytes, maxOf(it.t, at)) }
            outQ.clear(); offQueue.clear()
            for (n in 0 until 128) if (lastOn[n] >= 0) { raw(intArrayOf(0x80 or ch, n, 0), at); lastOn[n] = -1 }
            keyOf.clear()
            lastOnAt = 0.0; voiceReadyAt = 0.0
        }
    }

    /** For producers inside schedule(): queue a hit. Hits from all producers are sorted by time before they run,
     *  so voice diffs from two producers (sequencer + looper) can't land out of order. */
    fun planHit(vced: IntArray?, note: Int, vel: Int, t: Double, gateMs: Double?, onPlayed: ((Double) -> Unit)? = null) {
        plans += Plan(t, planSeq++, vced, note, vel, gateMs, null, onPlayed)
    }

    fun planSend(bytes: IntArray, t: Double) { plans += Plan(t, planSeq++, null, 0, 0, null, bytes, null) }

    /** Call every ~1 ms: send due messages; every ~15 ms let producers plan the next LOOKAHEAD ms. */
    @Synchronized fun tick() {
        flush()
        val t = now()
        if (t - lastPlan >= PLAN_EVERY) {
            lastPlan = t
            val horizon = t + LOOKAHEAD
            for (p in producers.toList()) p.schedule(horizon)
            plans.sortWith(compareBy<Plan> { it.t }.thenBy { it.seq })
            for (pl in plans) {
                val at = maxOf(pl.t, now())
                if (pl.bytes != null) send(pl.bytes, at) else { hit(pl.vced, pl.note, pl.vel, at, pl.gate); pl.onPlayed?.invoke(at) }
            }
            plans.clear()
            while (offQueue.isNotEmpty() && offQueue.peek()!!.t <= horizon) {
                val o = offQueue.poll()!!
                noteOff(o.note, o.t, o.id, raw = true)
            }
        }
        flush()
    }

    private fun flush() {
        val lim = now() + 0.7 + sendAhead
        while (outQ.isNotEmpty() && outQ.peek()!!.t <= lim) { val o = outQ.poll()!!; raw(o.bytes, o.t) }
    }
}
