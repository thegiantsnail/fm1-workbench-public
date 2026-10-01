package com.fm1.workbench.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** SpeechPlayer through the Engine on simulated time, with the Software FM-1 as the output. */
class SpeechPlayerTest {
    private val speech by lazy {
        val dir = listOf(File("../../app/speech"), File("../app/speech")).first { File(it, "units.json").exists() }
        Speech(JSONObject(File(dir, "units.json").readText()), File(dir, "cmudict.txt").readText(), JSONObject(File(dir, "pos.json").readText()))
    }

    private class Rig {
        var now = 1000.0
        val sent = ArrayList<Pair<Double, IntArray>>()
        val synth = Fm1Synth(22050)
        val engine = Engine({ now }) { b, at ->
            val t = if (at > now) at else now
            sent += t to b
            synth.queue(b, Math.round(t * 22.05))            // what SoftFm1Output does: the message sounds at its time
        }.apply { sendAhead = 20.0 }
        var frame = (now * 22.05).toLong()
        /** Advance simulated time, rendering the synth alongside. */
        fun run(ms: Double) {
            val end = now + ms
            val blk = FloatArray(22)
            while (now < end) { engine.tick(); now += 1.0; while (frame < (now * 22.05).toLong()) { synth.render(blk, frame); frame += blk.size } }
        }
    }

    @Test fun playsEveryEventAndEnds() {
        val r = speech.speak("Hello world. Can you hear me?")
        val ev = speech.toEvents(speech.plan(r.frames, r.emax, Speech.Opts()))
        val rig = Rig()
        val player = SpeechPlayer(rig.engine) { rig.now }
        player.play(ev)
        assertTrue(player.playing)
        rig.run(60 + ev.last().t + 600)
        Thread.sleep(400)                                   // the end-of-playback timer runs on real time
        assertEquals("every event sent", ev.size, rig.sent.size)
        val ons = rig.sent.count { it.second[0] and 0xF0 == 0x90 }; val offs = rig.sent.count { it.second[0] and 0xF0 == 0x80 }
        assertEquals("note-offs match note-ons", ons, offs)
        rig.run(300.0)
        assertEquals("no voice left sounding", 0, rig.synth.activeVoices)
        assertFalse("finished", player.playing)
    }

    /** Stopping mid-phrase: the catch-up note-offs must not overtake note-ons already handed out with timestamps. */
    @Test fun stopLeavesNoHangingNotes() {
        val r = speech.speak("The quick brown fox jumps over the lazy dog.")
        val ev = speech.toEvents(speech.plan(r.frames, r.emax, Speech.Opts()))
        val rig = Rig()
        val player = SpeechPlayer(rig.engine) { rig.now }
        repeat(5) { k ->
            player.play(ev)
            rig.run(300.0 + 37 * k)                           // stop at different points of a note
            player.stop()
            rig.run(400.0)
            assertEquals("stop #$k: no voice left sounding", 0, rig.synth.activeVoices)
        }
    }
}
