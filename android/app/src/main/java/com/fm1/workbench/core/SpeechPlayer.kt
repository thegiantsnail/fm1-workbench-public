package com.fm1.workbench.core

/**
 * Plays Speech.toEvents() output through the Engine (FM-1 or Software FM-1): the pre-timed parameter diffs and notes
 * are handed to the Engine's planner LOOKAHEAD ms ahead (planSend), so they merge in time order with other producers.
 * Our diffs bypass the Engine's mirror of the edit buffer, so it is forgotten before and after.
 */
class SpeechPlayer(private val engine: Engine, private val clock: () -> Double) : Engine.Producer {
    @Volatile var playing = false; private set
    private var events: List<Speech.Ev> = emptyList()
    private var i = 0
    private var t0 = 0.0
    private val keys = HashSet<Int>()
    var onEnded: (() -> Unit)? = null
    /** For diagnostics: events handed to the Engine / total, and ms since the first event was due. */
    val progress get() = "$i/${events.size} t=${"%.0f".format(clock() - t0)}ms of ${"%.0f".format(events.lastOrNull()?.t ?: 0.0)}ms"

    fun play(ev: List<Speech.Ev>) {
        stop()
        if (ev.isEmpty()) return
        events = ev; i = 0; keys.clear()
        t0 = clock() + 60
        engine.forgetDevice()
        playing = true
        engine.start(this)
    }

    override fun schedule(horizon: Double) {
        while (i < events.size && t0 + events[i].t <= horizon) {
            val e = events[i++]
            engine.planSend(e.b, t0 + e.t)
            if (e.b[0] and 0xF0 == 0x90) keys += e.b[1]
        }
        if (i >= events.size && playing) {
            engine.detach(this)                        // let the queued tail play out
            val endsIn = t0 + events.last().t - clock()
            Thread { Thread.sleep(maxOf(0L, endsIn.toLong() + 100)); if (playing && i >= events.size) finish() }.start()
        }
    }

    fun stop() {
        if (!playing) return
        engine.stop(this)
        // after any note-on already handed out with a timestamp (Engine.sendAhead), never before it
        val at = clock() + engine.sendAhead + 1
        for (k in keys) engine.send(intArrayOf(0x80 or engine.ch, k, 0), at)
        finish()
    }

    private fun finish() {
        playing = false
        engine.forgetDevice()
        onEnded?.invoke()
    }
}
