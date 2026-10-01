package com.fm1.workbench

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.fm1.workbench.core.DrumMacros
import com.fm1.workbench.core.MAX_STEPS
import com.fm1.workbench.core.RATES
import com.fm1.workbench.core.Step

/** Tap modes for the step grid: how a tap changes a step. Value modes cycle through a few useful values. */
private enum class StepMode(val label: String) { TOGGLE("On/off"), ACCENT("Accent"), SLIDE("Slide"), GATE("Gate"), VEL("Velocity"), CHANCE("Chance"), RATCHET("Ratchet"), PITCH("Pitch") }

private fun <T> cycle(values: List<T>, cur: T): T = values[(values.indexOf(cur) + 1) % values.size]

private fun applyMode(st: Step, mode: StepMode) {
    when (mode) {
        StepMode.TOGGLE -> st.on = !st.on
        StepMode.ACCENT -> { st.acc = !st.acc; if (st.acc) st.on = true }
        StepMode.SLIDE -> { st.slide = !st.slide; if (st.slide) st.on = true }
        StepMode.GATE -> { st.gate = cycle(listOf(null, 25, 50, 75, 100), st.gate); st.on = true }
        StepMode.VEL -> { st.vel = cycle(listOf(100, 70, 40, 127), st.vel).let { if (it !in listOf(100, 70, 40, 127)) 100 else it }; st.on = true }
        StepMode.CHANCE -> { st.prob = cycle(listOf(100, 75, 50, 25), st.prob).let { if (it !in listOf(100, 75, 50, 25)) 100 else it }; st.on = true }
        StepMode.RATCHET -> { st.rat = cycle(listOf(1, 2, 3, 4), st.rat); st.on = true }
        StepMode.PITCH -> { st.pitch = cycle(listOf(0, 5, 7, 12, -12), st.pitch).let { if (it !in listOf(0, 5, 7, 12, -12)) 0 else it }; st.on = true }
    }
}

private fun stepLabel(st: Step, i: Int): String {
    if (!st.on) return "${i + 1}"
    val bits = buildList {
        if (st.acc) add(">"); if (st.slide) add("~")
        st.gate?.let { add("${it}%") }; if (st.rat > 1) add("×${st.rat}"); if (st.pitch != 0) add((if (st.pitch > 0) "+" else "") + st.pitch)
        if (st.prob < 100) add("${st.prob}?")
    }
    return bits.joinToString(" ").ifEmpty { "${i + 1}" }
}

