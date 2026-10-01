package com.fm1.workbench.core

import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max

/** The Kotlin Software FM-1 renders the same audio as the web one (vectors: android/tools/make_synth_vectors.cjs). */
class SynthParityTest {
    @Test fun rendersLikeTheWebEngine() {
        // Several scenarios play factory voices, so the vectors are kept out of the public release.
        assumeTrue("synth vectors not distributed", javaClass.getResource("/synth_vectors.json") != null)
        val root = JSONObject(javaClass.getResource("/synth_vectors.json")!!.readText())
        val sr = root.getInt("sr"); val len = root.getInt("len")
        val scen = root.getJSONObject("scenarios")
        val report = StringBuilder()
        var ok = true
        for (name in scen.keys()) {
            val s = scen.getJSONObject(name)
            val synth = Fm1Synth(sr).apply { profile = s.getString("profile") }
            val ev = s.getJSONArray("events")
            for (i in 0 until ev.length()) {
                val a = ev.getJSONArray(i)
                synth.queue(IntArray(a.length() - 1) { a.getInt(it + 1) }, a.getLong(0))
            }
            val buf = FloatArray(len); val blk = FloatArray(128)
            var f = 0
            while (f < len) { synth.render(blk, f.toLong()); blk.copyInto(buf, f, 0, minOf(128, len - f)); f += 128 }
            val head = s.getJSONArray("head")
            var maxDiff = 0.0
            for (i in 0 until head.length()) maxDiff = max(maxDiff, abs(buf[i] - head.getDouble(i)))
            val rms = s.getJSONArray("rms")
            var maxDb = 0.0
            for (k in 0 until rms.length()) {
                var e = 0.0; for (j in k * 220 until k * 220 + 220) e += buf[j] * buf[j]
                val db = 10 * log10(e / 220 + 1e-20); val ref = rms.getDouble(k)
                if (ref > -80 || db > -80) maxDb = max(maxDb, abs(db - ref))
            }
            val pass = maxDiff < 1e-3 && maxDb < 0.5
            ok = ok && pass
            report.append("$name: max sample diff %.2e, max frame level diff %.3f dB %s\n".format(maxDiff, maxDb, if (pass) "ok" else "FAIL"))
        }
        println(report)
        assertTrue(report.toString(), ok)
    }
}
