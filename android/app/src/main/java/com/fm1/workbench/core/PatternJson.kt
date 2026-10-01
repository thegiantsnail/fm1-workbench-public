package com.fm1.workbench.core

import org.json.JSONArray
import org.json.JSONObject

/**
 * Pattern <-> JSON in the web app's format (seq.js), so patterns move between the browser and the phone.
 * Parsing rebuilds everything from known fields with clamped values (like the web's normalize()): a malformed file
 * either loads cleanly or is rejected.
 */
object PatternJson {
    private fun Int.clampTo(a: Int, b: Int) = coerceIn(a, b)
    private fun JSONObject.int(k: String, a: Int, b: Int, d: Int): Int =
        if (has(k) && !isNull(k)) optDouble(k, d.toDouble()).let { if (it.isNaN()) d else Math.round(it).toInt().clampTo(a, b) } else d
    private fun JSONObject.intOrNull(k: String, a: Int, b: Int): Int? =
        if (has(k) && !isNull(k)) optDouble(k).let { if (it.isNaN()) null else Math.round(it).toInt().clampTo(a, b) } else null

    fun toJson(p: Pattern): JSONObject = JSONObject().apply {
        put("bpm", p.bpm); put("swing", p.swing); put("human", p.human); put("glitch", p.glitch); put("rate", p.rate); put("len", p.len)
        put("fx", JSONObject().put("cc", p.fx.cc).put("len", p.fx.len).put("vals", JSONArray(p.fx.vals.map { it ?: JSONObject.NULL })))
        put("tracks", JSONArray(p.tracks.map { t ->
            JSONObject().put("name", t.name).put("role", t.role).put("note", t.note).put("len", t.len).put("gate", t.gate).put("mode", t.mode)
                .put("mute", t.mute).put("solo", t.solo).put("ratPitch", t.ratPitch).put("plock", t.plock)
                .put("voices", JSONArray(t.voices.map { v ->
                    JSONObject().put("name", v.name).put("vced", JSONArray(v.vced.take(155)))
                        .put("macros", JSONObject().apply { v.macros.forEach { (k, x) -> put(k, x ?: JSONObject.NULL) } })
                }))
                .put("steps", JSONArray(t.steps.map { s ->
                    JSONObject().put("on", s.on).put("vel", s.vel).put("prob", s.prob).put("rat", s.rat).put("nudge", s.nudge).put("pitch", s.pitch)
                        .put("pl", s.pl ?: JSONObject.NULL).put("gate", s.gate ?: JSONObject.NULL).put("acc", s.acc).put("slide", s.slide)
                }))
        }))
    }

    private val MACROS = setOf("level", "decay", "release", "tone", "punch", "sweep", "grit", "dyn")

    private fun voice(o: JSONObject?): TrackVoice? {
        o ?: return null
        val arr = o.optJSONArray("vced") ?: return null
        if (arr.length() < 155) return null
        val v = IntArray(155) { arr.optInt(it, -1) }
        if (v.any { it !in 0..127 }) return null
        val macros = mutableMapOf<String, Int?>()
        o.optJSONObject("macros")?.let { m -> m.keys().forEach { k -> if (k in MACROS) macros[k] = if (m.isNull(k)) null else m.optInt(k) } }
        return TrackVoice(o.optString("name", "VOICE").take(16), Dx7.clamp(v), macros)
    }

    private fun step(o: JSONObject?): Step {
        o ?: return Step()
        return Step(o.optBoolean("on"), o.int("vel", 1, 127, 100), o.int("prob", 0, 100, 100), o.int("rat", 1, 4, 1), o.int("nudge", -50, 50, 0),
            o.int("pitch", -24, 24, 0), o.intOrNull("pl", 0, 127), o.intOrNull("gate", 5, 100), o.optBoolean("acc"), o.optBoolean("slide"))
    }

    private fun track(o: JSONObject, i: Int): Track {
        val steps = Array(MAX_STEPS) { Step() }
        o.optJSONArray("steps")?.let { a -> for (s in 0 until minOf(a.length(), MAX_STEPS)) steps[s] = step(a.optJSONObject(s)) }
        val voices = mutableListOf<TrackVoice>()
        o.optJSONArray("voices")?.let { a -> for (v in 0 until a.length()) voice(a.optJSONObject(v))?.let { if (voices.size < 8) voices += it } }
        val plock = o.optInt("plock", -1).let { pl -> if (PLOCK_PARAMS.any { it.second == pl }) pl else -1 }
        return Track(o.optString("name", "Track ${i + 1}").take(16), o.optString("role").takeIf { it in ROLES } ?: "perc",
            o.int("note", 0, 127, 60), o.int("len", 1, MAX_STEPS, 16), o.int("gate", 5, 4000, 120),
            o.optString("mode").takeIf { it in listOf("fixed", "cycle", "random") } ?: "fixed",
            o.optBoolean("mute"), o.optBoolean("solo"), o.int("ratPitch", -12, 12, 0), plock, voices, steps)
    }

    fun fromJson(j: JSONObject): Pattern {
        val tr = j.optJSONArray("tracks") ?: throw IllegalArgumentException("no tracks")
        require(tr.length() > 0) { "no tracks" }
        val fxo = j.optJSONObject("fx") ?: JSONObject()
        val vals = arrayOfNulls<Int>(MAX_STEPS)
        fxo.optJSONArray("vals")?.let { a -> for (i in 0 until minOf(a.length(), MAX_STEPS)) if (!a.isNull(i)) vals[i] = a.optInt(i).clampTo(0, 127) }
        return Pattern(j.int("bpm", 30, 300, 112), j.int("swing", 0, 75, 0), j.int("human", 0, 30, 0), j.int("glitch", 0, 100, 0),
            FxLane(fxo.int("cc", -1, 23, -1), fxo.int("len", 1, MAX_STEPS, 16), vals),
            MutableList(minOf(tr.length(), 16)) { track(tr.optJSONObject(it) ?: JSONObject(), it) },
            j.optString("rate").takeIf { it in RATES } ?: "1/16", j.int("len", 1, MAX_STEPS, 16))
    }

    fun parse(text: String): Pattern = fromJson(JSONObject(text))
    fun write(p: Pattern): String = toJson(p).toString()
}
