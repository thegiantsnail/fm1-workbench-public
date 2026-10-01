package com.fm1.workbench.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/** Engine / sequencer / looper on a simulated clock, checked the same way the web app was (fake MIDI output). */
class EngineTest {
    private class Rig {
        var t = 0.0
        val sent = ArrayList<Pair<Double, IntArray>>()
        val engine = Engine({ t }) { b, _ -> sent += t to b }
        fun run(untilMs: Double) { while (t < untilMs) { engine.tick(); t += 1.0 } }
        /** Replay the output to find which voice (155 params) was in the device at each note-on. */
        fun noteOnsWithVoice(): List<Triple<Double, Int, IntArray>> {
            val dev = IntArray(156) { -1 }
            val out = ArrayList<Triple<Double, Int, IntArray>>()
            for ((at, m) in sent) {
                if (m[0] == 0xF0 && (m[2] and 0xF0) == 0x10) dev[(m[3] shl 7) or m[4]] = m[5]
                else if (m[0] and 0xF0 == 0x90) out += Triple(at, m[1], dev.copyOf(145))   // sound params; names aren't sent during playback
            }
            return out
        }
        fun balanced(): Boolean {
            val held = IntArray(128)
            for ((_, m) in sent) { if (m[0] and 0xF0 == 0x90) held[m[1]]++; if (m[0] and 0xF0 == 0x80) held[m[1]] = maxOf(0, held[m[1]] - 1) }
            return held.all { it == 0 }
        }
    }

    @Test fun firstHitSendsFullVoiceThenOnlyDiffs() {
        val r = Rig()
        val a = StarterKit.drumVoice("kick").vced; val b = StarterKit.drumVoice("snare").vced
        r.engine.hit(a, 48, 100, 10.0, 50.0); r.run(200.0)
        val first = r.sent.count { it.second[0] == 0xF0 }
        r.engine.hit(b, 60, 100, 300.0, 50.0); r.run(500.0)
        val second = r.sent.count { it.second[0] == 0xF0 } - first
        assertEquals(146, first)                                    // 145 sound params + op mask
        assertEquals((0 until 145).count { a[it] != b[it] }, second)
        val ons = r.noteOnsWithVoice()
        assertTrue(ons[0].third.contentEquals(a.copyOf(145))); assertTrue(ons[1].third.contentEquals(b.copyOf(145)))
        assertTrue(r.balanced())
    }

    @Test fun panicSweepsAllChannels() {
        val r = Rig(); r.engine.panic()
        assertEquals(16 * 128, r.sent.count { it.second[0] and 0xF0 == 0x80 })
        assertEquals((0 until 16).toSet(), r.sent.filter { it.second[0] and 0xF0 == 0x80 }.map { it.second[0] and 15 }.toSet())
    }

    @Test fun sequencerPlaysRightVoiceOnGrid() {
        val r = Rig()
        val p = StarterKit.defaultPattern().apply { bpm = 120 }
        val seq = Sequencer(r.engine, { r.t }, p, Random(1))
        seq.start(); r.run(2100.0); seq.stop(); r.run(2400.0)
        val ons = r.noteOnsWithVoice()
        assertTrue("notes played: ${ons.size}", ons.size >= 18)
        val t0 = ons[0].first; val sd = 125.0
        // First hit of each step exactly on the grid; further hits on the same step wait for their voice diff (~5-25 ms).
        for ((_, group) in ons.groupBy { Math.round((it.first - t0) / sd) }) {
            val step = Math.round((group[0].first - t0) / sd) * sd + t0
            assertEquals("first hit on grid", step, group.minOf { it.first }, 1.0)
            assertTrue("switch delays bounded: ${group.map { it.first - step }}", group.all { it.first - step < 30 })
        }
        for ((at, note, dev) in ons) {
            val cands = p.tracks.filter { it.note == note }
            assertTrue("right voice for note $note", cands.any { tr -> tr.voices.any { it.effective().copyOf(145).contentEquals(dev) } })
        }
        assertTrue("every note released", r.balanced())
    }

