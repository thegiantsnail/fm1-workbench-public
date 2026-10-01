package com.fm1.workbench

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.fm1.workbench.data.Assets
import com.fm1.workbench.data.LibVoice

private val CH_COLORS = listOf(0xFFF0A23B, 0xFF5CC8C2, 0xFFE5484D, 0xFF8E7DFF, 0xFF46A758, 0xFFE879C9, 0xFFFFD166, 0xFF4EA8DE, 0xFFC0C0C0,
    0xFFFF8C42, 0xFF9BD35A, 0xFFB392F0, 0xFFF7768E, 0xFF7DCFFF, 0xFFE0AF68, 0xFFBB9AF7).map { Color(it) }

private fun mmss(ms: Double): String { val s = (ms / 1000).toInt().coerceAtLeast(0); return "%d:%02d".format(s / 60, s % 60) }

@Composable
fun PlayerScreen(vm: AppModel) {
    val rev = vm.revision
    val ctx = LocalContext.current
    val p = vm.player
    var pos by remember { mutableFloatStateOf(0f) }
    var dragging by remember { mutableStateOf(false) }
    var pickFor by remember { mutableStateOf<Int?>(null) }
    val open = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            val name = uri.lastPathSegment?.substringAfterLast('/') ?: "file.mid"
            ctx.contentResolver.openInputStream(uri)?.use { vm.loadMidi(it.readBytes(), name) }
        }
    }
    LaunchedEffect(Unit) { while (true) { withFrameMillis { }; if (!dragging && p.duration > 0) pos = (p.position() / p.duration).toFloat() } }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = { open.launch(arrayOf("audio/midi", "audio/x-midi", "audio/mid", "*/*")) }) { Text("Open .mid") }
            OutlinedButton(onClick = vm::loadDemo) { Text("Demo song") }
        }
        val song = p.song
        if (song == null) { Text("Open a MIDI file, or try the demo song.", color = Dim); if (rev < 0) Text(""); return@Column }
        Text("${p.name} · ${song.notes.size} notes · ${song.chans.size} channels · ${song.bpm.toInt()} BPM · ${mmss(song.duration)}", color = Dim, fontSize = 12.sp)
        // transport
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = vm::togglePlayer, modifier = Modifier.height(48.dp)) { Text(if (p.playing) "■ Stop" else "▶ Play", fontSize = 16.sp) }
            OutlinedButton(onClick = { p.seek(0.0); vm.touched() }) { Text("⏮") }
            Switch(p.loop, { p.loop = it; vm.touched() }); Text("Loop")
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(mmss(pos * p.duration), fontFamily = FontFamily.Monospace)
            Slider(pos, { pos = it; dragging = true }, onValueChangeFinished = { dragging = false; p.seek(pos * p.duration); vm.touched() }, modifier = Modifier.weight(1f))
            Text(mmss(p.duration), fontFamily = FontFamily.Monospace)
        }
        // piano roll overview + playhead (tap to seek)
        Canvas(Modifier.fillMaxWidth().height(110.dp).clip(RoundedCornerShape(8.dp)).background(Panel2)
            .pointerInput(song) { detectTapGestures { o -> p.seek(o.x / size.width * p.duration); vm.touched() } }) {
            var lo = 127; var hi = 0
            for (n in song.notes) { if (n.note < lo) lo = n.note; if (n.note > hi) hi = n.note }
            val span = maxOf(12, hi - lo + 1)
            for (n in song.notes) {
                val on = p.chans[n.ch]?.on != false
                drawRect(CH_COLORS[n.ch].copy(alpha = if (on) 0.9f else 0.15f),
                    Offset((n.t / song.duration * size.width).toFloat(), size.height - (n.note - lo + 1f) / span * size.height),
                    Size(maxOf(2f, (n.dur!! / song.duration * size.width).toFloat()), maxOf(2f, size.height / span)))
            }
            drawRect(Color.White, Offset(pos * size.width - 1.5f, 0f), Size(3f, size.height))
        }
        // tempo / transpose / velocity
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Tempo", Modifier.width(80.dp)); Slider(p.tempoPct.toFloat(), { p.tempoPct = it.toInt(); vm.touched() }, valueRange = 25f..200f, modifier = Modifier.weight(1f))
            Text("${p.tempoPct}%", Modifier.width(56.dp), fontFamily = FontFamily.Monospace)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Velocity", Modifier.width(80.dp)); Slider(p.velPct.toFloat(), { p.velPct = it.toInt(); vm.touched() }, valueRange = 25f..200f, modifier = Modifier.weight(1f))
            Text("${p.velPct}%", Modifier.width(56.dp), fontFamily = FontFamily.Monospace)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Transpose ${if (p.transpose > 0) "+" else ""}${p.transpose}", Modifier.width(120.dp))
            OutlinedButton(onClick = { p.transpose--; vm.touched() }) { Text("−") }
            OutlinedButton(onClick = { p.transpose++; vm.touched() }) { Text("+") }
            Switch(p.passBend, { p.passBend = it; vm.touched() }); Text("Pitch bend", fontSize = 12.sp)
        }
        // channels
        Text("Channels", fontWeight = FontWeight.Bold, color = Accent)
        for ((ch, cs) in p.chans) {
            val info = song.chans[ch]!!
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(Panel).border(0.dp, Color.Transparent)
                .padding(start = 0.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(6.dp).height(64.dp).background(CH_COLORS[ch]))
                Checkbox(cs.on, { cs.on = it; vm.touched() })
                Column(Modifier.weight(1f)) {
                    Text("Ch ${ch + 1}" + (if (info.name.isNotEmpty()) " · ${info.name}" else ""), fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("${info.count} notes · ${noteName(info.lo)}–${noteName(info.hi)}" + (info.pc?.let { " · GM ${it + 1}" } ?: ""), fontSize = 11.sp, color = Dim)
                    Text(if (cs.kit) "→ drum kit (${vm.kitName})" else "→ " + (cs.voiceName ?: "whatever is loaded"), fontSize = 12.sp, color = Accent, fontFamily = FontFamily.Monospace)
                }
                Column(horizontalAlignment = Alignment.End) {
                    TextButton(onClick = { pickFor = ch }) { Text("Voice…") }
                    Row {
                        if (ch == 9) TextButton(onClick = { cs.kit = !cs.kit; vm.touched() }) { Text(if (cs.kit) "Kit ✓" else "Kit") }
                        TextButton(onClick = { cs.voice = vm.edit.copyOf(); cs.voiceName = com.fm1.workbench.core.Dx7.name(vm.edit).trim() + " (editor)"; cs.kit = false; vm.touched() }) { Text("Editor") }
                    }
                }
            }
        }
    }
    pickFor?.let { ch ->
        VoicePicker(vm, onDismiss = { pickFor = null }) { v ->
            p.chans[ch]?.let { it.voice = v.vced.copyOf(); it.voiceName = v.name; it.kit = false }
            pickFor = null; vm.touched()
        }
    }
}

