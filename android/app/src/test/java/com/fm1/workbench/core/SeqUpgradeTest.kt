package com.fm1.workbench.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/** Sequencer upgrades (accent, step gate, slide/tie, rates, 64 steps, chaining) + pattern JSON shared with the web app. */
class SeqUpgradeTest {
    private class Rig {
        var t = 0.0
        val sent = ArrayList<Pair<Double, IntArray>>()
        val engine = Engine({ t }) { b, _ -> sent += t to b }
        fun run(until: Double) { while (t < until) { engine.tick(); t += 0.5 } }
        /** note-ons with velocity and length (paired FIFO per key) */
        fun notes(): List<Triple<Double, Int, Pair<Int, Double?>>> {
            val open = HashMap<Int, ArrayDeque<Int>>(); val out = ArrayList<Triple<Double, Int, Pair<Int, Double?>>>(); val lens = ArrayList<Double?>()
            for ((at, m) in sent) {
                if (m[0] and 0xF0 == 0x90) { open.getOrPut(m[1]) { ArrayDeque() }.addLast(out.size); out += Triple(at, m[1], m[2] to null); lens += null }
                else if (m[0] and 0xF0 == 0x80) open[m[1]]?.removeFirstOrNull()?.let { lens[it] = at - out[it].first }
            }
            return out.mapIndexed { i, n -> Triple(n.first, n.second, n.third.first to lens[i]) }
        }
    }

    private fun blank(bpm: Int = 120) = StarterKit.defaultPattern().apply {
        this.bpm = bpm; tracks.forEach { t -> t.len = 16; t.steps.forEach { s -> s.on = false } }
    }

    private fun play(p: Pattern, ms: Double, chain: (() -> Pattern?)? = null): List<Triple<Double, Int, Pair<Int, Double?>>> {
        val r = Rig(); val seq = Sequencer(r.engine, { r.t }, p, Random(1)); seq.chainNext = chain
        seq.start(); r.run(ms); seq.stop(); r.run(ms + 300)
        return r.notes()
    }

    @Test fun accentAndStepGate() {
        val p = blank(); val s = p.tracks[0].steps; p.tracks[0].gate = 400
        s[0].apply { on = true; acc = true; vel = 60 }; s[4].apply { on = true; gate = 50 }
        val n = play(p, 700.0).filter { it.second == 48 }
        assertEquals(127, n[0].third.first)
        assertEquals(62.5, n[1].third.second!!, 1.5)
    }

    @Test fun slideOverlapsAndTieSpansEmptySteps() {
        val p = blank(); val s = p.tracks[0].steps; p.tracks[0].gate = 50
        s[0].apply { on = true; slide = true }; s[1].apply { on = true; pitch = 5 }
        s[4].apply { on = true; slide = true }; s[7].apply { on = true; pitch = 7 }
        val n = play(p, 1100.0)
        assertTrue("legato", n[0].first + n[0].third.second!! > n[1].first)
        assertEquals(3 * 125.0 + 15, n[2].third.second!!, 2.0)
    }

    @Test fun tripletRateAnd64Steps() {
        val p = blank().apply { rate = "1/16T" }; (0..3).forEach { p.tracks[0].steps[it].on = true }
        val n = play(p, 500.0)
        (0..2).forEach { assertEquals(83.33, n[it + 1].first - n[it].first, 1.0) }
        val q = blank(); q.tracks[0].len = 64; q.tracks[0].steps[0].on = true; q.tracks[0].steps[40].on = true
        val m = play(q, 5400.0).filter { it.second == 48 }
        assertEquals(5000.0, m[1].first - m[0].first, 1.0)
    }

    @Test fun chainSwitchesPatternsAtPatternEnd() {
        val a = blank().apply { len = 4 }; a.tracks[0].steps[0].on = true
        val b = blank().apply { len = 4 }; b.tracks[1].steps[0].on = true
        var i = 0
        val n = play(a, 2100.0) { i++; if (i % 2 == 1) b else a }
        assertEquals(listOf(48, 60, 48, 60), n.take(4).map { it.second })
        assertEquals(listOf(0.0, 500.0, 1000.0, 1500.0), n.take(4).map { Math.round(it.first - n[0].first).toDouble() })
    }

    @Test fun noChainKeepsPolymeter() {
        val p = blank().apply { len = 4 }; p.tracks[0].len = 3; p.tracks[0].steps[0].on = true
        val n = play(p, 1600.0)
        assertEquals(listOf(0.0, 375.0, 750.0, 1125.0), n.take(4).map { Math.round(it.first - n[0].first).toDouble() })
    }

    @Test fun webPatternJsonLoadsAndRoundTrips() {
        val web = PatternJson.parse(javaClass.classLoader!!.getResource("web_pattern.json")!!.readText())
        assertEquals("1/16T", web.rate); assertEquals(12, web.len); assertEquals(6, web.tracks.size)
        assertEquals(MAX_STEPS, web.tracks[0].steps.size)
        assertTrue(web.tracks[0].steps[0].acc); assertTrue(web.tracks[0].steps[7].slide)
        assertEquals(40, web.tracks[1].steps[4].gate); assertTrue(web.tracks[1].steps[40].on); assertEquals(48, web.tracks[1].len)
        assertEquals(-8, web.tracks[0].voices[0].macros["level"]); assertTrue(web.tracks[0].voices[0].macros.containsKey("sweep"))
        // same sound as the web's own starter kick
        assertEquals(StarterKit.drumVoice("kick").vced.toList(), web.tracks[0].voices[0].vced.toList())
        val back = PatternJson.parse(PatternJson.write(web))
        assertEquals(PatternJson.write(web), PatternJson.write(back))
    }

    @Test fun oldAndBrokenPatternsAreCleaned() {
        val old = """{"bpm": "fast", "tracks": [{"steps": [{"on": true, "vel": 999}], "len": -5, "voices": [{"vced": [999]}]}]}"""
        val p = PatternJson.parse(old)
        assertEquals(112, p.bpm); assertEquals(1, p.tracks[0].len); assertEquals(127, p.tracks[0].steps[0].vel)
        assertEquals(MAX_STEPS, p.tracks[0].steps.size); assertEquals(0, p.tracks[0].voices.size)
        try { PatternJson.parse("""{"tracks": []}"""); assertTrue("should reject", false) } catch (e: IllegalArgumentException) { }
    }
}
