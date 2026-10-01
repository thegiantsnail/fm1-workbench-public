package com.fm1.workbench.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The FM-1 matches note-offs using the transpose in effect at note-off time (a TRNP change while a note is held
 * leaves it hanging - measured). The engine keeps the device's TRNP at 24 and shifts note numbers instead.
 */
class TransposeTest {
    @Test fun voiceTransposeShiftsNotesAndNoteOffsMatch() {
        var t = 0.0
        val sent = ArrayList<IntArray>()
        val e = Engine({ t }) { b, _ -> sent += b }
        val down = Dx7.initVoice("DOWN").also { it[144] = 12 }        // -12 semitones, like 11k library voices
        val plain = Dx7.initVoice("PLAIN")                             // TRNP 24
        e.hit(down, 60, 100, 5.0, 300.0)                               // held across the next switch
        e.hit(plain, 64, 100, 100.0, 50.0)
        while (t < 800) { e.tick(); t += 1.0 }
        val trnpWrites = sent.filter { it[0] == 0xF0 && (it[3] shl 7 or it[4]) == 144 }.map { it[5] }
        assertTrue("device TRNP only ever 24: $trnpWrites", trnpWrites.all { it == 24 })
        val ons = sent.filter { it[0] and 0xF0 == 0x90 }.map { it[1] }
        val offs = sent.filter { it[0] and 0xF0 == 0x80 }.map { it[1] }
        assertEquals(listOf(48, 64), ons)                              // 60 transposed down an octave; 64 untouched
        assertTrue("note-off for the transposed note uses its key: $offs", 48 in offs && 64 in offs)
    }

    @Test fun editorTransposeParamIsANoteShift() {
        var t = 0.0
        val sent = ArrayList<IntArray>()
        val e = Engine({ t }) { b, _ -> sent += b }
        e.setVoice(Dx7.initVoice())
        val id = e.note(60, 100, 0.0, null)
        e.setParam(144, 36)                                            // +12 while the note is held
        e.noteOff(60, null, id)                                        // must still release key 60
        e.note(60, 100, 0.0, null)
        assertEquals(listOf(60, 72), sent.filter { it[0] and 0xF0 == 0x90 }.map { it[1] })
        assertEquals(listOf(60), sent.filter { it[0] and 0xF0 == 0x80 }.map { it[1] })
        assertTrue(sent.none { it[0] == 0xF0 && it[4] == 144 - 128 && it[3] == 1 && it[5] != 24 })
    }
}