@Composable
fun DrumsScreen(vm: AppModel, toEditor: () -> Unit) {
    val rev = vm.revision
    val ctx = LocalContext.current
    var page by remember { mutableIntStateOf(0) }
    var mode by remember { mutableStateOf(StepMode.TOGGLE) }
    var picked by remember { mutableStateOf<Pair<Int, Int>?>(null) }     // (track, step) waiting to be moved/copied
    var copyMode by remember { mutableStateOf(false) }
    var step by remember { mutableIntStateOf(-1) }
    val flash = remember { mutableStateMapOf<Int, Long>() }
    val importer = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) ctx.contentResolver.openInputStream(uri)?.use { vm.importPattern(it.readBytes().decodeToString(), uri.lastPathSegment ?: "pattern") }
    }
    LaunchedEffect(Unit) {
        while (true) {
            withFrameMillis { }
            val now = vm.midi.clock()
            synchronized(vm.seq.marks) { while (vm.seq.marks.isNotEmpty() && vm.seq.marks.first().t <= now) step = vm.seq.marks.removeFirst().k }
            synchronized(vm.seq.flashes) { while (vm.seq.flashes.isNotEmpty() && vm.seq.flashes.first().first <= now) flash[vm.seq.flashes.removeFirst().second] = System.currentTimeMillis() }
            if (!vm.seq.playing) step = -1
        }
    }
    val p = vm.pattern
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        // transport
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = vm::togglePlay, modifier = Modifier.height(48.dp)) { Text(if (vm.seq.playing) "■ Stop" else "▶ Play", fontSize = 16.sp) }
            OutlinedButton(onClick = { vm.setBpm(p.bpm - 1) }) { Text("−") }
            Text("${p.bpm} BPM", fontFamily = FontFamily.Monospace)
            OutlinedButton(onClick = { vm.setBpm(p.bpm + 1) }) { Text("+") }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            var rateOpen by remember { mutableStateOf(false) }
            Box {
                OutlinedButton(onClick = { rateOpen = true }) { Text("Rate ${p.rate} ▾") }
                DropdownMenu(rateOpen, { rateOpen = false }) { RATES.keys.forEach { r -> DropdownMenuItem(text = { Text(r) }, onClick = { p.rate = r; rateOpen = false; vm.touched() }) } }
            }
            Text("Length ${p.len}", fontSize = 13.sp)
            OutlinedButton(onClick = { p.len = (p.len - 1).coerceAtLeast(1); vm.touched() }, contentPadding = PaddingValues(10.dp, 0.dp)) { Text("−") }
            OutlinedButton(onClick = { p.len = (p.len + 1).coerceAtMost(MAX_STEPS); vm.touched() }, contentPadding = PaddingValues(10.dp, 0.dp)) { Text("+") }
        }
        KitPicker(vm)
        // pads
        val tracks = p.tracks
        for (row in tracks.indices.chunked(3)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for (ti in row) {
                    val t = tracks[ti]
                    val lit = (flash[ti] ?: 0L) > System.currentTimeMillis() - 150
                    Column(Modifier.weight(1f).height(78.dp).clip(RoundedCornerShape(10.dp)).background(if (lit) Accent else Panel2)
                        .border(2.dp, if (ti == vm.activeTrack) Accent2 else Color.Transparent, RoundedCornerShape(10.dp))
                        .pointerInput(ti) { detectTapGestures(onPress = { vm.activeTrack = ti; vm.hitPad(ti); vm.touched() }) }
                        .padding(8.dp)) {
                        Text(t.name, fontWeight = FontWeight.Bold, color = if (lit) Color.Black else Color.White)
                        Text(t.voices.firstOrNull()?.name ?: "no voice", fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                            color = if (lit) Color.Black else Dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
        // step editing
        val tr = tracks.getOrNull(vm.activeTrack) ?: return@Column
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("${tr.name}", fontWeight = FontWeight.Bold)
            Text("track length ${tr.len}", color = Dim, fontSize = 12.sp)
            OutlinedButton(onClick = { tr.len = (tr.len - 1).coerceAtLeast(1); vm.touched() }, contentPadding = PaddingValues(10.dp, 0.dp)) { Text("−") }
            OutlinedButton(onClick = { tr.len = (tr.len + 1).coerceAtMost(MAX_STEPS); vm.touched() }, contentPadding = PaddingValues(10.dp, 0.dp)) { Text("+") }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            (0 until MAX_STEPS / 16).forEach { pg -> FilterChip(page == pg, { page = pg }, label = { Text("${pg * 16 + 1}–${pg * 16 + 16}") }) }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            StepMode.entries.forEach { m -> FilterChip(mode == m, { mode = m }, label = { Text(m.label) }) }
        }
        Text(picked?.let { (pt, ps) -> "Step ${ps + 1} of ${tracks[pt].name} picked up — tap a step (any track: switch pads first) to ${if (copyMode) "copy" else "move"} it" }
            ?: "Tap = ${mode.label.lowercase()} · long-press = pick up a step to move/copy it", color = if (picked != null) Accent2 else Dim, fontSize = 12.sp)
        for (r in 0 until 4) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (c in 0 until 4) {
                    val i = page * 16 + r * 4 + c
                    val st = tr.steps[i]
                    val now = step >= 0 && step % tr.len == i
                    val isPicked = picked == vm.activeTrack to i
                    Box(Modifier.weight(1f).aspectRatio(1.3f).clip(RoundedCornerShape(8.dp))
                        .background(when { i >= tr.len -> Panel; st.on -> Accent.copy(alpha = 0.35f + 0.65f * (if (st.acc) 127 else st.vel) / 127f); else -> Panel2 })
                        .border(if (now || isPicked) 3.dp else if (c == 0) 1.dp else 0.dp,
                            if (isPicked) Accent2 else if (now) Color.White else Dim, RoundedCornerShape(8.dp))
                        .pointerInput(vm.activeTrack, i, mode, picked, copyMode, rev) {
                            detectTapGestures(
                                onLongPress = { picked = vm.activeTrack to i },
                                onTap = {
                                    val pk = picked
                                    if (pk != null) {
                                        val (pt, ps) = pk
                                        if (pt != vm.activeTrack || ps != i) {
                                            tracks[vm.activeTrack].steps[i] = tracks[pt].steps[ps].copy()
                                            if (!copyMode) tracks[pt].steps[ps] = Step()
                                        }
                                        picked = null; vm.touched()
                                    } else if (i < tr.len) { applyMode(st, mode); vm.touched() }
                                })
                        }, contentAlignment = Alignment.Center) {
                        Text(stepLabel(st, i), fontSize = 11.sp, color = if (st.on) Color.Black else Dim, maxLines = 2)
                    }
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(copyMode, { copyMode = it }); Text("  Long-press + tap copies (instead of moving)", fontSize = 12.sp, modifier = Modifier.weight(1f))
        }
        // pattern slots + chain
        Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Patterns", fontWeight = FontWeight.Bold, color = Accent)
                Text("Tap a slot to load it · long-press to save the current pattern into it", color = Dim, fontSize = 12.sp)
                for (row in (1..16).chunked(8)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        for (n in row) {
                            val filled = vm.slotFilled(n) && rev >= 0
                            val playingNow = vm.chainOn && vm.seq.playing && vm.chainSlots.getOrNull(vm.chainPos) == n
                            Box(Modifier.weight(1f).height(40.dp).clip(RoundedCornerShape(6.dp))
                                .background(if (playingNow) Accent else if (filled) Accent2.copy(alpha = 0.35f) else Panel2)
                                .pointerInput(n) { detectTapGestures(onTap = { vm.loadSlot(n) }, onLongPress = { vm.saveSlot(n) }) },
                                contentAlignment = Alignment.Center) { Text("$n", fontSize = 13.sp, color = if (playingNow) Color.Black else Color.White) }
                        }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Switch(vm.chainOn, { vm.chainOn = it })
                    Text("Chain", modifier = Modifier.width(50.dp))
                    var text by remember { mutableStateOf(vm.chainSlots.joinToString(" ")) }
                    OutlinedTextField(text, { s -> text = s; vm.chainSlots = s.split(Regex("[\\s,]+")).mapNotNull { it.toIntOrNull() }.filter { it in 1..16 } },
                        label = { Text("slots, e.g. 1 2 2 3") }, singleLine = true, modifier = Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { val f = vm.exportPattern(); vm.toast("Exported ${f.name} (opens in the web app too)") }) { Text("Export .json") }
                    OutlinedButton(onClick = { importer.launch(arrayOf("application/json", "text/plain", "*/*")) }) { Text("Import .json") }
                }
            }
        }
        // macros for the active track's first voice
        tr.voices.firstOrNull()?.let { v ->
            Text("Shape ${v.name}", fontWeight = FontWeight.Bold, color = Accent)
            v.measured?.get("level_db")?.let { Text("measured on your FM-1: %.1f dBFS, decay %.0f ms".format(it, v.measured!!["decay_ms"]), color = Dim, fontSize = 11.sp) }
            for (d in DrumMacros.DEFS) {
                val cur = (v.macros[d.id] ?: d.keep ?: 0)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(d.label, Modifier.width(88.dp), fontSize = 13.sp)
                    Slider(value = cur.toFloat(), valueRange = d.min.toFloat()..d.max.toFloat(), steps = d.max - d.min - 1,
                        onValueChange = { x -> val n = x.toInt(); v.macros[d.id] = if (d.keep != null && n == d.keep) null else n; vm.touched() },
                        onValueChangeFinished = { vm.hitPad(vm.activeTrack) }, modifier = Modifier.weight(1f))
                    Text(DrumMacros.format(d.id, cur), Modifier.width(92.dp), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { v.macros.clear(); vm.touched(); vm.hitPad(vm.activeTrack) }) { Text("Reset macros") }
                OutlinedButton(onClick = { vm.editTrackVoice(tr, v); toEditor() }) { Text("✎ Full editor") }
            }
        }
    }
}

@Composable
fun KitPicker(vm: AppModel) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }, enabled = vm.kits.isNotEmpty()) { Text("Kit: ${vm.kitName}  ▾") }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            vm.kits.forEach { k -> DropdownMenuItem(text = { Text("${k.name} (${k.tracks.size})") }, onClick = { vm.applyKit(k); open = false }) }
        }
    }
}
