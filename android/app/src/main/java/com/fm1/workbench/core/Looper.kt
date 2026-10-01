package com.fm1.workbench.core

/**
 * MIDI looper: records notes (from the FM-1's own keys, pads, on-screen keys) together with the voice that was
 * loaded when each note was played, then replays them in a loop through the Engine - so a loop can layer several
 * voices on the single-voice FM-1 (voice diffs go out between notes; ringing notes keep their sound).
 *
 * States: EMPTY -> RECORDING (first pass sets the loop length) -> PLAYING <-> OVERDUBBING. Each record/overdub pass
 * is a layer that can be undone. Loop length snaps to whole bars of [bpm] when [syncBars] is on.
 */
class Looper(private val engine: Engine, private val now: () -> Double) : Engine.Producer {
    enum class State { EMPTY, RECORDING, PLAYING, OVERDUBBING, STOPPED }

    /** One recorded note. [off] = position in the loop (ms). [vced] = voice it was played with (null = whatever is loaded). */
    data class Ev(val off: Double, val note: Int, val vel: Int, var dur: Double, val vced: IntArray?, val layer: Int, val echo: Boolean)

    @Volatile var state = State.EMPTY; private set
    var bpm = 112
    var syncBars = true
    var quantize = false                      // snap note starts to 16ths on replay
    var lengthMs = 0.0; private set
    val layers get() = events.maxOfOrNull { it.layer }?.plus(1) ?: 0

    private val events = ArrayList<Ev>()
    private val open = HashMap<Int, Pair<Ev, Double>>()   // note -> (event, absolute start) while recording
    private var t0 = 0.0                      // absolute time of loop position 0 (current cycle base for scheduling)
    private var planned = 0.0                 // absolute time up to which replay has been scheduled
    private var layer = 0
    private val barMs get() = 4 * 60000.0 / bpm
    private val stepMs get() = 60000.0 / bpm / 4

    fun events(): List<Ev> = synchronized(engine) { events.toList() }

    /** Loop position 0..1 for the UI. */
    fun position(): Double = if (lengthMs <= 0) 0.0 else (((now() - t0) % lengthMs + lengthMs) % lengthMs) / lengthMs

    // ---- transport ----
    /** Record button: EMPTY -> RECORDING; RECORDING -> PLAYING (closes the loop); PLAYING -> OVERDUBBING -> PLAYING. */
    fun record() = synchronized(engine) {
        when (state) {
            State.EMPTY -> { events.clear(); layer = 0; t0 = now(); state = State.RECORDING }
            State.RECORDING -> closeLoop()
            State.PLAYING -> { layer = layers; state = State.OVERDUBBING }
            State.OVERDUBBING -> { endHeldNotes(); state = State.PLAYING }
            State.STOPPED -> { layer = layers; play(); state = State.OVERDUBBING }
        }
    }

    private fun closeLoop() {
        val raw = now() - t0
        lengthMs = if (syncBars) maxOf(1.0, Math.round(raw / barMs).toDouble()) * barMs else raw
        endHeldNotes()
        events.removeAll { it.off >= lengthMs }               // played after the snapped end
        for (e in events) e.dur = minOf(e.dur, lengthMs)
        if (events.isEmpty()) { state = State.EMPTY; lengthMs = 0.0; return }
        // keep time continuous: the next cycle starts where the recording's (snapped) end is
        t0 += lengthMs * Math.floor((now() - t0) / lengthMs)
        planned = now()
        state = State.PLAYING
        engine.start(this)
    }

    fun play() = synchronized(engine) {
        if (lengthMs <= 0 || state == State.PLAYING || state == State.OVERDUBBING) return
        t0 = now() + 30; planned = t0; state = State.PLAYING
        engine.start(this)
    }

    fun stop() = synchronized(engine) {
        if (state == State.RECORDING) { closeLoop() }
        if (state == State.PLAYING || state == State.OVERDUBBING) { endHeldNotes(); engine.stop(this); state = State.STOPPED }
    }

    fun undo() = synchronized(engine) {
        if (layers == 0) return
        val last = layers - 1
        events.removeAll { it.layer == last }
        if (events.isEmpty()) clear()
    }

    fun clear() = synchronized(engine) {
        engine.stop(this); events.clear(); open.clear(); lengthMs = 0.0; layer = 0; state = State.EMPTY
    }

    // ---- input ----
    /**
     * A note was played live. [vced] = voice it sounds with; [echo] = true if the Engine must re-send it on replay
     * but it already sounded live on the unit (FM-1 keys) - it is recorded the same way either way.
     */
    fun noteOn(note: Int, vel: Int, vced: IntArray?, echo: Boolean = false) = synchronized(engine) {
        if (state != State.RECORDING && state != State.OVERDUBBING) return
        val t = now()
        var off = t - t0
        if (state == State.OVERDUBBING) off = ((off % lengthMs) + lengthMs) % lengthMs
        val e = Ev(off, note, vel, 100.0, vced?.copyOf(), layer, echo)
        events += e
        open[note] = e to t
    }

    fun noteOff(note: Int) = synchronized(engine) {
        val (e, start) = open.remove(note) ?: return
        e.dur = maxOf(10.0, now() - start)
    }

    private fun endHeldNotes() {
        val t = now()
        for ((_, p) in open) p.first.dur = maxOf(10.0, t - p.second)
        open.clear()
    }

    // ---- replay (Engine.Producer) ----
    override fun schedule(horizon: Double) {
        if (lengthMs <= 0) return
        val from = planned
        if (horizon <= from) return
        // cycles overlapping (from, horizon]
        var cycle = Math.floor((from - t0) / lengthMs).toLong()
        // <= : a note exactly on the loop boundary belongs to the window (from, horizon]; with < it fell between passes
        while (t0 + cycle * lengthMs <= horizon) {
            val base = t0 + cycle * lengthMs
            for (e in events) {
                if (state == State.OVERDUBBING && e.layer == layer) continue      // don't replay what is being recorded now
                var off = e.off
                if (quantize) off = Math.round(off / stepMs) * stepMs % lengthMs
                val t = base + off
                if (t > from && t <= horizon) engine.planHit(e.vced, e.note, e.vel, t, e.dur)
            }
            cycle++
        }
        planned = horizon
    }
}
