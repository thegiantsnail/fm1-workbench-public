package com.fm1.workbench

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.fm1.workbench.core.Dx7
import com.fm1.workbench.core.VoiceGen

private val NOTE_NAMES = listOf("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
internal fun noteName(n: Int) = NOTE_NAMES[n % 12] + (n / 12 - 1)

/** How each parameter is shown (label, formatter). */
private val OP_LABELS = mapOf("OL" to "Level", "FC" to "Coarse", "FF" to "Fine", "DT" to "Detune", "MODE" to "Fixed", "KVS" to "Vel sens",
    "AMS" to "AM sens", "RS" to "Rate scl", "R1" to "R1", "R2" to "R2", "R3" to "R3", "R4" to "R4", "L1" to "L1", "L2" to "L2", "L3" to "L3", "L4" to "L4",
    "BP" to "Break pt", "LD" to "L depth", "RD" to "R depth", "LC" to "L curve", "RC" to "R curve")
private val OP_ORDER = listOf("OL", "FC", "FF", "DT", "MODE", "KVS", "AMS", "RS", "R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4", "BP", "LD", "RD", "LC", "RC")
private val G_LAYOUT = listOf("FB" to "Feedback", "TRNP" to "Transpose", "OKS" to "Osc sync", "LFW" to "LFO wave", "LFS" to "LFO speed", "LFD" to "LFO delay",
    "LPMD" to "LFO pitch", "LAMD" to "LFO amp", "LPMS" to "Pitch sens", "LFKS" to "LFO sync", "PR1" to "PEG R1", "PR2" to "PEG R2", "PR3" to "PEG R3",
    "PR4" to "PEG R4", "PL1" to "PEG L1", "PL2" to "PEG L2", "PL3" to "PEG L3", "PL4" to "PEG L4")

private fun fmt(field: String, v: Int): String = when (field) {
    "DT" -> (if (v - 7 > 0) "+" else "") + (v - 7)
    "MODE" -> if (v == 1) "fixed" else "ratio"
    "BP" -> noteName(v + 21)
    "LC", "RC" -> listOf("-LIN", "-EXP", "+EXP", "+LIN")[v]
    "TRNP" -> noteName(v + 36)
    "OKS", "LFKS" -> if (v == 1) "on" else "off"
    "LFW" -> listOf("TRI", "SAW↓", "SAW↑", "SQR", "SINE", "S/H")[v]
    else -> v.toString()
}

@Composable
fun EditorScreen(vm: AppModel) {
    val rev = vm.revision
    val v = vm.edit
    var style by remember { mutableStateOf("any") }
    var amount by remember { mutableFloatStateOf(12f) }
    var open by remember { mutableIntStateOf(1) }
    var morph by remember { mutableFloatStateOf(0f) }
    var octave by remember { mutableIntStateOf(4) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        // name + actions
        var name by remember(rev) { mutableStateOf(Dx7.name(v).trimEnd()) }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(name, { s -> name = s.take(10); Dx7.name(v).let { _ -> Dx7.setName(v, name); for (i in 0 until 10) vm.engine.setParam(145 + i, v[145 + i]) } },
                label = { Text("Name") }, singleLine = true, modifier = Modifier.width(180.dp), textStyle = TextStyle(fontFamily = FontFamily.Monospace))
            Column { Text("Algorithm ${v[Dx7.ALG] + 1}", fontWeight = FontWeight.Bold); if (rev < 0) Text("") }
        }
        vm.editTarget?.let { t ->
            Button(onClick = vm::saveEditToTarget, colors = ButtonDefaults.buttonColors(containerColor = Accent2)) { Text("Save to ${t.first}") }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton(onClick = { vm.sendEdit(); vm.audition(v) }) { Text("▶ Play") }
            OutlinedButton(onClick = vm::saveEditToMine) { Text("Save to My patches") }
            OutlinedButton(onClick = { val f = vm.exportEditSyx(); vm.toast("Exported ${f.name}") }) { Text("Export .syx") }
            OutlinedButton(onClick = { vm.pushUndo(); vm.setEdit(Dx7.initVoice(), keepTarget = true) }) { Text("Init") }
            OutlinedButton(onClick = vm::undo, enabled = vm.canUndo) { Text("Undo") }
        }
        // test keyboard (one octave, press/release)
        Row(Modifier.fillMaxWidth().height(64.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            OutlinedButton(onClick = { octave = (octave - 1).coerceAtLeast(1) }, contentPadding = PaddingValues(0.dp), modifier = Modifier.width(40.dp).fillMaxHeight()) { Text("−") }
            for (i in 0 until 13) {
                val n = 12 * (octave + 1) + i
                val black = i % 12 in setOf(1, 3, 6, 8, 10)
                Box(Modifier.weight(1f).fillMaxHeight().clip(RoundedCornerShape(4.dp)).background(if (black) Color(0xFF2A2E36) else Color(0xFFDADDE2))
                    .pointerInput(n) {
                        detectTapGestures(onPress = {
                            val id = vm.engine.note(n, 100, vm.midi.clock(), null)
                            vm.looper.noteOn(n, 100, v.copyOf())
                            tryAwaitRelease()
                            vm.engine.noteOff(n, null, id); vm.looper.noteOff(n)
                        })
                    }, contentAlignment = Alignment.BottomCenter) {
                    if (i % 12 == 0) Text(noteName(n), fontSize = 9.sp, color = Color.DarkGray)
                }
            }
            OutlinedButton(onClick = { octave = (octave + 1).coerceAtMost(7) }, contentPadding = PaddingValues(0.dp), modifier = Modifier.width(40.dp).fillMaxHeight()) { Text("+") }
        }
        // algorithm
        Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
            Row(Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    OutlinedButton(onClick = { vm.pushUndo(); vm.setParam(Dx7.ALG, (v[Dx7.ALG] + 31) % 32) }) { Text("◀") }
                    Text("${v[Dx7.ALG] + 1}", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                    OutlinedButton(onClick = { vm.pushUndo(); vm.setParam(Dx7.ALG, (v[Dx7.ALG] + 1) % 32) }) { Text("▶") }
                }
                AlgorithmDiagram(v[Dx7.ALG], vm.opOn, Modifier.weight(1f).height(170.dp))
            }
        }
        // generate + morph
        Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    VoiceGen.STYLES.forEach { (id, label) -> FilterChip(style == id, { style = id }, label = { Text(label) }) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Button(onClick = { vm.randomize(style) }) { Text("Randomize") }
                    Button(onClick = { vm.mutate(amount.toInt()) }) { Text("Mutate") }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {                 // own row: squeezed beside the buttons on phones
                    Text("Mutate amount", Modifier.width(120.dp), fontSize = 13.sp, color = Dim)
                    Slider(amount, { amount = it }, valueRange = 1f..50f, modifier = Modifier.weight(1f))
                    Text("${amount.toInt()}%", Modifier.width(48.dp), fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedButton(onClick = { vm.morphA = v.copyOf(); vm.touched() }) { Text("Set A") }
                    OutlinedButton(onClick = { vm.morphB = v.copyOf(); vm.touched() }) { Text("Set B") }
                    Text("A", color = Dim)
                    Slider(morph, { morph = it; vm.morph(it) }, enabled = vm.morphA != null && vm.morphB != null,
                        onValueChangeFinished = { vm.audition(vm.edit) }, modifier = Modifier.weight(1f))
                    Text("B", color = Dim)
                }
            }
        }
        // global parameters
        Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
            Column(Modifier.padding(10.dp)) {
                Text("Global", fontWeight = FontWeight.Bold, color = Accent)
                for ((f, label) in G_LAYOUT) ParamRow(vm, Dx7.paramIndex(null, f), label) { fmt(f, it) }
            }
        }
        // operators
        val alg = Dx7.ALGS[v[Dx7.ALG]]
        for (n in 1..6) {
            val car = n in alg.carriers
            Card(colors = CardDefaults.cardColors(containerColor = if (vm.opOn[n - 1]) Panel else Color(0xFF15171B))) {
                Column(Modifier.padding(10.dp)) {
                    Row(Modifier.fillMaxWidth().clickable { open = if (open == n) 0 else n }, verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(vm.opOn[n - 1], { vm.toggleOp(n) })
                        Text("OP$n", fontWeight = FontWeight.Bold, color = if (car) Accent2 else Color.White)
                        Text(if (car) "  carrier" else "  mod → " + alg.edges.filter { it.first == n }.joinToString(",") { it.second.toString() }, color = Dim, fontSize = 12.sp)
                        Spacer(Modifier.weight(1f))
                        Text("L ${v[Dx7.paramIndex(n, "OL")]}  ${Dx7.freqLabel(v, n)}", fontFamily = FontFamily.Monospace, color = Accent, fontSize = 12.sp)
                        Text(if (open == n) "  ▴" else "  ▾")
                    }
                    if (open == n) {
                        EnvelopeGraph(v, n, Modifier.fillMaxWidth().height(56.dp))
                        for (f in OP_ORDER) ParamRow(vm, Dx7.paramIndex(n, f), OP_LABELS[f]!!) { fmt(f, it) }
                    }
                }
            }
        }
    }
}

