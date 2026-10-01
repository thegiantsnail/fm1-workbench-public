package com.fm1.workbench.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/** Editor generators and the MIDI file player, on a simulated clock. */
class Phase2Test {
    private fun inRange(v: IntArray) = (0 until 145).all { v[it] in 0..Dx7.maxOf(it) }

    @Test fun generatorsProduceValidVoices() {
        val rnd = Random(7)
        for ((style, _) in VoiceGen.STYLES) repeat(300) { assertTrue(style, inRange(VoiceGen.random(style, rnd))) }
        val a = VoiceGen.random("pad", rnd); val b = VoiceGen.random("bell", rnd)
        repeat(300) { assertTrue(inRange(VoiceGen.mutate(a, 50, rnd))) }
        for (t in listOf(0.0, 0.25, 0.5, 0.75, 1.0)) assertTrue(inRange(VoiceGen.morph(a, b, t)))
        assertEquals(a.copyOf(145).toList(), VoiceGen.morph(a, b, 0.0).copyOf(145).toList())
        assertEquals(b.copyOf(145).toList(), VoiceGen.morph(a, b, 1.0).copyOf(145).toList())
        val mid = VoiceGen.morph(a, b, 0.5)
        val ol = Dx7.paramIndex(1, "OL")
        assertTrue("halfway level", Math.abs(Math.round((a[ol] + b[ol]) / 2.0).toInt() - mid[ol]) <= 1)
    }

    private class Rig {
        var t = 0.0
        val sent = ArrayList<Pair<Double, IntArray>>()
        val engine = Engine({ t }) { b, _ -> sent += t to b }
        fun run(until: Double) { while (t < until) { engine.tick(); t += 1.0 } }
    }

    @Test fun playerPlaysDemoWithPerChannelVoices() {
        val r = Rig()
        val kitPattern = StarterKit.defaultPattern()
        val seq = Sequencer(r.engine, { r.t }, kitPattern)
        val p = MidiPlayer(r.engine, { r.t }) { seq.trackForGm(it) }
        p.load(javaClass.classLoader!!.getResource("demo.mid")!!.readBytes(), "demo.mid")
        val bass = VoiceGen.random("bass", Random(1)); val pad = VoiceGen.random("pad", Random(2)); val lead = VoiceGen.random("pluck", Random(3))
        p.chans[0]!!.voice = bass; p.chans[1]!!.voice = pad; p.chans[2]!!.voice = lead
        assertTrue("ch10 goes to the kit by default", p.chans[9]!!.kit)
        p.tempoPct = 200
        p.start(); r.run(3060.0)
        val song = p.song!!
        val expected = song.notes.count { (it.t) < (3060.0 - 60) * 2 - 10 }
        // replay the output: which voice was loaded at each note-on, and which notes came out
        val dev = IntArray(156) { -1 }
        var ons = 0; var wrong = 0
        val byNote = song.notes.filter { it.ch != 9 }.groupBy { it.note }
        for ((at, m) in r.sent) {
            if (m[0] == 0xF0 && (m[2] and 0xF0) == 0x10) dev[(m[3] shl 7) or m[4]] = m[5]
            else if (m[0] and 0xF0 == 0x90) {
                ons++
                val loaded = dev.copyOf(145)
                val isKit = kitPattern.tracks.any { tr -> tr.note == m[1] && tr.voices.any { it.effective().copyOf(145).contentEquals(loaded) } }
                val chVoices = byNote[m[1]].orEmpty().map { it.ch }.toSet().map { listOf(bass, pad, lead)[it] }
                if (!isKit && chVoices.none { it.copyOf(145).contentEquals(loaded) }) {
                    wrong++
                    val names = mapOf("bass" to bass, "pad" to pad, "lead" to lead) + kitPattern.tracks.associate { it.name to it.voices[0].effective() }
                    val diffs = names.mapValues { (_, v) -> (0 until 145).count { v[it] != loaded[it] } }
                    println("WRONG note ${m[1]} at $at: expected ${chVoices.size} voice(s); closest: ${diffs.entries.sortedBy { it.value }.take(3)}")
                }
            }
        }
        assertTrue("played $ons of about $expected", ons >= expected - 3)
        assertEquals("notes with the wrong channel voice", 0, wrong)
        p.stop(); r.run(3400.0)
        val held = IntArray(128)
        for ((_, m) in r.sent) { if (m[0] and 0xF0 == 0x90) held[m[1]]++; if (m[0] and 0xF0 == 0x80) held[m[1]] = maxOf(0, held[m[1]] - 1) }
        assertTrue("every note released", held.all { it == 0 })
    }

    @Test fun playerStopsAtEndWithoutCuttingTheLastNotes() {
        val r = Rig()
        val p = MidiPlayer(r.engine, { r.t }) { null }
        p.load(javaClass.classLoader!!.getResource("demo.mid")!!.readBytes(), "demo.mid")
        p.chans.values.forEach { it.kit = false; it.voice = null }
        var ended = false
        p.onEnded = { ended = true }
        p.tempoPct = 200
        p.start(); r.run(60 + p.duration / 2 + 400)
        assertTrue(ended); assertFalse(p.playing)
        val ons = r.sent.count { it.second[0] and 0xF0 == 0x90 }
        assertEquals(p.song!!.notes.size, ons)
    }
}
