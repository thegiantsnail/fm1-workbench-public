package com.fm1.workbench.core

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.*

/**
 * Speech for the FM-1 (port of app/speech.js): typed text or a recording -> frame tracks (energy, voicing, formants) ->
 * FM-1 notes, one DX7 voice per 20 ms frame (algorithm 22 formant voices, feedback-noise voices for hiss) -> timed MIDI
 * events that play the same on the hardware and on the Software FM-1.
 *
 * Text is normalised and linted (numbers, money, ordinals, years, abbreviations, acronyms, unknown words, sentence type),
 * looked up in CMUdict, tagged with a Brown-corpus HMM (word class -> accents, REcord/reCORD stress) and stitched from
 * diphone units analysed from Windows TTS speech. Data: app/speech/{units.json, cmudict.txt, pos.json} (bundled assets).
 * Measured facts (FINDINGS.md): param changes apply to the NEXT note; TRNP stays 24 (it hangs held notes); consecutive
 * notes alternate between keys an octave apart (upper key with halved ratios) so a frame never retriggers a releasing key.
 * Parity with the JS is enforced by SpeechParityTest (vectors: android/tools/make_speech_vectors.cjs).
 */
class Speech(unitsJson: JSONObject, dictText: String, private val pos: JSONObject?) {
    companion object {
        const val HOP = 10                                  // analysis frame (ms)
        private val ONES = listOf("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
            "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
        private val TENS = listOf("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
        private val ABBR = mapOf("mr." to "mister", "mrs." to "missus", "ms." to "miz", "dr." to "doctor", "st." to "street", "vs." to "versus",
            "etc." to "et cetera", "e.g." to "for example", "i.e." to "that is", "jr." to "junior", "sr." to "senior", "prof." to "professor",
            "mt." to "mount", "approx." to "approximately")
        private val SYMBOLS = mapOf('&' to "and", '%' to "percent", '+' to "plus", '=' to "equals", '@' to "at", '#' to "number", '/' to "slash")
        private val LETTERS = mapOf('a' to "ey1", 'b' to "b iy1", 'c' to "s iy1", 'd' to "d iy1", 'e' to "iy1", 'f' to "eh1 f", 'g' to "jh iy1",
            'h' to "ey1 ch", 'i' to "ay1", 'j' to "jh ey1", 'k' to "k ey1", 'l' to "eh1 l", 'm' to "eh1 m", 'n' to "eh1 n", 'o' to "ow1",
            'p' to "p iy1", 'q' to "k y uw1", 'r' to "aa1 r", 's' to "eh1 s", 't' to "t iy1", 'u' to "y uw1", 'v' to "v iy1",
            'w' to "d ah1 b ax0 l y uw0", 'x' to "eh1 k s", 'y' to "w ay1", 'z' to "z iy1")
        private val FUNC = ("a an the and or but nor if of to in on at by for with from as is am are was were be been being do does did " +
            "have has had i you he she it we they me him her us them my your his its our their this that these those not no so than then " +
            "there here will would can could shall should may might must just very what who whom whose when where why how which up out " +
            "into about oh").split(' ').toSet()
        private val WH = "what who whom whose when where why how which".split(' ').toSet()
        private val AUX = "is am are was were do does did have has had can could will would shall should may might must".split(' ').toSet()
        private val NEG = setOf("not", "n't", "never", "no", "nothing", "nobody", "none", "nowhere")
        private val ACCENT = mapOf("NOUN" to 1.0, "NUM" to 1.0, "ADJ" to 0.9, "ADV" to 0.8, "VERB" to 0.7)
        private val LTS: List<Pair<Regex, String>> = listOf(
            "^tion" to "sh ax n", "^sion" to "zh ax n", "^ough" to "ao", "^augh" to "ao", "^eigh" to "ey", "^igh" to "ay", "^tch" to "ch",
            "^dge" to "jh", "^sh" to "sh", "^ch" to "ch", "^th" to "th", "^ph" to "f", "^wh" to "w", "^ck" to "k", "^ng" to "ng", "^qu" to "k w",
            "^ee" to "iy", "^ea" to "iy", "^oo" to "uw", "^ou" to "aw", "^ow" to "ow", "^oa" to "ow", "^ai" to "ey", "^ay" to "ey", "^oi" to "oy",
            "^oy" to "oy", "^au" to "ao", "^aw" to "ao", "^ew" to "uw", "^ie" to "iy", "^ei" to "ey", "^ar" to "aa r", "^er" to "er", "^ir" to "er",
            "^ur" to "er", "^or" to "ao r", "^([bdfgklmnprstz])\\1" to "$1", "^x" to "k s", "^c(?=[eiy])" to "s", "^c" to "k", "^g(?=[eiy])" to "jh",
            "^y(?=[aeiou])" to "y", "^y$" to "iy", "^y" to "ih", "^j" to "jh", "^q" to "k", "^h" to "hh",
            "^a(?=[^aeiou]e$)" to "ey", "^i(?=[^aeiou]e$)" to "ay", "^o(?=[^aeiou]e$)" to "ow", "^u(?=[^aeiou]e$)" to "uw", "^e$" to "",
            "^a" to "ae", "^e" to "eh", "^i" to "ih", "^o" to "aa", "^u" to "ah", "^([bdfgklmnprstvwz])" to "$1", "^." to "").map { Regex(it.first) to it.second }
        private val VOWEL = Regex("^(aa|ae|ah|ao|aw|ax|ay|eh|er|ey|ih|iy|ow|oy|uh|uw)")

        fun under1000(n0: Long): List<String> {
            var n = n0; val w = ArrayList<String>()
            if (n >= 100) { w += ONES[(n / 100).toInt()]; w += "hundred"; n %= 100 }
            if (n >= 20) { w += TENS[(n / 10).toInt()]; n %= 10; if (n != 0L) w += ONES[n.toInt()] }
            else if (n != 0L || w.isEmpty()) w += ONES[n.toInt()]
            return w
        }
        fun numWords(n0: Long): List<String> {
            if (n0 == 0L) return listOf("zero")
            var n = n0; val w = ArrayList<String>()
            for ((v, name) in listOf(1_000_000_000_000L to "trillion", 1_000_000_000L to "billion", 1_000_000L to "million", 1000L to "thousand")) {
                if (n >= v) { w += under1000(n / v); w += name; n %= v }
            }
            if (n != 0L) w += under1000(n)
            return w
        }
        private fun yearWords(n: Long): List<String> {
            if (n in 2000..2009) return numWords(n)
            val hi = n / 100; val lo = n % 100
            return under1000(hi) + (if (lo == 0L) listOf("hundred") else if (lo < 10) listOf("oh", ONES[lo.toInt()]) else under1000(lo))
        }
        private fun ordinal(words: List<String>): List<String> {
            val w = words.toMutableList(); val last = w.last()
            val irr = mapOf("one" to "first", "two" to "second", "three" to "third", "five" to "fifth", "eight" to "eighth", "nine" to "ninth", "twelve" to "twelfth")
            w[w.size - 1] = irr[last] ?: if (last.endsWith("y")) last.dropLast(1) + "ieth" else last + "th"
            return w
        }
        fun guess(w: String): List<String> {
            val out = ArrayList<String>()
            var i = 0
            while (i < w.length) {
                val rest = w.substring(i)
                for ((re, ph) in LTS) {
                    val m = re.find(rest) ?: continue
                    val s = ph.replace("$1", m.groupValues.getOrNull(1) ?: "")
                    if (s.isNotEmpty()) out += s.split(' ')
                    i += max(1, m.value.length)
                    break
                }
            }
            var stressed = false
            return out.map { p ->
                if (VOWEL.containsMatchIn(p) && p != "ax") p + (if (stressed) "0" else { stressed = true; "1" })
                else if (p == "ax") "ah0" else p
            }
        }
        private fun midiHz(n: Int) = 440.0 * 2.0.pow((n - 69) / 12.0)
        private fun midiHz(n: Double) = 440.0 * 2.0.pow((n - 69) / 12.0)
        private fun jsRound(x: Double) = Math.round(x).toInt()     // JS Math.round: half up
    }

    // ---------------------------------------------------------------- data
    private val units = unitsJson.getJSONObject("units")
    private val phones = unitsJson.getJSONObject("phones")
    // word -> its phones as the file's one-character codes; decoded only for the words actually looked up
    private val dictCodes = HashMap<String, String>(180_000)
    private val syms: List<String>
    init {
        var start = dictText.indexOf('\n')
        syms = dictText.substring(0, start).trimEnd('\r').split(',')
        var prev = ""
        while (start >= 0 && start < dictText.length) {
            val end = dictText.indexOf('\n', start + 1).let { if (it < 0) dictText.length else it }
            val ln = dictText.substring(start + 1, end).trimEnd('\r')
            start = end
            val sp = ln.indexOf(' ')
            if (sp < 0) continue
            val w = prev.substring(0, ln[0] - '0') + ln.substring(1, sp)
            dictCodes[w] = ln.substring(sp + 1)
            prev = w
        }
    }
    private fun dict(w: String): List<String>? = dictCodes[w]?.let { c -> List(c.length) { syms[c[it].code - 48] } }

    class Word(val text: String, var ph: List<String>, val src: String, val func: Boolean) {
        var brk = false; var pos: String? = null; var weight = 0.0
    }
    class Sentence { val words = ArrayList<Word>(); var end = "."; var type = "stmt" }
    class Note(val k: String, val m: String)
    class Lint(val sentences: List<Sentence>, val notes: List<Note>)
    class Ph(val p: String, val semi: Double = 0.0, val semi2: Double? = null, val stretch: Double = 1.0, val pause: Double = 0.0, val w: String? = null)
    class Frame(val e: Double, val v: Int, val F: DoubleArray, val L: DoubleArray, val cent: Double, val f0: Double = 0.0, val i: Int = 0,
                var p: String = "", var target: Double = 0.0, var semi: Double = 0.0, val hf: Double = -30.0) {
        fun copy(F: DoubleArray = this.F, L: DoubleArray = this.L) = Frame(e, v, F, L, cent, f0, i, p, target, semi, hf)
    }
    class Result(val sentences: List<Sentence>, val notes: List<Note>, val frames: List<Frame>, val emax: Double)
    class PlanNote(val v: IntArray, val key: Int, val start: Double, val dur: Double, val pitch: Double? = null)
    class Ev(val t: Double, val b: IntArray)
    data class Opts(val tone: String = "grammar", val accent: Double = 3.0, val speed: Double = 1.0, val pos: Boolean = true,
                    val base: Int = 45, val bright: Int = 54, val slope: Double = 0.8, val hop: Int = 20, val silenceDb: Double = -42.0,
                    val release: Int = 85, val design: String = "fixedfm", val vib: Int = 0, val select: Boolean = true,
                    val fric: String = "band", val fricDepth: Int = 84, val fricUpper: Double = -8.0, val fricSpread: Double = 1.0,
                    val hfVoiced: Double = -16.0, val hfAspirate: Double = -20.0, val burst: Boolean = true, val burstDecay: Int = 72,
                    val hfVowel: Double = -20.0, val vowelDb: Double = -18.0,
                    val style: String = "", val melody: List<Int>? = null, val vowelStretch: Double = 1.0,
                    val formant: Double = 1.0, val whisper: Boolean = false, val stutter: Double = 0.0, val inharm: Int = 0,
                    val fx: List<IntArray>? = null,
                    val glide: Int = 0, val fine: Boolean = false, val attack: Int = 0, val overlap: Double = 8.0, val releaseRate: Int = 0,
                    val sync: Boolean = true, val smooth: String = "soft",
                    val softStart: Double = 0.0, val softRise: Int = 70, val softVoiced: Boolean = true,
                    val diph: String = "off", val diphAll: Boolean = false, val diphFall: Double = 1.0, val diphRise: Double = 0.55,
                    val diphEdge: Double = 0.25, val diphOnset: Double = 0.35, val diphGlide: Double = 0.8)

    fun lookup(w: String): Pair<List<String>, String> {
        dict(w)?.let { return it to "dict" }
        val strip = listOf("ing" to listOf("ih0", "ng"), "'s" to listOf("z"), "s" to listOf("z"), "es" to listOf("ih0", "z"), "ed" to listOf("d"),
            "ly" to listOf("l", "iy0"), "er" to listOf("er0"), "ers" to listOf("er0", "z"), "ness" to listOf("n", "ah0", "s"),
            "ment" to listOf("m", "ah0", "n", "t"), "ful" to listOf("f", "ah0", "l"))
        for ((suf, ph) in strip) {
            if (w.length > suf.length + 2 && w.endsWith(suf)) {
                val stem = w.dropLast(suf.length)
                for (s in listOf(stem, stem + "e")) dict(s)?.let { return (it + ph) to "suffix" }
            }
        }
        for (k in 3..w.length - 3) {
            val a = dict(w.substring(0, k)); val b = dict(w.substring(k))
            if (a != null && b != null) return (a + b) to "compound"
        }
        return guess(w) to "guess"
    }

    // ---------------------------------------------------------------- part of speech (HMM from the Brown corpus)
    private fun guessTags(w: String): List<String> = when {
        Regex("ly$").containsMatchIn(w) -> listOf("ADV")
        Regex("(ing|ed|ize|ise|ate|en)$").containsMatchIn(w) -> listOf("VERB", "ADJ", "NOUN")
        Regex("(ous|ful|ive|able|ible|al|ic|ish|less|ary|ent|ant)$").containsMatchIn(w) -> listOf("ADJ")
        else -> listOf("NOUN")
    }
    fun tag(words: List<String>): List<String?> {
        val p = pos ?: return words.map { null }
        if (words.isEmpty()) return emptyList()
        val tags = p.getJSONArray("tags").let { a -> List(a.length()) { a.getString(it) } }
        val codes = p.getJSONArray("codes").let { a -> List(a.length()) { a.getString(it) } }
        val prior = p.getJSONArray("prior"); val start = p.getJSONArray("start"); val trans = p.getJSONArray("trans")
        val lex = p.getJSONObject("lex"); val hetero = p.getJSONObject("hetero")
        val T = tags.size
        val tr = Array(T) { a -> DoubleArray(T) { b -> trans.getJSONArray(a).getDouble(b) } }
        fun emit(w: String): DoubleArray {
            val lw = w.lowercase()
            if (!lex.has(lw)) { val g = guessTags(lw); return DoubleArray(T) { if (tags[it] in g) 0.0 else -300.0 } }
            val c = lex.getString(lw)
            val d = HashMap<String, Int>()
            var i = 0
            while (i < c.length) { d[c[i].toString()] = c[i + 1] - '0'; i += 2 }
            if (hetero.has(lw)) { if ((d["N"] ?: 0) == 0) d["N"] = 1; if ((d["V"] ?: 0) == 0) d["V"] = 1 }
            return DoubleArray(T) { t -> val k = d[codes[t]] ?: 0; if (k != 0) 100 * ln(k / 10.0) - prior.getDouble(t) else -900.0 }
        }
        var V = emit(words[0]).let { e -> DoubleArray(T) { start.getDouble(it) + e[it] } }
        val B = ArrayList<IntArray>()
        for (i in 1 until words.size) {
            val e = emit(words[i]); val row = DoubleArray(T); val back = IntArray(T)
            for (t in 0 until T) {
                var k = 0
                for (u in 1 until T) if (V[u] + tr[u][t] > V[k] + tr[k][t]) k = u
                row[t] = V[k] + tr[k][t] + e[t]; back[t] = k
            }
            V = row; B += back
        }
        var t = 0
        for (u in 1 until T) if (V[u] > V[t]) t = u
        val path = arrayListOf(t)
        for (i in B.indices.reversed()) { t = B[i][t]; path += t }
        return path.reversed().map { tags[it] }
    }

    // ---------------------------------------------------------------- text normalisation + lint
    fun lint(text: String): Lint {
        val notes = ArrayList<Note>()
        var t = " " + text.replace(Regex("[‘’]"), "'").replace(Regex("[“”]"), "\"") + " "
        t = Regex("\\$(\\d[\\d,]*)(?:\\.(\\d\\d))?").replace(t) { m ->
            notes += Note("number", "\"${m.value}\" read as money")
            val d = m.groupValues[1]; val c = m.groupValues[2]
            " $d dollars${if (c.isNotEmpty() && c.toInt() != 0) " and " + c.toInt() + " cents" else ""} "
        }
        t = Regex("(\\d+)(st|nd|rd|th)\\b", RegexOption.IGNORE_CASE).replace(t) { m ->
            notes += Note("number", "\"${m.value}\" read as an ordinal"); " " + ordinal(numWords(m.groupValues[1].toLong())).joinToString(" ") + " "
        }
        t = Regex("\\b[a-z]{1,5}\\.(?:[a-z]\\.)?", RegexOption.IGNORE_CASE).replace(t) { m ->
            val a = ABBR[m.value.lowercase()] ?: return@replace m.value
            notes += Note("abbr", "\"${m.value}\" → \"$a\""); a
        }
        t = Regex("[&%+=@#/]").replace(t) { m -> " ${SYMBOLS[m.value[0]]} " }
        t = Regex("(\\d)-(?=\\d)").replace(t, "$1 to ")
        t = Regex("([A-Za-z])-?(\\d)").replace(t, "$1 $2").let { Regex("(\\d)([A-Za-z])").replace(it, "$1 $2") }
        t = Regex("(\\d[\\d,]*)(\\.\\d+)?").replace(t) { m ->
            val d = m.groupValues[1]; val frac = m.groupValues[2]
            val n = d.replace(",", "").toLongOrNull() ?: 0L
            val w: List<String>
            if (frac.isEmpty() && Regex("^\\d{4}$").matches(d) && n in 1100..2099) {
                w = yearWords(n); notes += Note("number", "\"${m.value}\" read as a year (${w.joinToString(" ")})")
            } else {
                w = numWords(n) + (if (frac.isNotEmpty()) listOf("point") + frac.drop(1).map { ONES[it - '0'] } else emptyList())
                if (m.value.length > 1 || frac.isNotEmpty()) notes += Note("number", "\"${m.value}\" → ${w.joinToString(" ")}")
            }
            " " + w.joinToString(" ") + " "
        }
        if (Regex("[!?.]{2,}").containsMatchIn(t)) notes += Note("style", "Repeated punctuation reads the same as a single mark.")
        val sentences = ArrayList<Sentence>()
        var cur = Sentence()
        for (m in Regex("[A-Za-z']+|[.,!?;:]").findAll(t)) {
            val tok = m.value
            if (tok.length == 1 && tok[0] in ".!?") { if (cur.words.isNotEmpty()) { cur.end = tok; sentences += cur }; cur = Sentence(); continue }
            if (tok.length == 1 && tok[0] in ",;:") { cur.words.lastOrNull()?.brk = true; continue }
            val w = tok.lowercase().replace(Regex("^'+|'+$"), "")
            if (w.isEmpty()) continue
            val caps = tok.length >= 2 && tok == tok.uppercase() && tok.any { it in 'A'..'Z' }
            if (caps && (!dictCodes.containsKey(w) || !Regex("[aeiouy]").containsMatchIn(w) || w.length <= 3) && w.length <= 6 && w !in FUNC) {
                notes += Note("spell", "\"$tok\" spelled out as letters")
                for (c in w) LETTERS[c]?.let { cur.words += Word(c.uppercase(), it.split(' '), "letter", false) }
                continue
            }
            val (ph, src) = lookup(w)
            if (src == "guess") notes += Note("guess", "\"$tok\" is not in the dictionary: pronunciation guessed (${ph.joinToString(" ")})")
            cur.words += Word(tok, ph, src, w in FUNC)
        }
        if (cur.words.isNotEmpty()) {
            sentences += cur
            if (!Regex("[.!?]\\s*$").containsMatchIn(text.trim())) notes += Note("style", "No final punctuation: read as a statement.")
        }
        val hetero = pos?.getJSONObject("hetero")
        for (s in sentences) {
            val tags = tag(s.words.map { it.text })
            s.words.forEachIndexed { i, w ->
                w.pos = if (w.src == "letter") "NOUN" else tags[i]
                val lw = w.text.lowercase()
                w.weight = if (lw in NEG) 1.0 else if (lw in AUX || Regex("^(be|been|being|'s|'re|'m|'ll|'d|'ve)$").matches(lw)) 0.0 else ACCENT[w.pos] ?: 0.0
                if (hetero != null && hetero.has(lw) && w.src == "dict" && (w.pos == "VERB" || w.pos == "NOUN" || w.pos == "ADJ")) {
                    val pick = hetero.getJSONArray(lw).getString(if (w.pos == "VERB") 1 else 0)
                    w.ph = pick.split(' ')
                    val sy = w.ph.filter { x -> x.any(Char::isDigit) }; val at = sy.indexOfFirst { it.endsWith("1") }
                    notes += Note("grammar", "\"${w.text}\" read as a ${if (w.pos == "VERB") "verb" else if (w.pos == "ADJ") "adjective" else "noun"}: stress on syllable ${at + 1} of ${sy.size}")
                }
            }
            val first = s.words[0].text.lowercase()
            s.type = if (s.end == "?") (if (first in WH) "wh" else "yn") else if (s.end == "!") "excl" else "stmt"
            val label = mapOf("wh" to "wh-question: falls at the end", "yn" to "yes/no question: rises at the end",
                "excl" to "exclamation: stronger accents, falls", "stmt" to "statement: falls at the end")[s.type]
            notes += Note("tone", "\"${s.words.joinToString(" ") { it.text }}${s.end}\" — $label")
            var run = 0
            for (w in s.words) { run = if (w.brk) 0 else run + 1; if (run == 22) notes += Note("style", "Long stretch without a comma: add one so the pitch can reset and the voice can pause.") }
            if (s.type == "yn" && first !in AUX) notes += Note("tone", "Question without an auxiliary verb first (\"${s.words[0].text} …\"): intonation rise used anyway.")
        }
        return Lint(sentences, notes)
    }

    // ---------------------------------------------------------------- phones + intonation
    private class P(val p: String, val st: Int)
    private fun phonesOf(w: Word) = w.ph.map { x -> P(if (x.lowercase() == "ah0") "ax" else x.replaceFirst(Regex("\\d"), "").lowercase(), if (x.any(Char::isDigit)) x.last() - '0' else -1) }
    private fun mainVowel(ph: List<P>): Int { val m = ph.indexOfFirst { it.st == 1 }; return if (m < 0) ph.indexOfFirst { it.st >= 0 } else m }

    fun prosody(sentences: List<Sentence>, o: Opts): List<Ph> = if (o.pos && pos != null) prosodyPos(sentences, o) else prosodyList(sentences, o)

    /** Accents by word class, downstep, and a nuclear accent per phrase where the tune turns (a ramp across its vowel). */
    private fun prosodyPos(sentences: List<Sentence>, o: Opts): List<Ph> {
        val seq = arrayListOf(Ph("_", pause = 60.0)); val flat = o.tone == "flat"
        val sing = o.tone == "sing" && !o.melody.isNullOrEmpty(); val singsong = o.style == "singsong"
        var wordNo = 0
        sentences.forEachIndexed { si, s ->
            val type = if (o.style == "uptalk" && s.type == "stmt") "yn" else s.type
            val A = o.accent * (if (type == "excl") 1.4 else 1.0); val n = s.words.size
            val phrases = ArrayList<List<Int>>(); var cur = ArrayList<Int>()
            s.words.forEachIndexed { i, w -> cur += i; if (w.brk || i == n - 1) { phrases += cur; cur = ArrayList() } }
            phrases.forEachIndexed { pi, phr ->
                val final = pi == phrases.size - 1
                fun wt(i: Int) = if (s.words[i].weight != 0.0) s.words[i].weight else if (type == "wh" && i == 0) 0.8 else 0.0
                val acc = phr.filter { wt(it) > 0 }
                val nuc = if (acc.isNotEmpty()) acc.last() else phr.last()
                var k = 0
                for (i in phr) {
                    val w = s.words[i]; val ph = phonesOf(w); val main = mainVowel(ph)
                    val decl = if (n > 1) 0.8 - 1.6 * i / (n - 1) else 0.0
                    val pre = if (i < nuc && wt(i) > 0) { val f = if (singsong && k % 2 == 1) -0.8 else 1.0; A * wt(i) * 0.85.pow(k++) * f } else 0.0
                    val note = if (sing) o.melody!![wordNo++ % o.melody.size] else 0
                    ph.forEachIndexed { j, x ->
                        var semi = decl; var semi2: Double? = null
                        if (i < nuc) { if (j == main) semi += pre else if (x.st == 2 && pre != 0.0) semi += pre * 0.3 }
                        else {
                            val on = i == nuc && j == main; val past = i > nuc || j > main
                            val (from, to, after) = if (final && type == "yn") Triple(decl - 0.3 * A, (if (i == phr.last()) 1.8 else 1.0) * A, A * 1.8)
                                else if (final) Triple(decl + A, -A, -A) else Triple(decl + 0.3 * A, decl + A, decl + A)
                            if (on) { semi = from; semi2 = to } else if (past) semi = after
                        }
                        val lastWord = i == phr.last()
                        val st = (if (lastWord && j >= main) 1.3 else 1.0) * (if (x.st >= 0) o.vowelStretch else 1.0)
                        seq += if (sing) Ph(x.p, note.toDouble(), null, st, w = w.text)
                        else Ph(x.p, if (flat) 0.0 else semi, if (flat) null else semi2, st, w = w.text)
                    }
                }
                if (!final) seq += Ph("_", pause = 180.0)
            }
            seq += Ph("_", pause = if (si == sentences.size - 1) 60.0 else 350.0)
        }
        return seq
    }
    /** The earlier function-word-list rules (Opts.pos = false), kept for comparison. */
    private fun prosodyList(sentences: List<Sentence>, o: Opts): List<Ph> {
        val seq = arrayListOf(Ph("_", pause = 60.0))
        sentences.forEachIndexed { si, s ->
            val A = o.accent * (if (s.type == "excl") 1.4 else 1.0); val n = s.words.size
            var phraseStart = true
            s.words.forEachIndexed { wi, w ->
                val ph = phonesOf(w); val main = mainVowel(ph)
                val last = wi == n - 1; val phraseEnd = last || w.brk
                val decl = if (n > 1) 1.0 - 2.0 * wi / (n - 1) else 0.0
                ph.forEachIndexed { k, x ->
                    var semi = decl
                    if (!w.func) {
                        if (k == main) { semi += if (phraseStart) A else A * 0.6 }
                        else if (x.st == 2) { semi += 1 }
                    }
                    if (last && k >= main && main >= 0) {
                        if (s.type == "yn") { semi = if (k == main) A else A * 1.8 }
                        else if (k > main) { semi = -A }
                    } else if (w.brk && k >= main && main >= 0) { semi = if (k == main) decl + A * 0.5 else decl + A }
                    seq += Ph(x.p, if (o.tone == "flat") 0.0 else semi, null, if (phraseEnd && k >= main) 1.3 else 1.0, w = w.text)
                }
                if (!w.func) phraseStart = false
                if (w.brk && !last) { seq += Ph("_", pause = 180.0); phraseStart = true }
            }
            seq += Ph("_", pause = if (si == sentences.size - 1) 60.0 else 350.0)
        }
        return seq
    }

    /** Phones -> 10 ms frames by stitching diphone units (half-phones where the corpus has no diphone). */
    fun framesFromSeq(seq: List<Ph>, speed: Double = 1.0, select: Boolean = true): List<Frame> {
        val raw = ArrayList<Frame>()
        fun push(f: JSONArray, i: Int) = raw.add(Frame(f.getDouble(0), f.getInt(1), doubleArrayOf(f.getDouble(2), f.getDouble(3), f.getDouble(4)),
            doubleArrayOf(f.getDouble(5), f.getDouble(6), f.getDouble(7)), f.getDouble(8), i = i, hf = if (f.length() > 9) f.getDouble(9) else -30.0))
        val silence = JSONArray(listOf(-90, 0, 500, 1500, 2500, 0, 0, 0, 0))
        class Cand(val split: Int, val fr: List<JSONArray>)
        fun cand(o: JSONObject) = Cand(o.getInt("s"), o.getJSONArray("f").let { arr -> List(arr.length()) { arr.getJSONArray(it) } })
        // candidates per diphone (units.json keeps up to 4; half-phones where the corpus has none)
        val cands = (0 until seq.size - 1).map { i ->
            val a = seq[i].p; val b = seq[i + 1].p
            val u = units.optJSONObject("$a $b")
            if (u != null) {
                val alt = if (select) u.optJSONArray("alt") else null
                listOf(cand(u)) + (0 until (alt?.length() ?: 0)).map { cand(alt!!.getJSONObject(it)) }
            } else {
                val pa = phones.optJSONObject(a) ?: phones.getJSONObject("_"); val pb = phones.optJSONObject(b) ?: phones.getJSONObject("_")
                val fa = pa.getJSONArray("f"); val fb = pb.getJSONArray("f")
                listOf(Cand(fa.length() - pa.getInt("s"),
                    (pa.getInt("s") until fa.length()).map { fa.getJSONArray(it) } + (0 until pb.getInt("s")).map { fb.getJSONArray(it) }))
            }
        }
        // unit selection (Viterbi): join cost at each seam (formant jumps, energy step, voicing flip) + length target
        val pick = IntArray(cands.size)
        if (select && cands.isNotEmpty()) {
            fun join(x: Cand, y: Cand): Double {
                val p = x.fr.last(); val q = y.fr.first()
                var c = abs(p.getDouble(0) - q.getDouble(0)) * 0.1 + (if (p.getInt(1) != q.getInt(1)) 1 else 0)
                if (p.getInt(1) != 0 && q.getInt(1) != 0) for (k in 2 until 5) c += abs(ln(max(1.0, p.getDouble(k)) / max(1.0, q.getDouble(k)))) * 4
                return c
            }
            fun target(list: List<Cand>, j: Int) = abs(list[j].fr.size - list[0].fr.size) * 0.15
            var cost = DoubleArray(cands[0].size) { target(cands[0], it) }
            val back = ArrayList<IntArray>()
            for (i in 1 until cands.size) {
                val row = DoubleArray(cands[i].size); val bk = IntArray(cands[i].size)
                cands[i].forEachIndexed { j, y ->
                    var best = Double.POSITIVE_INFINITY; var bj = 0
                    cands[i - 1].forEachIndexed { k, x -> val c = cost[k] + join(x, y); if (c < best) { best = c; bj = k } }
                    row[j] = best + target(cands[i], j); bk[j] = bj
                }
                cost = row; back += bk
            }
            var j = cost.indices.minBy { cost[it] }
            pick[cands.size - 1] = j
            for (i in cands.size - 1 downTo 1) { j = back[i - 1][j]; pick[i - 1] = j }
        }
        for (i in 0 until seq.size - 1) {
            val b = seq[i + 1].p
            val u = cands[i][pick[i]]
            u.fr.forEachIndexed { k, f -> push(f, if (k < u.split) i else i + 1) }
            if (b == "_" && seq[i + 1].pause > 120) {
                var k = 0
                while (k < (seq[i + 1].pause - 120) / HOP) { push(silence, i + 1); k++ }
            }
        }
        val out = ArrayList<Frame>()
        var acc = 0.0
        for (f in raw) {
            acc += seq[f.i].stretch / speed
            while (acc >= 1) { out += f.copy().also { it.p = seq[f.i].p }; acc -= 1 }
        }
        val count = HashMap<Int, Int>(); val seen = HashMap<Int, Int>()
        out.forEach { count[it.i] = (count[it.i] ?: 0) + 1 }
        out.forEach { f ->
            val q = seq[f.i]; val k = seen[f.i] ?: 0; val n = count[f.i]!!
            seen[f.i] = k + 1
            f.target = if (q.semi2 == null) q.semi else q.semi + (q.semi2 - q.semi) * (if (n > 1) k.toDouble() / (n - 1) else 1.0)
        }
        for (k in out.indices) {
            var s = 0.0; var n = 0
            for (j in max(0, k - 3)..min(out.size - 1, k + 3)) { s += out[j].target; n++ }
            out[k].semi = s / n
        }
        return out.mapIndexed { k, f ->
            if (f.v == 0) f else {
                val nb = listOfNotNull(out.getOrNull(k - 1), f, out.getOrNull(k + 1)).filter { it.v != 0 }
                f.copy(F = DoubleArray(3) { i -> nb.sumOf { it.F[i] } / nb.size }, L = DoubleArray(3) { i -> nb.sumOf { it.L[i] } / nb.size })
            }
        }
    }

    fun speak(text: String, o: Opts = Opts()): Result {
        val l = lint(text)
        return Result(l.sentences, l.notes, framesFromSeq(prosody(l.sentences, o), o.speed, o.select), 0.0)
    }

    // ---------------------------------------------------------------- recording analysis
    private fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) { j = j xor bit; bit = bit shr 1 }
            j = j xor bit
            if (i < j) { var t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t }
        }
        var len = 2
        while (len <= n) {
            val a = -2 * PI / len; val wr = cos(a); val wi = sin(a)
            var i = 0
            while (i < n) {
                var cr = 1.0; var ci = 0.0
                for (k in 0 until len / 2) {
                    val ur = re[i + k]; val ui = im[i + k]
                    val vr = re[i + k + len / 2] * cr - im[i + k + len / 2] * ci; val vi = re[i + k + len / 2] * ci + im[i + k + len / 2] * cr
                    re[i + k] = ur + vr; im[i + k] = ui + vi; re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi
                    val t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t
                }
                i += len
            }
            len = len shl 1
        }
    }
    private fun resample(x: DoubleArray, from: Double, to: Double): DoubleArray {
        if (from == to) return x.copyOf()
        val ratio = from / to; val n = floor(x.size / ratio).toInt(); val out = DoubleArray(n); val fc = min(1.0, 1 / ratio) * 0.95; val W = 16
        val cr = ceil(ratio).toInt()
        for (i in 0 until n) {
            val c = i * ratio; val c0 = floor(c).toInt()
            var s = 0.0; var ws = 0.0
            for (k in c0 - W * cr..c0 + W * cr) {
                if (k < 0 || k >= x.size) continue
                val d = (k - c) * fc; val win = 0.5 + 0.5 * cos(PI * (k - c) / (W * cr + 1))
                val h = (if (d == 0.0) 1.0 else sin(PI * d) / (PI * d)) * win
                s += x[k] * h; ws += h
            }
            out[i] = if (ws != 0.0) s / ws else 0.0
        }
        return out
    }
    private fun lpc(x: DoubleArray, order: Int): DoubleArray {
        val r = DoubleArray(order + 1)
        for (l in 0..order) { var s = 0.0; for (i in l until x.size) s += x[i] * x[i - l]; r[l] = s }
        val a = DoubleArray(order + 1); a[0] = 1.0
        if (r[0] <= 0) return a
        var e = r[0]
        for (i in 1..order) {
            var acc = r[i]
            for (j in 1 until i) acc += a[j] * r[i - j]
            val k = -acc / e; val prev = a.copyOf()
            for (j in 1 until i) a[j] = prev[j] + k * prev[i - j]
            a[i] = k; e *= 1 - k * k
            if (e <= 0) break
        }
        return a
    }
    /** Per 10 ms frame of a recording: energy dB, voicing + f0, F1-F3 and their levels (LPC envelope peaks), centroid. */
    fun analyse(samples: FloatArray, sr: Int): List<Frame> {
        val SR = 22050.0
        val a = resample(DoubleArray(samples.size) { samples[it].toDouble() }, sr.toDouble(), SR); val lo = resample(a, SR, SR / 2); val srl = SR / 2
        val win = floor(0.03 * SR).toInt(); val hop = floor(HOP / 1000.0 * SR).toInt()
        fun ham(n: Int) = DoubleArray(n) { 0.54 - 0.46 * cos(2 * PI * it / (n - 1)) }
        val hw = ham(win); val hwl = ham(win / 2); val NF = 1024
        var prevF = doubleArrayOf(500.0, 1500.0, 2500.0)
        val frames = ArrayList<Frame>()
        var i = 0
        while (i + win < a.size) {
            val x = DoubleArray(win); var pw = 0.0
            for (k in 0 until win) { x[k] = a[i + k] * hw[k]; pw += x[k] * x[k] }
            val e = 20 * log10(sqrt(pw / win) + 1e-9)
            val lmin = floor(SR / 320).toInt(); val lmax = floor(SR / 70).toInt()
            // normalised by the energy of both overlapping parts (unbiased for long lags / low voices); voiced above 0.4
            val c2 = DoubleArray(win + 1)
            for (k in 0 until win) c2[k + 1] = c2[k] + x[k] * x[k]
            var best = -1.0; var bk = 0
            for (l in lmin until lmax) {
                var s = 0.0; for (k in l until win) s += x[k] * x[k - l]
                val r = s / (sqrt((c2[win] - c2[l]) * c2[win - l]) + 1e-12)
                if (r > best) { best = r; bk = l }
            }
            val voiced = pw > 0 && best > 0.4; val f0 = if (voiced) SR / bk else 0.0
            val j = i / 2; val nl = win / 2; val xl = DoubleArray(nl)
            for (k in 0 until nl) xl[k] = ((lo.getOrElse(j + k) { 0.0 }) - (if (k != 0) 0.97 * lo.getOrElse(j + k - 1) { 0.0 } else 0.0)) * hwl[k]
            val A = lpc(xl, 12); val H = DoubleArray(512)
            for (b in 0 until 512) {
                val w = PI * b / 512; var re = 0.0; var im = 0.0
                for (k in 0..12) { re += A[k] * cos(w * k); im -= A[k] * sin(w * k) }
                H[b] = 1 / (hypot(re, im) + 1e-9)
            }
            val cand = ArrayList<Double>()
            for (b in 1 until 511) { val f = b * srl / 2 / 512; if (H[b] > H[b - 1] && H[b] >= H[b + 1] && f > 150 && f < 4500) cand += f }
            val F = prevF.copyOf()
            listOf(200.0 to 1000.0, 700.0 to 2800.0, 1600.0 to 3800.0).forEachIndexed { idx, (lf, hf) ->
                cand.firstOrNull { f -> f >= lf && f <= hf && (idx == 0 || f > F[idx - 1] + 150) }?.let { F[idx] = it }
            }
            prevF = F
            val L = DoubleArray(3) { 20 * log10(H[min(511, floor(F[it] / srl * 2 * 512).toInt())] + 1e-9) }
            val re = DoubleArray(NF); val im = DoubleArray(NF)
            for (k in 0 until win) re[k] = a[i + k] * (0.5 - 0.5 * cos(2 * PI * k / (win - 1)))
            fft(re, im)
            var num = 0.0; var den = 0.0
            var hiE = 0.0; var allE = 1e-18
            for (b in 0..NF / 2) { val m = hypot(re[b], im[b]); val f = b * SR / NF; num += f * m; den += m; allE += m * m; if (f > 4000) hiE += m * m }
            frames += Frame(e, if (voiced) 1 else 0, F, L, num / (den + 1e-9), f0, hf = 10 * log10(hiE / allE + 1e-18))
            i += hop
        }
        return frames
    }
    fun framesFromRecording(frames: List<Frame>, tone: String = "follow", speed: Double = 1.0): Result {
        val vf = frames.filter { it.v != 0 }.map { it.f0 }.sorted()
        val med = if (vf.isNotEmpty()) vf[vf.size shr 1] else 110.0
        val out = ArrayList<Frame>(); var acc = 0.0
        for (f in frames) {
            acc += 1 / speed
            while (acc >= 1) { out += f.copy().also { it.semi = if (tone == "follow" && f.v != 0) 12 * log2(f.f0 / med) else 0.0 }; acc -= 1 }
        }
        if (tone == "follow") {
            val s = out.map { it.semi }
            out.forEachIndexed { k, f -> val w = s.subList(max(0, k - 3), min(s.size, k + 4)).sorted(); f.semi = w[w.size shr 1].coerceIn(-12.0, 12.0) }
        }
        return Result(emptyList(), emptyList(), out, frames.maxOfOrNull { it.e } ?: 0.0)
    }

    // ---------------------------------------------------------------- FM-1 voices (speech/fm_speak.py designs)
    private fun ratioFor(f: Double, f0: Double) = min(31, max(1, jsRound(f / f0)))
    private fun op(v: IntArray, n: Int, field: String, value: Int) { v[Dx7.paramIndex(n, field)] = value }
    private fun baseVoice(name: String): IntArray {
        val v = Dx7.initVoice(name)
        for ((f, x) in listOf("ALG" to 21, "FB" to 0, "OKS" to 1, "TRNP" to 24, "LFS" to 40, "LFD" to 0, "LPMD" to 0, "LAMD" to 0)) v[Dx7.paramIndex(null, f)] = x
        for (n in 1..6) for ((f, x) in listOf("OL" to 0, "R1" to 95, "R2" to 60, "R3" to 60, "R4" to 72, "L1" to 99, "L2" to 99, "L3" to 99, "L4" to 0,
            "KVS" to 0, "DT" to 7, "FC" to 1, "FF" to 0)) op(v, n, f, x)
        return v
    }
    fun voiced(f0: Double, F: DoubleArray, levels: IntArray, body: Int, bright: Int): IntArray {
        val v = baseVoice("SPEECH V")
        op(v, 3, "FC", ratioFor(F[0], f0)); op(v, 3, "OL", levels[0])
        op(v, 4, "FC", ratioFor(F[1], f0)); op(v, 4, "OL", levels[1])
        op(v, 5, "FC", ratioFor(F[2], f0)); op(v, 5, "OL", levels[2])
        op(v, 6, "FC", 1); op(v, 6, "OL", bright)
        op(v, 1, "FC", 1); op(v, 1, "OL", body)
        op(v, 2, "FC", 1); op(v, 2, "OL", 40)
        return v
    }
    fun fricative(f0: Double, centre: Double, withVoice: Boolean): IntArray {
        val v = baseVoice("SPEECH U"); val r = ratioFor(centre, f0)
        op(v, 4, "FC", r); op(v, 4, "OL", 90)
        op(v, 5, "FC", min(31, r + 6)); op(v, 5, "OL", 86)
        op(v, 3, "FC", max(1, r - 5)); op(v, 3, "OL", 70)
        op(v, 6, "FC", 1); op(v, 6, "OL", 96)
        v[Dx7.FB] = 7
        if (withVoice) { op(v, 1, "FC", 1); op(v, 1, "OL", 82); op(v, 2, "FC", 1); op(v, 2, "OL", 40) }
        return v
    }
    // ---- alternative voiced designs (port of speech.js; FINDINGS.md "Speech: voice designs") ----
    /** DX7 fixed-frequency operator: f = 10^(FC&3) * 10^(FF/100) Hz. */
    private fun fixedFreq(v: IntArray, n: Int, f: Double) {
        val l = log10(f.coerceIn(1.0, 9772.0))
        var c = floor(l).toInt(); var ff = jsRound((l - c) * 100)
        if (ff > 99) { c += 1; ff = 0 }
        op(v, n, "MODE", 1); op(v, n, "FC", c.coerceIn(0, 3)); op(v, n, "FF", ff)
    }
    private fun lvAdd(ol: Int, db: Double) = jsRound(ol + db / 0.75).coerceIn(0, 99)
    /** Le Brun / Chafe: each formant from the two bracketing harmonics, cross-faded by proximity (algorithm 24). */
    fun voicedLeBrun(f0: Double, F: DoubleArray, levels: IntArray, body: Int, bright: Int): IntArray {
        val v = baseVoice("SPEECH L")
        v[Dx7.ALG] = 23
        fun pair(fc: Double, lv: Int, a: Int, b: Int) {
            val r = (fc / f0).coerceIn(1.0, 30.0); val lo = floor(r).toInt(); val w = r - lo
            op(v, a, "FC", max(1, lo)); op(v, a, "OL", lvAdd(lv, 20 * log10(max(1e-3, 1 - w))))
            op(v, b, "FC", min(31, lo + 1)); op(v, b, "OL", lvAdd(lv, 20 * log10(max(1e-3, w))))
        }
        pair(F[0], max(levels[0], body), 1, 2)
        pair(F[1], levels[1], 3, 4)
        op(v, 5, "FC", ratioFor(F[2], f0)); op(v, 5, "OL", levels[2])
        op(v, 6, "FC", 1); op(v, 6, "OL", bright)
        return v
    }
    /** Sine-wave speech (Remez & Rubin; SAM): carriers at the exact formants plus a quiet fundamental (algorithm 32). */
    fun voicedSine(f0: Double, F: DoubleArray, levels: IntArray, body: Int): IntArray {
        val v = baseVoice("SPEECH S")
        v[Dx7.ALG] = 31
        for (i in 0..2) { fixedFreq(v, i + 1, F[i]); op(v, i + 1, "OL", levels[i]) }
        op(v, 4, "FC", 1); op(v, 4, "OL", max(0, body - 20))
        return v
    }
    /** Exact formant centres with voicing: fixed-frequency carriers at F1-F3 modulated by op6 at f0 (the default). */
    fun voicedFixedFM(f0: Double, F: DoubleArray, levels: IntArray, body: Int, bright: Int): IntArray {
        val v = voiced(f0, F, levels, body, bright)
        for (i in 0..2) fixedFreq(v, i + 3, F[i])
        return v
    }

    // ---- diphthongs as ONE note (port of speech.js; FINDINGS.md "Diphthongs") ----
    // operator EG: ms for a 99 -> 0 fall to reach -20 dB (rates 33..80) and a 0 -> 99 rise to reach -6 dB (rates 22..80)
    private val FALL20 = intArrayOf(1984, 1984, 1652, 1416, 1416, 1240, 1240, 992, 826, 826, 708, 620, 620, 496, 414, 414, 354, 310, 310, 248, 248, 208,
        178, 178, 156, 124, 124, 104, 90, 90, 78, 62, 62, 52, 52, 44, 40, 40, 32, 26, 26, 22, 20, 20, 16, 16, 14, 12)
    private val RISE6 = intArrayOf(2230, 2230, 1910, 1672, 1672, 1338, 1338, 1114, 956, 956, 836, 668, 668, 558, 478, 478, 418, 418, 334, 278, 278, 238, 208,
        208, 168, 140, 140, 120, 104, 104, 84, 84, 70, 60, 60, 52, 42, 42, 34, 30, 30, 26, 20, 20, 18, 18, 14, 14, 14, 10, 8, 8, 8, 6, 6, 6, 6, 4, 4)
    private fun rateFor(tab: IntArray, r0: Int, ms: Double): Int {
        var best = r0; var err = 1e9
        tab.forEachIndexed { k, t -> val e = abs(ln(t / max(4.0, ms))); if (e < err) { err = e; best = r0 + k } }
        return best
    }
    private fun fade(v: IntArray, n: Int, r: Int) { for ((f, x) in listOf("R1" to 99, "L1" to 99, "R2" to r, "L2" to 0, "R3" to 99, "L3" to 0)) op(v, n, f, x) }
    private fun grow(v: IntArray, n: Int, r: Int) { for ((f, x) in listOf("R1" to 99, "L1" to 0, "R2" to r, "L2" to 99, "R3" to 99, "L3" to 99)) op(v, n, f, x) }
    private val DIPH = Regex("^(ay|aw|oy|ey|ow)")
    /** Pitch-EG speed on the FM-1, 1/32-octave steps per ms at PR 40..85 (step 5), test_peg_sweep.py. */
    private val PEG_SPEED = doubleArrayOf(0.041, 0.051, 0.062, 0.077, 0.089, 0.105, 0.12, 0.147, 0.185, 0.218)
    fun pegRate(perMs: Double): Int {
        if (perMs <= 0) return 99
        val t = PEG_SPEED; val x = ln(perMs)
        if (perMs <= t[0]) return max(1, jsRound(40 + (x - ln(t[0])) / ln(t[1] / t[0]) * 5))
        if (perMs >= t[t.size - 1]) return min(99, jsRound(85 + (x - ln(t[9])) / ln(t[9] / t[8]) * 5))
        var k = 0; while (t[k + 1] < perMs) k++
        return jsRound(40 + 5 * (k + (x - ln(t[k])) / ln(t[k + 1] / t[k])))
    }
    private fun ratioOf(v: IntArray, n: Int, r0: Double) {
        val r = r0.coerceIn(0.5, 31.9)
        op(v, n, "MODE", 0)
        if (r < 1) { op(v, n, "FC", 0); op(v, n, "FF", min(99, jsRound((r / 0.5 - 1) * 100))) }
        else { val c = floor(r).toInt(); op(v, n, "FC", c); op(v, n, "FF", min(99, jsRound((r / c - 1) * 100))) }
    }
    private fun fixedHz(v: IntArray, n: Int) = 10.0.pow((v[Dx7.paramIndex(n, "FC")] and 3) + v[Dx7.paramIndex(n, "FF")] / 100.0)
    class Ends(val F: DoubleArray, val lv: IntArray)
    /** A real glide in one note: op4 = F2 in ratio mode swept by the pitch EG (the FM-1's pitch EG moves ratio operators
     *  but not fixed ones); the f0 modulators op6/op2 and F3 (op5) are fixed; F1 by staggered onset (op3 out, op1 in). */
    fun diphSweep(keyHz: Double, f0: Double, A: Ends, B: Ends, body: Int, bright: Int, fallR: Int, riseR: Int, D: Double,
                  onset: Double = 0.35, glideEnd: Double = 0.8): IntArray {
        val v = baseVoice("SPEECH W")
        val steps = jsRound(32 * log2(B.F[1] / A.F[1])).coerceIn(-23, 35)
        ratioOf(v, 4, A.F[1] / keyHz); op(v, 4, "OL", A.lv[1])
        val s1 = jsRound(steps * 0.15); val t1 = max(10.0, D * onset); val t2 = max(10.0, D * (glideEnd - onset))
        for ((f, x) in listOf("PL4" to 50, "PR1" to pegRate(abs(s1) / t1), "PL1" to 50 + s1, "PR2" to pegRate(abs(steps - s1) / t2),
                "PL2" to 50 + steps, "PR3" to 99, "PL3" to 50 + steps, "PR4" to 99)) v[Dx7.paramIndex(null, f)] = x
        fixedFreq(v, 6, f0); op(v, 6, "OL", bright)
        fixedFreq(v, 2, f0); op(v, 2, "OL", bright)
        fixedFreq(v, 5, sqrt(A.F[2] * B.F[2])); op(v, 5, "OL", jsRound((A.lv[2] + B.lv[2]) / 2.0))
        val f1moves = abs(ln(B.F[0] / A.F[0])) > 0.18
        fixedFreq(v, 3, A.F[0]); op(v, 3, "OL", A.lv[0])
        if (f1moves) { fade(v, 3, fallR); fixedFreq(v, 1, B.F[0]); op(v, 1, "OL", B.lv[0]); grow(v, 1, riseR) }
        else { fixedFreq(v, 1, f0); op(v, 1, "OL", body); op(v, 2, "OL", 40) }
        return v
    }

    // ---- noise bands (port of speech.js; FINDINGS.md "Consonants") ----
    /** Fricative as a wide noise: exact centre, cascade of inharmonic modulators (algorithm 1). */
    fun fricNoise(centre: Double, depth: Int = 88, upper: Double = -8.0, spread: Double = 1.0): IntArray {
        val v = baseVoice("SPEECH N"); v[Dx7.ALG] = 0; v[Dx7.FB] = 7
        fixedFreq(v, 3, centre); op(v, 3, "OL", 92)
        fixedFreq(v, 4, centre * 0.371 * spread); op(v, 4, "OL", depth)
        fixedFreq(v, 5, centre * 0.613 * spread); op(v, 5, "OL", depth - 4)
        fixedFreq(v, 6, centre * 1.137 * spread); op(v, 6, "OL", depth)
        fixedFreq(v, 1, centre * 1.43); op(v, 1, "OL", lvAdd(92, upper))
        fixedFreq(v, 2, centre * 0.529 * spread); op(v, 2, "OL", depth)
        return v
    }
    /** Aspiration (h, breath after p/t/k): the frame's own F1-F3 driven by a low inharmonic modulator (algorithm 22). */
    fun aspirate(F: DoubleArray, levels: IntArray, depth: Int = 80): IntArray {
        val v = baseVoice("SPEECH H"); v[Dx7.ALG] = 21; v[Dx7.FB] = 7
        for (i in 0..2) { fixedFreq(v, i + 3, F[i]); op(v, i + 3, "OL", levels[i]) }
        fixedFreq(v, 6, 137.0); op(v, 6, "OL", depth)
        return v
    }
    /** Voiced fricative: fundamental + voice bar at F1 + a hiss band at the measured centre (algorithm 22). */
    fun voicedFric(f0: Double, F: DoubleArray, levels: IntArray, body: Int, centre: Double, depth: Int = 76): IntArray {
        val v = baseVoice("SPEECH Z"); v[Dx7.ALG] = 21; v[Dx7.FB] = 7
        op(v, 1, "FC", 1); op(v, 1, "OL", body)
        fixedFreq(v, 3, F[0]); op(v, 3, "OL", levels[0])
        fixedFreq(v, 4, centre); op(v, 4, "OL", lvAdd(levels[0], -6.0))
        fixedFreq(v, 5, centre * 1.28); op(v, 5, "OL", lvAdd(levels[0], -9.0))
        fixedFreq(v, 6, centre * 0.093); op(v, 6, "OL", depth)
        return v
    }
    /** Fricative as a band-limited hiss: carriers across the band, LOW inharmonic modulators (algorithm 22). */
    fun fricBand(centre: Double, depth: Int = 78, width: Double = 1.0): IntArray {
        val v = baseVoice("SPEECH B"); v[Dx7.ALG] = 21; v[Dx7.FB] = 7
        fun w(k: Double) = 1 + (k - 1) * width
        fixedFreq(v, 3, centre * w(0.8)); op(v, 3, "OL", 86)
        fixedFreq(v, 4, centre); op(v, 4, "OL", 92)
        fixedFreq(v, 5, centre * w(1.28)); op(v, 5, "OL", 87)
        fixedFreq(v, 6, centre * 0.093); op(v, 6, "OL", depth)
        fixedFreq(v, 1, centre * w(1.62)); op(v, 1, "OL", 80)
        fixedFreq(v, 2, centre * 0.137); op(v, 2, "OL", depth - 4)
        return v
    }

    /** Same sound one key-octave up: every ratio halved exactly (odd ratios via the fine ratio: 3 -> 1 x 1.50). */
    fun octaveUp(v: IntArray): IntArray {
        val w = v.copyOf()
        for (n in 1..6) {
            if (w[Dx7.paramIndex(n, "MODE")] != 0) continue
            val fc = w[Dx7.paramIndex(n, "FC")]; val ff = w[Dx7.paramIndex(n, "FF")]
            val r = (if (fc == 0) 0.5 else fc.toDouble()) * (1 + ff / 100.0) / 2
            if (r < 1) { op(w, n, "FC", 0); op(w, n, "FF", min(99, jsRound((r / 0.5 - 1) * 100))) }
            else { val c = floor(r).toInt(); op(w, n, "FC", c); op(w, n, "FF", min(99, jsRound((r / c - 1) * 100))) }
        }
        return w
    }

    /** Frames -> notes, one per [Opts.hop] ms. */
    fun plan(frames: List<Frame>, emax: Double, o: Opts): List<PlanNote> {
        val step = jsRound(o.hop / HOP.toDouble()); val notes = ArrayList<PlanNote>()
        var lastKey = o.base; var quietBefore = true; var lastPitch: Double? = null; var prevVoiced = false
        // partial-depth soft start: jump to -softStart dB, then ease up at softRise (plain-envelope operators only)
        fun soften(v: IntArray, isV: Boolean) {
            if (o.softStart == 0.0 || (o.softVoiced && !(isV && prevVoiced))) return
            val l1 = max(0, 99 - jsRound(o.softStart / 0.75))
            for (k in 1..6) if (v[Dx7.paramIndex(k, "L1")] == 99 && v[Dx7.paramIndex(k, "L2")] == 99 && v[Dx7.paramIndex(k, "L3")] == 99) {
                op(v, k, "R1", 99); op(v, k, "L1", l1); op(v, k, "R2", o.softRise)
            }
        }
        fun pegLevel(st: Double) = (50 + jsRound(st * 32 / 12)).coerceIn(27, 85)
        val band = o.fric == "band"
        fun fs(f: Double) = min(5000.0, f * o.formant)
        fun fsc(c: Double) = c * (1 + (o.formant - 1) * 0.5)
        var n = 0
        while (n + step <= frames.size) {
            // a diphthong (or, with diphAll, any vowel) as one long note whose F2 glides inside the voice
            if (o.diph == "sweep" && !o.whisper && frames[n].p.isNotEmpty() && (if (o.diphAll) VOWEL else DIPH).containsMatchIn(frames[n].p)) {
                var end = n; while (end < frames.size && frames[end].i == frames[n].i) end++
                val run = frames.subList(n, end); val vr = run.filter { it.v != 0 }; val e = run.maxOf { it.e }
                if (vr.size >= 6 && vr.size * 4 >= run.size * 3 && e >= emax + o.silenceDb) {
                    val k = max(2, jsRound(vr.size * o.diphEdge))
                    fun med(fr: List<Frame>, get: (Frame) -> Double): Double { val s = fr.map(get).sorted(); return if (s.size % 2 == 1) s[s.size shr 1] else (s[s.size / 2 - 1] + s[s.size / 2]) / 2 }
                    val gain = jsRound((e - emax) / 0.75 * 0.7)
                    fun ends(fr: List<Frame>): Ends {
                        val F = DoubleArray(3) { i -> fs(med(fr) { it.F[i] }) }; val L = DoubleArray(3) { i -> med(fr) { it.L[i] } }; val top = L.max()
                        return Ends(F, IntArray(3) { i -> (99 + (L[i] - top) / 0.75 * o.slope + gain).toInt().coerceIn(20, 99) })
                    }
                    val A = ends(vr.subList(0, k)); val B = ends(vr.subList(vr.size - k, vr.size))
                    val semi = run.sumOf { it.semi } / run.size; val pitch = (o.base + semi).coerceIn(30.0, 72.0)
                    val key = (o.base + jsRound(semi)).coerceIn(30, 72); lastKey = key
                    val D = run.size * HOP.toDouble(); val body = (70 + gain).coerceIn(0, 99)
                    val fallR = rateFor(FALL20, 33, D * o.diphFall); val riseR = rateFor(RISE6, 22, D * o.diphRise)
                    val v = diphSweep(midiHz(key), midiHz(pitch), A, B, body, o.bright, fallR, riseR, D, o.diphOnset, o.diphGlide)
                    val notePitch = 69 + 12 * log2(fixedHz(v, 6) / 440)              // the fixed f0 sets the pitch
                    for (kk in 1..6) op(v, kk, "R4", if (o.releaseRate != 0) o.releaseRate else o.release)
                    soften(v, true)
                    notes += PlanNote(v, key, n * HOP.toDouble(), D + o.overlap, notePitch)
                    lastPitch = pitch; quietBefore = false; prevVoiced = true
                    n += ((end - n + step - 1) / step) * step
                    continue
                }
            }
            val g = frames.subList(n, n + step)
            val e = g.maxOf { it.e }
            if (e < emax + o.silenceDb) { quietBefore = true; lastPitch = null; prevVoiced = false; n += step; continue }
            val afterQuiet = quietBefore; quietBefore = e < emax - 30
            // loud frames with a vowel-like spectrum count as voiced (the pitch detector misses some vowel frames)
            val vo = if (o.whisper) emptyList() else g.filter { it.v != 0 || (band && it.hf < o.hfVowel && it.e > emax + o.vowelDb) }
            val gain = jsRound((e - emax) / 0.75 * 0.7)
            val semi = g.sumOf { it.semi } / g.size
            val key: Int; val v: IntArray
            val isVoiced = vo.size * 2 >= g.size
            val pitch = o.base + semi
            if (isVoiced) {
                key = (o.base + jsRound(semi)).coerceIn(30, 72); lastKey = key
                fun med(get: (Frame) -> Double): Double { val s = vo.map(get).sorted(); return if (s.size % 2 == 1) s[s.size shr 1] else (s[s.size / 2 - 1] + s[s.size / 2]) / 2 }
                val F = DoubleArray(3) { i -> fs(med { it.F[i] }) }; val L = DoubleArray(3) { i -> med { it.L[i] } }
                val top = L.max()
                val lv = IntArray(3) { i -> (99 + (L[i] - top) / 0.75 * o.slope + gain).toInt().coerceIn(20, 99) }
                val f0 = midiHz(key); val body = (70 + gain).coerceIn(0, 99)
                val hfv = med { it.hf }
                v = if (band && hfv > o.hfVoiced) voicedFric(f0, F, lv, body, fsc(med { it.cent }).coerceIn(1200.0, 7000.0))
                else when (o.design) {
                    "harmonic" -> voiced(f0, F, lv, body, o.bright)
                    "lebrun" -> voicedLeBrun(f0, F, lv, body, o.bright)
                    "sine" -> voicedSine(f0, F, lv, body)
                    else -> voicedFixedFM(f0, F, lv, body, o.bright)
                }
                if (o.inharm != 0) op(v, 6, "FF", o.inharm)          // modulator off the harmonic grid: bell / cyborg colour
                if (o.vib != 0) for ((f, x) in listOf("LPMD" to o.vib, "LPMS" to 3, "LFS" to 40, "LFW" to 4)) v[Dx7.paramIndex(null, f)] = x
            } else {
                val c = g.map { it.cent }.sorted()[g.size shr 1]
                key = lastKey
                val hfu = g.map { it.hf }.sorted()[g.size shr 1]
                if (band && hfu < o.hfAspirate) {
                    fun mid(get: (Frame) -> Double) = g.map(get).sorted()[g.size shr 1]
                    val F = DoubleArray(3) { i -> fs(mid { it.F[i] }) }; val L = DoubleArray(3) { i -> mid { it.L[i] } }; val top = L.max()
                    v = aspirate(F, IntArray(3) { i -> (92 + (L[i] - top) / 0.75 * o.slope + gain).toInt().coerceIn(20, 99) }, o.fricDepth)
                } else if (band) {
                    v = fricBand(fsc(c).coerceIn(1200.0, 7000.0), o.fricDepth, o.fricSpread)
                    for (k in listOf(1, 3, 4, 5)) op(v, k, "OL", (v[Dx7.paramIndex(k, "OL")] + gain).coerceIn(0, 99))
                } else if (o.fric == "noise") {
                    v = fricNoise(c.coerceIn(1200.0, 7000.0), o.fricDepth, o.fricUpper, o.fricSpread)
                    for (k in listOf(1, 3)) op(v, k, "OL", (v[Dx7.paramIndex(k, "OL")] + gain).coerceIn(0, 99))
                } else {
                    v = fricative(midiHz(key), c.coerceIn(1500.0, 3400.0), false)
                    for (k in 3..5) op(v, k, "OL", (v[Dx7.paramIndex(k, "OL")] + gain).coerceIn(0, 99))
                }
            }
            for (k in 1..6) op(v, k, "R4", if (o.releaseRate != 0) o.releaseRate else o.release)
            if (o.attack != 0) for (k in 1..6) op(v, k, "R1", min(v[Dx7.paramIndex(k, "R1")], o.attack))
            if (o.fine || o.glide != 0) {
                val p = if (isVoiced) pitch.coerceIn(30.0, 72.0) else key.toDouble()
                val hold = if (o.fine) pegLevel(p - key) else 50
                val from = if (o.glide != 0 && lastPitch != null) pegLevel(lastPitch!! - key) else hold
                for ((f, x) in listOf("PL4" to from, "PL1" to hold, "PL2" to hold, "PL3" to hold, "PR1" to (if (o.glide != 0) o.glide else 99),
                        "PR2" to 99, "PR3" to 99, "PR4" to 0)) v[Dx7.paramIndex(null, f)] = x
                if (isVoiced) lastPitch = p
            }
            val notePitch: Double? = if (isVoiced) (if (o.fine) pitch.coerceIn(30.0, 72.0) else key.toDouble()) else null
            // stop release after silence: a percussive burst instead of a held hiss
            if (o.burst && band && !isVoiced && afterQuiet)
                for (k in 1..6) for ((f, x) in listOf("R1" to 99, "L1" to 99, "R2" to o.burstDecay, "L2" to 0, "R3" to 99, "L3" to 0)) op(v, k, f, x)
            soften(v, notePitch != null)
            prevVoiced = notePitch != null
            notes += PlanNote(v, key, n * HOP.toDouble(), if (o.stutter != 0.0) o.hop * o.stutter else o.hop + o.overlap, notePitch)
            n += step
        }
        return notes
    }

    /** Notes -> timed MIDI: param diffs right after the previous note-on, alternating octave keys. */
    fun toEvents(notes: List<PlanNote>, ch: Int = 0, lead: Double = 40.0, msPerParam: Double = 0.17,
                 fx: List<IntArray>? = null, fxCh: Int = 1, sync: Boolean = true): List<Ev> {
        val ev = ArrayList<Ev>()
        fx?.forEach { (c, v) -> ev += Ev(0.0, intArrayOf(0xB0 or fxCh, c, v)) }
        var dev: IntArray? = null
        var flip = 0; var lastOn = -1e9; var lastVoicedOn: Double? = null; var lastPeriod = 0.0
        for (nt in notes) {
            flip = flip xor 1
            val key = nt.key + 12 * flip; val v = if (flip == 1) octaveUp(nt.v) else nt.v
            val want = IntArray(156).also { v.copyInto(it, 0, 0, 155); it[144] = 24; it[155] = 63 }
            val start = nt.start + lead
            val at = max(lastOn + 1, start - 15)
            var k = 0
            for (p in 0..155) {
                if (p in 145..154) continue
                if (dev != null && dev[p] == want[p]) continue
                ev += Ev(at + k * msPerParam, Dx7.paramSysex(p, want[p], ch)); k++
            }
            dev = want
            var on = max(start, at + k * msPerParam + 0.5)
            // pitch-synchronous onset: a whole number of periods after the previous voiced note (phase-aligned overlap)
            if (sync && nt.pitch != null && lastVoicedOn != null && lastPeriod > 0) {
                val earliest = at + k * msPerParam + 0.5
                val m = Math.round((on - lastVoicedOn!!) / lastPeriod)
                var t = lastVoicedOn!! + m * lastPeriod
                while (t < earliest) t += lastPeriod
                on = t
            }
            if (nt.pitch != null) { lastVoicedOn = on; lastPeriod = 1000 / midiHz(nt.pitch) } else lastVoicedOn = null
            ev += Ev(on, intArrayOf(0x90 or ch, key, 110))
            ev += Ev(on + nt.dur, intArrayOf(0x80 or ch, key, 0))
            lastOn = on
        }
        if (!fx.isNullOrEmpty() && ev.isNotEmpty()) {
            val end = ev.maxOf { it.t } + 400                      // after the tail (reverb, delay)
            fx.filter { it[0] % 4 == 0 }.forEach { ev += Ev(end, intArrayOf(0xB0 or fxCh, it[0], 0)) }
        }
        return ev.sortedBy { it.t }
    }

    // ---- characters (port of speech.js CHARACTERS): presets on top of the user's settings ----
    class Character(val label: String, val keyShift: Int = 0, val speedMul: Double = 1.0, val apply: (Opts) -> Opts = { it })
    val characters: Map<String, Character> = linkedMapOf(
        "natural" to Character("Natural"),
        "feminine" to Character("Feminine", 10) { it.copy(formant = 1.17, bright = 50) },
        "child" to Character("Child", 15, 1.08) { it.copy(formant = 1.3) },
        "giant" to Character("Giant", -10, 0.82) { it.copy(formant = 0.82) },
        "whisper" to Character("Whisper") { it.copy(whisper = true) },
        "robot" to Character("Robot") { it.copy(tone = "flat", design = "harmonic", bright = 66, hop = 30, release = 99) },
        "stutter" to Character("Glitch robot") { it.copy(tone = "flat", design = "harmonic", bright = 66, hop = 40, stutter = 0.5) },
        "cyborg" to Character("Cyborg (bell)") { it.copy(inharm = 41, bright = 62) },
        "alien" to Character("Alien", -6) { it.copy(formant = 1.35, vib = 45, bright = 60) },
        "singer" to Character("Singer", 0, 0.8) { it.copy(tone = "sing", melody = listOf(0, 4, 7, 12, 7, 4, 2, 0), vowelStretch = 1.8, vib = 10) },
        "radio" to Character("Old radio") { it.copy(fx = fxList(0 to 1, 1 to 1, 2 to 55, 3 to 4, 12 to 1, 13 to 30, 14 to 40, 15 to 50)) },
        "cathedral" to Character("Cathedral", 0, 0.9) { it.copy(fx = fxList(4 to 1, 5 to 1, 6 to 90, 7 to 50)) },
        "choir" to Character("Chorus") { it.copy(vib = 8, fx = fxList(16 to 1, 17 to 30, 18 to 80, 19 to 80, 4 to 1, 5 to 1, 6 to 70, 7 to 35)) },
        "echo" to Character("Echo") { it.copy(fx = fxList(8 to 1, 9 to 40, 10 to 35, 11 to 45)) },
        "uptalk" to Character("Uptalk (intonation)") { it.copy(style = "uptalk") },
        "singsong" to Character("Sing-song (intonation)") { it.copy(style = "singsong", accent = 5.0) },
        "drawl" to Character("Drawl (intonation)", 0, 0.85) { it.copy(vowelStretch = 1.5, accent = 2.0) },
        "excited" to Character("Excited", 3, 1.12) { it.copy(accent = 6.0) },
    )
    private fun fxList(vararg p: Pair<Int, Int>) = p.map { intArrayOf(it.first, it.second) }
    /** Effective options for a character: Pitch/Speed stay relative to the user's settings, the rest override. */
    fun character(name: String, o: Opts): Opts {
        val sm = smoothing(o)
        val c = characters[name] ?: return sm
        return c.apply(sm).copy(base = o.base + c.keyShift, speed = o.speed * c.speedMul)
    }
    /** Smoothness: soft = phase-aligned onsets + crossfaded voiced joins (default), sync = phase-aligned onsets only,
     *  smooth = crossfade everything + continuous pitch, off = plain chain. */
    fun smoothing(o: Opts): Opts = when (o.smooth) {
        "soft" -> o.copy(sync = true, softStart = 99.0, softRise = 80, overlap = 20.0, releaseRate = 75)
        "smooth" -> o.copy(sync = true, fine = true, glide = 99, attack = 80, overlap = 20.0, releaseRate = 75)
        "off" -> o.copy(sync = false)
        else -> o.copy(sync = true)
    }
}