@Composable
private fun ParamRow(vm: AppModel, i: Int, label: String, format: (Int) -> String) {
    val max = Dx7.maxOf(i)
    val cur = vm.edit[i]
    var started by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.width(84.dp), fontSize = 13.sp, color = Dim)
        Slider(cur.toFloat(), { x -> if (!started) { vm.pushUndo(); started = true }; vm.setParam(i, x.toInt()) },
            valueRange = 0f..max.toFloat(), steps = if (max <= 20) max - 1 else 0, onValueChangeFinished = { started = false }, modifier = Modifier.weight(1f))
        Text(format(cur), Modifier.width(56.dp), fontFamily = FontFamily.Monospace, fontSize = 12.sp)
    }
}

@Composable
private fun EnvelopeGraph(v: IntArray, n: Int, modifier: Modifier) {
    Canvas(modifier.clip(RoundedCornerShape(6.dp)).background(Panel2)) {
        val o = (6 - n) * 21
        fun seg(r: Int) = 6f + (99 - r) * 0.5f
        fun lv(l: Int) = size.height - 4 - l / 99f * (size.height - 8)
        val pts = mutableListOf(0f to lv(v[o + 7]))
        var t = 0f
        for (s in 0 until 3) { t += seg(v[o + s]); pts += t to lv(v[o + 4 + s]) }
        t += 30f; pts += t to lv(v[o + 6])
        t += seg(v[o + 3]); pts += t to lv(v[o + 7])
        val sx = (size.width - 8) / t
        for (i in 1 until pts.size) drawLine(Accent, Offset(4 + pts[i - 1].first * sx, pts[i - 1].second), Offset(4 + pts[i].first * sx, pts[i].second), 3f)
    }
}

