package com.fm1.workbench.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** FM-1+VA firmware support: voice switches also send CC 76/78 (LFO speed/delay), only when the LFO settings change. */
class FirmwareTest {
    private fun run(firmware: String): List<IntArray> {
        val sent = ArrayList<IntArray>()
        val e = Engine({ 0.0 }) { b, _ -> sent += b }.apply { this.firmware = firmware }
        val v = Dx7.initVoice(); v[Dx7.paramIndex(null, "LFS")] = 62; v[Dx7.paramIndex(null, "LFD")] = 33
        e.setVoice(v)
        e.setVoice(v.copyOf().also { it[Dx7.paramIndex(1, "OL")] = 80 })            // no LFO change: no new CCs
        return sent
    }

    @Test fun vaSendsLfoControllers() {
        val cc = run("va").filter { it[0] and 0xF0 == 0xB0 }
        assertEquals(listOf(listOf(0xB0, 76, 80), listOf(0xB0, 78, 42)), cc.map { it.toList() })   // 62*127/99, 33*127/99
    }

    @Test fun stockSendsNoControllers() {
        assertTrue(run("stock").none { it[0] and 0xF0 == 0xB0 })
    }

    @Test fun softSynthVaProfileFollowsCc76AndCc7() {
        val s = Fm1Synth(22050).apply { profile = "va" }
        assertEquals(5.0, s.lfoHz(31), 1e-9)
        assertEquals(50.7, s.lfoHz(99), 1e-9)
        // CC 7 = 0 silences the output
        s.queue(intArrayOf(0xB0, 7, 0), 0); s.queue(intArrayOf(0x90, 69, 100), 1)
        val out = FloatArray(2048); s.render(out, 0)
        assertTrue(out.all { it == 0f })
    }
}
