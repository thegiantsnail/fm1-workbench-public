package com.fm1.workbench

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
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
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat

/** Speech: type text (or record your voice) and the FM-1 — hardware or the Software FM-1 — speaks it. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SpeechScreen(vm: AppModel) {
    LaunchedEffect(Unit) { vm.loadSpeech() }
    val ctx = LocalContext.current
    var hasMic by remember { mutableStateOf(ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { hasMic = it; if (it) vm.speechRecordToggle() }
    @Suppress("UNUSED_VARIABLE") val rev = vm.revision
    val o = vm.speechOpts
    fun rebuild() { vm.speechBuild() }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FilterChip(selected = vm.speechSource == "text", onClick = { vm.speechSource = "text"; rebuild() }, label = { Text("Type") })
            FilterChip(selected = vm.speechSource == "rec", onClick = { vm.speechSource = "rec"; rebuild() }, label = { Text("Recording") })
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { if (hasMic) vm.speechRecordToggle() else ask.launch(Manifest.permission.RECORD_AUDIO) },
                colors = if (vm.speechRecording) ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFFE5484D)) else ButtonDefaults.outlinedButtonColors()) {
                Text(if (vm.speechRecording) "■ Stop" else "● Record")
            }
        }
        vm.speechRecInfo?.let { if (vm.speechSource == "rec" || vm.speechRecording) Text(it, color = Dim, fontSize = 12.sp) }
        if (vm.speechSource == "text") {
            OutlinedTextField(vm.speechText, { vm.speechText = it }, Modifier.fillMaxWidth(), minLines = 2, maxLines = 5,
                label = { Text("Text to speak") })
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.speak() }, enabled = vm.speech != null) {
                Text(if (vm.speechPlayer.playing) "■ Stop" else "▶ Speak", fontWeight = FontWeight.Bold)
            }
            OutlinedButton(onClick = { vm.speechSaveWav() }, enabled = vm.speech != null) { Text("Save WAV") }
            if (vm.speechLoading) { CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp); Text("Loading voice…", color = Dim, fontSize = 12.sp) }
        }
        vm.speech?.let { sp ->
            var open by remember { mutableStateOf(false) }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Character", color = Dim, fontSize = 12.sp)
                Box {
                    OutlinedButton(onClick = { open = true }) { Text((sp.characters[vm.speechCharacter]?.label ?: "Natural") + "  ▾") }
                    DropdownMenu(open, { open = false }) {
                        for ((k, c) in sp.characters) DropdownMenuItem(text = { Text((if (k == vm.speechCharacter) "● " else "○ ") + c.label) },
                            onClick = { vm.speechCharacter = k; open = false; rebuild() })
                    }
                }
            }
        }
        FlowRow(verticalArrangement = Arrangement.Center, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Voice", color = Dim, fontSize = 12.sp, modifier = Modifier.align(Alignment.CenterVertically))
            for ((k, l) in listOf("fixedfm" to "Exact formants", "harmonic" to "Harmonic", "lebrun" to "Pairs", "sine" to "Sine-wave")) {
                FilterChip(selected = o.design == k, onClick = { vm.speechOpts = o.copy(design = k); rebuild() }, label = { Text(l) })
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Smoothness", color = Dim, fontSize = 12.sp)
            for ((k, l) in listOf("soft" to "Soft joins", "sync" to "Phase-locked", "smooth" to "Smooth", "off" to "Raw")) {
                FilterChip(selected = o.smooth == k, onClick = { vm.speechOpts = o.copy(smooth = k); rebuild() }, label = { Text(l) })
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Diphthongs", color = Dim, fontSize = 12.sp)
            val cur = if (o.diph != "sweep") "off" else if (o.diphAll) "all" else "glide"
            for ((k, l) in listOf("glide" to "One-note glide", "all" to "All vowels", "off" to "Note chain")) {
                FilterChip(selected = cur == k, onClick = {
                    vm.speechOpts = o.copy(diph = if (k == "off") "off" else "sweep", diphAll = k == "all", diphEdge = 0.15); rebuild()
                }, label = { Text(l) })
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Intonation", color = Dim, fontSize = 12.sp)
            for ((k, l) in listOf("grammar" to "Grammar", "flat" to "Flat", "follow" to "Follow rec.")) {
                FilterChip(selected = o.tone == k, onClick = { vm.speechOpts = o.copy(tone = k); rebuild() }, label = { Text(l) })
            }
        }
        val names = listOf("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        Knob("Pitch", o.base.toFloat(), 36f..60f, names[o.base % 12] + (o.base / 12 - 1)) { vm.speechOpts = o.copy(base = it.toInt()); rebuild() }
        Knob("Speed", (o.speed * 100).toFloat(), 60f..160f, "${(o.speed * 100).toInt()}%") { vm.speechOpts = o.copy(speed = it.toInt() / 100.0); rebuild() }
        Knob("Accent", o.accent.toFloat(), 0f..8f, if (o.accent == 0.0) "off" else "±${o.accent.toInt()} st") { vm.speechOpts = o.copy(accent = it.toInt().toDouble()); rebuild() }
        Knob("Brightness", o.bright.toFloat(), 30f..75f, "${o.bright}") { vm.speechOpts = o.copy(bright = it.toInt()); rebuild() }

        vm.speechResult?.let { r ->
            SpeechView(r.frames, Modifier.fillMaxWidth().height(120.dp).clip(RoundedCornerShape(8.dp)).background(Panel2))
            if (r.sentences.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(5.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                for (s in r.sentences) {
                    for (w in s.words) {
                        Text(w.text + if (w.brk) "," else "", fontSize = 14.sp,
                            color = if (w.weight > 0) MaterialTheme.colorScheme.onSurface else Dim,
                            modifier = Modifier.clip(RoundedCornerShape(4.dp)).background(Accent.copy(alpha = (w.weight * 0.3).toFloat())).padding(horizontal = 5.dp, vertical = 1.dp))
                    }
                    Text(s.end + " " + mapOf("stmt" to "↘", "excl" to "↘!", "yn" to "↗", "wh" to "↘?")[s.type], color = Accent, fontWeight = FontWeight.Bold)
                }
            }
            for (n in r.notes) {
                val c = when (n.k) { "guess", "style" -> Color(0xFFE5484D); "tone", "grammar" -> Accent; else -> Accent2 }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(mapOf("number" to "NUMBER", "abbr" to "ABBREV.", "spell" to "SPELLED", "guess" to "GUESSED", "tone" to "INTONATION",
                        "style" to "STYLE", "grammar" to "GRAMMAR")[n.k] ?: n.k.uppercase(), color = c, fontSize = 10.sp, modifier = Modifier.width(78.dp))
                    Text(n.m, fontSize = 12.sp)
                }
            }
        }
        Text("Every 20 ms of speech is one FM-1 note with its own voice: three carriers on the vowel formants (algorithm 22), " +
            "or feedback noise for hiss. Typed text is stitched from sound units of a text-to-speech voice; a recording is analysed " +
            "directly. Intonation comes from the grammar: word classes pick the accents, and each phrase falls at a statement's " +
            "end and rises for yes/no questions and before commas.", color = Dim, fontSize = 11.sp)
    }
}

@Composable
private fun Knob(label: String, value: Float, range: ClosedFloatingPointRange<Float>, shown: String, set: (Float) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Dim, fontSize = 12.sp, modifier = Modifier.width(78.dp))
        Slider(value, set, valueRange = range, modifier = Modifier.weight(1f))
        Text(shown, color = Accent, fontSize = 12.sp, modifier = Modifier.width(56.dp))
    }
}

/** Energy (bars), formants F1-F3 (dots; unvoiced frames: centroid) and the pitch contour (orange). */
@Composable
private fun SpeechView(frames: List<com.fm1.workbench.core.Speech.Frame>, modifier: Modifier) {
    Canvas(modifier) {
        if (frames.isEmpty()) return@Canvas
        val w = size.width; val h = size.height; val n = frames.size
        val emax = frames.maxOf { it.e }
        val x = { k: Int -> k * w / n }
        val fy = { f: Double -> (h - 6 - (f / 4000).coerceIn(0.0, 1.0) * (h - 12)).toFloat() }
        frames.forEachIndexed { k, f ->
            val bh = (((f.e - emax + 50) / 50).coerceIn(0.0, 1.0) * (h - 12)).toFloat()
            drawRect(Color(0xFF2E333D), Offset(x(k), h - 6 - bh), Size(maxOf(1f, w / n), bh))
        }
        frames.forEachIndexed { k, f ->
            if (f.e < emax - 42) return@forEachIndexed
            if (f.v != 0) f.F.forEach { drawRect(Accent2, Offset(x(k), fy(it) - 1), Size(2f, 2f)) }
            else drawRect(Dim, Offset(x(k), fy(f.cent) - 1), Size(2f, 2f))
        }
        val p = Path()
        frames.forEachIndexed { k, f -> val y = (h / 2 - f.semi * 4).toFloat(); if (k == 0) p.moveTo(x(k), y) else p.lineTo(x(k), y) }
        drawPath(p, Accent, style = Stroke(width = 2f))
    }
}
