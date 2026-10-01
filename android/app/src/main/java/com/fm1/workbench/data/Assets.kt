package com.fm1.workbench.data

import android.content.Context
import android.util.Base64
import com.fm1.workbench.core.Dx7
import com.fm1.workbench.core.TrackVoice
import org.json.JSONArray
import org.json.JSONObject

/** One library voice (from the bundled index.json - the same deduplicated sysexFinal library as the web app). */
class LibVoice(val id: Int, val name: String, val bank: String, val tags: List<String>, private val b64: String?,
               private val direct: IntArray? = null) {
    val lname = (name + " " + bank).lowercase()
    val vced: IntArray by lazy {
        direct ?: Base64.decode(b64, Base64.DEFAULT).let { raw -> Dx7.fromVmem(IntArray(128) { raw[it].toInt() and 0xFF }) }
    }
    val mine get() = direct != null
}

class KitTrack(val name: String, val role: String, val note: Int, val gate: Int, val voice: TrackVoice)
class Kit(val name: String, val source: String, val tracks: List<KitTrack>)

object Assets {
    /** Speech data shared with the web app (app/speech): diphone units, compact CMUdict, part-of-speech tagger. */
    fun speech(ctx: Context): com.fm1.workbench.core.Speech {
        fun text(n: String) = ctx.assets.open(n).bufferedReader().use { it.readText() }
        return com.fm1.workbench.core.Speech(JSONObject(text("units.json")), text("cmudict.txt"), JSONObject(text("pos.json")))
    }

    val DRUM_TAGS = setOf("kick", "snare", "hat", "tom", "perc")

    fun library(ctx: Context): List<LibVoice> {
        val j = JSONObject(ctx.assets.open("index.json").bufferedReader().use { it.readText() })
        val banks = j.getJSONArray("banks")
        val vs = j.getJSONArray("voices")
        return List(vs.length()) { i ->
            val r = vs.getJSONArray(i)
            val tags = r.getJSONArray(4).let { t -> List(t.length()) { t.getString(it) } }
            LibVoice(i, r.getString(0).trim(), banks.getString(r.getInt(1)), tags, r.getString(3))
        }
    }

    /** Factory kits measured on the FM-1 (build_kits.py): voices with a balancing Level macro and gates from decay. */
    fun kits(ctx: Context): List<Kit> {
        val j = JSONObject(ctx.assets.open("kits.json").bufferedReader().use { it.readText() })
        val ks = j.getJSONArray("kits")
        return List(ks.length()) { i ->
            val k = ks.getJSONObject(i)
            val tr: JSONArray = k.getJSONArray("tracks")
            Kit(k.getString("name"), k.optString("source"), List(tr.length()) { t ->
                val o = tr.getJSONObject(t)
                val v = o.getJSONObject("voice")
                val raw = Base64.decode(v.getString("b64"), Base64.DEFAULT)
                val macros = mutableMapOf<String, Int?>()
                o.optJSONObject("macros")?.let { m -> m.keys().forEach { key -> macros[key] = if (m.isNull(key)) null else m.getInt(key) } }
                val meas = v.optJSONObject("measured")
                val decay = meas?.optDouble("decay_ms", Double.NaN) ?: Double.NaN
                val gate = if (decay.isNaN()) o.getInt("gate") else (decay + 20).toInt().coerceIn(40, 800)
                KitTrack(o.getString("name"), o.getString("role"), o.getInt("note"), gate,
                    TrackVoice(v.getString("name"), Dx7.fromVmem(IntArray(128) { raw[it].toInt() and 0xFF }), macros,
                        meas?.let { mapOf("level_db" to it.optDouble("level_db"), "decay_ms" to it.optDouble("decay_ms"), "note" to it.optDouble("note")) }))
            })
        }
    }
}