/** DX7 algorithm diagram (port of drawAlg in app.js): carriers on the bottom row, modulators stacked above. */
@Composable
fun AlgorithmDiagram(algIndex: Int, opOn: BooleanArray, modifier: Modifier) {
    val tm = rememberTextMeasurer()
    Canvas(modifier) {
        val alg = Dx7.ALGS[algIndex]
        val pos = HashMap<Int, Double>(); val depth = HashMap<Int, Int>(); var x = 0
        fun mods(o: Int) = alg.edges.filter { it.second == o }.map { it.first }.sortedDescending()
        fun place(o: Int, d: Int) {
            if (o in pos) return
            depth[o] = d
            val ms = mods(o).filter { it !in pos }
            if (ms.isEmpty()) { pos[o] = (x++).toDouble(); return }
            val start = x
            ms.forEach { place(it, d + 1) }
            val xs = ms.mapNotNull { pos[it] }
            pos[o] = if (xs.isNotEmpty()) (xs.min() + xs.max()) / 2 else start.toDouble()
            if (x == start) x++
        }
        alg.carriers.forEach { place(it, 0) }
        val maxD = depth.values.max(); val cols = maxOf(x, 1)
        val bw = 34.dp.toPx(); val bh = 24.dp.toPx(); val W = size.width; val H = size.height
        val stepY = minOf(40.dp.toPx(), (H - 40.dp.toPx()) / (maxD + 1))
        fun cx(o: Int) = (W / (cols + 1) * (pos[o]!! + 1)).toFloat()
        fun cy(o: Int) = H - 20.dp.toPx() - depth[o]!! * stepY - bh / 2
        for ((f, t) in alg.edges) drawLine(Dim, Offset(cx(f), cy(f) + bh / 2), Offset(cx(t), cy(t) - bh / 2), 3f)
        val busY = H - 8.dp.toPx()
        drawLine(Accent2, Offset(cx(alg.carriers.first()) - 12, busY), Offset(cx(alg.carriers.last()) + 12, busY), 5f)
        for (c in alg.carriers) drawLine(Dim, Offset(cx(c), cy(c) + bh / 2), Offset(cx(c), busY), 3f)
        val fb = alg.fb
        drawRect(Accent, Offset(cx(fb) + bw / 2, cy(fb) - bh / 2 - 6), Size(8f, bh / 2 + 6), style = Stroke(3f))
        for (o in 1..6) {
            val car = o in alg.carriers
            drawRoundRect(if (opOn[o - 1]) Panel2 else Color.Transparent, Offset(cx(o) - bw / 2, cy(o) - bh / 2), Size(bw, bh), CornerRadius(6f))
            drawRoundRect(if (car) Accent2 else Dim, Offset(cx(o) - bw / 2, cy(o) - bh / 2), Size(bw, bh), CornerRadius(6f), style = Stroke(3f))
            val r = tm.measure("$o", TextStyle(color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold))
            drawText(r, topLeft = Offset(cx(o) - r.size.width / 2, cy(o) - r.size.height / 2))
        }
    }
}
