package com.fm1.workbench.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sin

/** The Kotlin speech engine produces the same lint, word classes, contours and MIDI events as app/speech.js
 *  (vectors: android/tools/make_speech_vectors.cjs; data: the web app's app/speech files). */
class SpeechParityTest {
    private val speech by lazy {
        val dir = listOf(File("../../app/speech"), File("../app/speech"), File("app/speech")).first { File(it, "units.json").exists() }
        Speech(JSONObject(File(dir, "units.json").readText()), File(dir, "cmudict.txt").readText(), JSONObject(File(dir, "pos.json").readText()))
    }
    private val vec by lazy { JSONObject(javaClass.getResource("/speech_vectors.json")!!.readText()) }

    private fun opts(o: JSONObject) = Speech.Opts(tone = o.optString("tone", "grammar"), accent = o.optDouble("accent", 3.0), speed = o.optDouble("speed", 1.0),
        pos = o.optBoolean("pos", true), base = o.optInt("base", 45), bright = o.optInt("bright", 54),
        design = o.optString("design", "fixedfm"), vib = o.optInt("vib", 0), select = o.optBoolean("select", true),
        fric = o.optString("fric", "band"), fricDepth = o.optInt("fricDepth", 84), fricSpread = o.optDouble("fricSpread", 1.0),
        burst = o.optBoolean("burst", true), sync = o.optBoolean("sync", true), fine = o.optBoolean("fine", false),
        glide = o.optInt("glide", 0), attack = o.optInt("attack", 0), overlap = o.optDouble("overlap", 8.0), releaseRate = o.optInt("releaseRate", 0),
        diph = o.optString("diph", "off"), softStart = o.optDouble("softStart", 0.0), softRise = o.optInt("softRise", 70),
        softVoiced = o.optBoolean("softVoiced", true), diphAll = o.optBoolean("diphAll", false), diphEdge = o.optDouble("diphEdge", 0.25),
        diphOnset = o.optDouble("diphOnset", 0.35), diphGlide = o.optDouble("diphGlide", 0.8))

    private fun compareEvents(name: String, got: List<Speech.Ev>, want: JSONArray) {
        assertEquals("$name: event count", want.length(), got.size)
        for (i in 0 until want.length()) {
            val w = want.getJSONArray(i); val g = got[i]
            assertTrue("$name event $i time ${g.t} vs ${w.getDouble(0)}", abs(g.t - w.getDouble(0)) < 1e-4)
            val bytes = IntArray(w.length() - 1) { w.getInt(it + 1) }
            assertTrue("$name event $i bytes ${g.b.toList()} vs ${bytes.toList()}", g.b.contentEquals(bytes))
        }
    }

    @Test fun textMatchesTheWebEngine() {
        val cases = vec.getJSONArray("cases")
        for (c in 0 until cases.length()) {
            val k = cases.getJSONObject(c); val text = k.getString("text")
            val o = if (k.isNull("character")) opts(k.getJSONObject("opts")) else speech.character(k.getString("character"), opts(k.getJSONObject("opts")))
            val r = speech.speak(text, o)
            val notes = k.getJSONArray("notes")
            assertEquals("lint for '$text'", List(notes.length()) { notes.getString(it) }, r.notes.map { it.k + "|" + it.m })
            val words = k.getJSONArray("words")
            assertEquals("words for '$text'", List(words.length()) { words.getString(it) },
                r.sentences.flatMap { s -> s.words.map { w -> "${w.text}/${w.pos}/${fmt(w.weight)}/${w.ph.joinToString(" ")}" } })
            assertEquals("frames for '$text'", k.getInt("frames"), r.frames.size)
            val semis = k.getJSONArray("semis")
            for (i in 0 until semis.length()) assertTrue("semi $i for '$text'", abs(r.frames[i].semi - semis.getDouble(i)) < 1e-5)
            compareEvents(text + " / " + k.optString("character"), speech.toEvents(speech.plan(r.frames, r.emax, o), fx = o.fx, sync = o.sync), k.getJSONArray("events"))
        }
    }

    @Test fun recordingAnalysisMatchesTheWebEngine() {
        val rec = vec.getJSONObject("recording"); val sr = rec.getInt("sr")
        val frames = speech.analyse(signal(sr), sr)
        val want = rec.getJSONArray("frames")
        assertEquals(want.length(), frames.size)
        for (i in 0 until want.length()) {
            val w = want.getJSONArray(i); val f = frames[i]
            val got = doubleArrayOf(f.e, f.v.toDouble(), f.f0, f.F[0], f.F[1], f.F[2], f.L[0], f.L[1], f.L[2], f.cent)
            for (j in got.indices) assertTrue("analysis frame $i field $j: ${got[j]} vs ${w.getDouble(j)}", abs(got[j] - w.getDouble(j)) < 1e-3)
        }
        val r = speech.framesFromRecording(frames)
        compareEvents("recording", speech.toEvents(speech.plan(r.frames, r.emax, Speech.Opts())), rec.getJSONArray("events"))
    }

    /** JS number formatting of the accent weight (1 -> "1", 0.7 -> "0.7", 0 -> "0"). */
    private fun fmt(x: Double) = if (x == Math.floor(x)) x.toLong().toString() else x.toString()

    private fun signal(sr: Int): FloatArray {                  // same as make_speech_vectors.cjs
        val n = (sr * 1.2).toInt(); val x = FloatArray(n)
        var seed = 12345L
        fun rnd(): Double { seed = (seed * 16807) % 2147483647; return seed / 2147483647.0 * 2 - 1 }
        for (i in 0 until n) {
            val t = i.toDouble() / sr
            if (t < 0.5) { var s = 0.0; for (h in 1..30) { val f = 120 * h * (1 + 0.1 * t); val a = exp(-((f - 700) * (f - 700)) / 2e5) + 0.5 * exp(-((f - 1200) * (f - 1200)) / 3e5) + 0.2 * exp(-((f - 2500) * (f - 2500)) / 5e5); s += a * sin(2 * PI * f * t) }; x[i] = (0.3 * s).toFloat() }
            else if (t < 0.7) x[i] = (0.1 * rnd()).toFloat()
            else if (t < 1.0) { var s = 0.0; for (h in 1..20) { val f = 150.0 * h; val a = exp(-((f - 350) * (f - 350)) / 1e5) + exp(-((f - 2200) * (f - 2200)) / 4e5); s += a * sin(2 * PI * f * t) }; x[i] = (0.2 * s).toFloat() }
        }
        return x
    }
}