    @Test fun swingRatchetNudgePolymeter() {
        val r = Rig()
        val p = StarterKit.defaultPattern().apply { bpm = 120; swing = 50 }
        p.tracks.forEach { t -> t.steps.forEach { it.on = false } }
        p.tracks[0].steps[0].apply { on = true; rat = 4 }; p.tracks[0].ratPitch = 2
        p.tracks[1].steps[1].on = true
        p.tracks[2].steps[2].apply { on = true; nudge = -25 }
        p.tracks[4].len = 3; p.tracks[4].steps[0].on = true
        val seq = Sequencer(r.engine, { r.t }, p, Random(1)); seq.start(); r.run(1000.0); seq.stop()
        val ons = r.noteOnsWithVoice().map { it.first to it.second }
        val t0 = ons.first { it.second == 48 }.first
        val kick = ons.filter { it.second in 48..54 }.take(4).map { (it.first - t0).toInt() to it.second }
        assertEquals(listOf(0 to 48, 31 to 50, 62 to 52, 93 to 54), kick.map { (Math.round(it.first / 31.25) * 31).toInt().let { x -> (if (x == 93) 93 else x) } to it.second })
        assertEquals(187.5, ons.first { it.second == 60 }.first - t0, 2.0)        // odd step swung by 50%
        assertEquals(218.75, ons.first { it.second == 72 }.first - t0, 2.0)       // nudged -25%
        val toms = ons.filter { it.second == 55 }.map { it.first - t0 }
        assertEquals(437.5, toms[1], 3.0)                                          // polymeter: step 3 (swung)
    }

    @Test fun looperRecordsVoicesAndReplays() {
        val r = Rig()
        val lp = Looper(r.engine, { r.t }).apply { bpm = 120 }                  // bar = 2000 ms
        val kick = StarterKit.drumVoice("kick").vced; val snare = StarterKit.drumVoice("snare").vced
        r.t = 1000.0
        lp.record()
        r.t = 1000.0; lp.noteOn(36, 110, kick); r.t = 1100.0; lp.noteOff(36)
        r.t = 2000.0; lp.noteOn(38, 90, snare); r.t = 2080.0; lp.noteOff(38)
        r.t = 3050.0; lp.record()                                                 // ~1 bar -> snaps to 2000 ms
        assertEquals(Looper.State.PLAYING, lp.state)
        assertEquals(2000.0, lp.lengthMs, 0.001)
        r.run(7100.0)                                                             // two more cycles
        val ons = r.noteOnsWithVoice()
        val kicks = ons.filter { it.second == 36 }; val snares = ons.filter { it.second == 38 }
        assertEquals(listOf(5000.0, 7000.0), kicks.map { Math.round(it.first).toDouble() }.filter { it >= 5000 }.take(2))
        assertTrue(kicks.all { it.third.contentEquals(kick.copyOf(145)) }); assertTrue(snares.all { it.third.contentEquals(snare.copyOf(145)) })
        assertEquals(listOf(4000.0, 6000.0), snares.map { Math.round(it.first).toDouble() }.take(2))

        // overdub a hat on the next pass, then undo it
        val hat = StarterKit.drumVoice("hat").vced
        lp.record(); assertEquals(Looper.State.OVERDUBBING, lp.state)
        r.t = 7500.0; lp.noteOn(42, 80, hat); r.t = 7550.0; lp.noteOff(42)
        lp.record(); assertEquals(2, lp.layers)
        r.run(9700.0)
        assertTrue(r.noteOnsWithVoice().any { it.second == 42 && it.first > 9000 && it.third.contentEquals(hat.copyOf(145)) })
        lp.undo(); assertEquals(1, lp.layers)
        lp.stop(); r.run(10000.0)
        assertTrue(r.balanced())
    }

    @Test fun sequencerAndLooperTogetherKeepVoicesRight() {
        val r = Rig()
        val p = StarterKit.defaultPattern().apply { bpm = 120 }
        val seq = Sequencer(r.engine, { r.t }, p, Random(2)); seq.start()
        val lp = Looper(r.engine, { r.t }).apply { bpm = 120 }
        val bell = Dx7.initVoice("BELL").also { it[Dx7.paramIndex(1, "FC")] = 3 }
        r.run(500.0); lp.record()
        for (i in 0 until 4) { r.run(500.0 + i * 500 + 250); lp.noteOn(84, 100, bell); r.run(500.0 + i * 500 + 300); lp.noteOff(84) }
        r.run(2500.0); lp.record(); r.run(6500.0)
        seq.stop(); lp.stop(); r.run(6900.0)
        val ons = r.noteOnsWithVoice()
        assertTrue(ons.filter { it.second == 84 && it.first > 2600 }.size >= 6)
        for ((_, note, dev) in ons) {
            if (note == 84) assertTrue("looper note has its bell voice", dev.contentEquals(bell.copyOf(145)))
            else assertTrue("seq note $note has its kit voice", p.tracks.filter { it.note == note }.any { tr -> tr.voices.any { it.effective().copyOf(145).contentEquals(dev) } })
        }
        assertTrue(r.balanced())
    }
}