/** Library search dialog: tap a row to audition, "Use" to choose. */
@Composable
fun VoicePicker(vm: AppModel, onDismiss: () -> Unit, onPick: (LibVoice) -> Unit) {
    var q by remember { mutableStateOf("") }
    var cat by remember { mutableStateOf("") }
    val all = remember(vm.library, vm.myVoices) { vm.myVoices + vm.library }
    val shown = remember(q, cat, all) {
        val ql = q.trim().lowercase()
        all.filter { v -> (ql.isEmpty() || v.lname.contains(ql)) && (cat.isEmpty() || (if (cat == "@drums") v.tags.any { it in Assets.DRUM_TAGS } else cat in v.tags)) }.take(400)
    }
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(12.dp), color = Panel, modifier = Modifier.fillMaxWidth().fillMaxHeight(0.85f)) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(q, { q = it }, label = { Text("Search voices") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("" to "All", "bass" to "Bass", "keys" to "Keys", "pad" to "Pad", "lead" to "Lead", "@drums" to "Drums").forEach { (id, l) ->
                        FilterChip(cat == id, { cat = id }, label = { Text(l, fontSize = 11.sp) })
                    }
                }
                LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    items(shown, key = { it.id }) { v ->
                        Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).background(Panel2).clickable { vm.audition(v.vced) }
                            .padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(v.name, fontFamily = FontFamily.Monospace)
                                Text(v.bank.substringAfterLast('/'), fontSize = 10.sp, color = Dim, maxLines = 1)
                            }
                            TextButton(onClick = { onPick(v) }) { Text("Use", color = Accent2) }
                        }
                    }
                }
                TextButton(onClick = onDismiss, modifier = Modifier.align(Alignment.End)) { Text("Close") }
            }
        }
    }
}
