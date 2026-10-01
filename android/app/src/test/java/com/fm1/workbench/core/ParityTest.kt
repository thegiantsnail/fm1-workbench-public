package com.fm1.workbench.core

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test

/** Byte-for-byte parity with the web app (vectors generated from the web app's JS by tools/make_vectors.cjs). */
class ParityTest {
    private fun res(name: String): String = javaClass.classLoader!!.getResource(name)!!.readText()
    private fun bytes(name: String): ByteArray = javaClass.classLoader!!.getResource(name)!!.readBytes()
    private fun hex(s: String) = IntArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16) }
    private fun lines(name: String) = res(name).lines().filter { it.isNotBlank() }
    /** Vectors made from a factory bank are kept out of the public release; their tests skip without them. */
    private fun has(name: String) = javaClass.classLoader!!.getResource(name) != null

    @Test fun codecMatchesJs() {
        val cases = lines("codec_vectors.txt")
        assertEquals(300, cases.size)
        for (line in cases) {
            val (vced, vmem, sysex) = line.split("|").map(::hex)
            assertArrayEquals("toVmem", vmem, Dx7.toVmem(vced))
            assertArrayEquals("fromVmem", vced, Dx7.fromVmem(vmem))
            assertArrayEquals("vcedSysex", sysex, Dx7.vcedSysex(vced))
        }
    }

    @Test fun rom1aParsesLikeJs() {
        assumeTrue("factory test bank not distributed", has("ROM1A.syx") && has("rom1a_vced.txt") && has("bank_sysex.txt"))
        val want = lines("rom1a_vced.txt").map(::hex)
        val got = Dx7.parseSyx(bytes("ROM1A.syx"))
        assertEquals(32, got.size)
        want.forEachIndexed { i, v -> assertArrayEquals("voice $i", v, got[i].vced) }
        assertEquals("BRASS   1 ", Dx7.name(got[0].vced))
        assertArrayEquals("bank sysex", hex(lines("bank_sysex.txt")[0]), Dx7.vmemSysex(got.map { it.vced }))
    }

    @Test fun miscMatchesJs() {
        for (l in lines("misc_vectors.txt")) {
            val parts = l.split(" ")
            when (parts[0]) {
                "param" -> assertArrayEquals(l, hex(parts[3]), Dx7.paramSysex(parts[1].toInt(), parts[2].toInt()))
                "carriers" -> assertEquals(parts[1], Dx7.ALGS.joinToString(";") { a -> a.carriers.joinToString(",") })
                "edges" -> assertEquals(parts[1], Dx7.ALGS.joinToString(";") { a -> a.edges.joinToString(",") { "${it.first}>${it.second}" } + "|" + a.fb })
                "opmask" -> assertEquals(parts[1].toInt(), Dx7.opMaskValue(booleanArrayOf(true, false, true, false, true, false)))
            }
        }
    }

    @Test fun drumMacrosMatchJs() {
        assumeTrue("vectors made from the factory test bank are not distributed", has("macro_vectors.txt"))
        for (l in lines("macro_vectors.txt")) {
            val (mj, base, want) = l.split("|")
            val o = JSONObject(mj)
            val m = o.keys().asSequence().associateWith { k -> if (o.isNull(k)) null else o.getInt(k) }.toMutableMap()
            assertArrayEquals(mj, hex(want), DrumMacros.apply(hex(base), m))
        }
    }

    @Test fun smfMatchesJs() {
        val want = lines("smf_demo.txt")
        val song = Smf.parse(bytes("demo.mid"))
        val s = want[0].split(" ")
        assertEquals(s[1].toInt(), song.notes.size)
        assertEquals(s[2].toInt(), song.ctrl.size)
        assertEquals(s[3].toDouble(), song.duration, 0.001)
        assertEquals(s[4].toDouble(), song.bpm, 0.001)
        want.drop(1).forEachIndexed { i, l ->
            val p = l.split(" ")
            val n = song.notes[i]
            assertEquals("t $i", p[1].toDouble(), n.t, 0.001); assertEquals("dur $i", p[2].toDouble(), n.dur!!, 0.001)
            assertEquals(p[3].toInt(), n.ch); assertEquals(p[4].toInt(), n.note); assertEquals(p[5].toInt(), n.vel)
        }
    }

    @Test fun hugeMidiDoesNotOverflow() {
        val n = 200_000
        val ev = java.io.ByteArrayOutputStream()
        repeat(n) { ev.write(byteArrayOf(0, 0x90.toByte(), 60, 90, 0x10, 0x80.toByte(), 60, 0)) }
        ev.write(byteArrayOf(0, 0xFF.toByte(), 0x2F, 0))
        val trk = ev.toByteArray()
        val f = java.nio.ByteBuffer.allocate(14 + 8 + trk.size)
            .put("MThd".toByteArray()).putInt(6).putShort(0).putShort(1).putShort(96)
            .put("MTrk".toByteArray()).putInt(trk.size).put(trk).array()
        assertEquals(n, Smf.parse(f).notes.size)
    }
}
