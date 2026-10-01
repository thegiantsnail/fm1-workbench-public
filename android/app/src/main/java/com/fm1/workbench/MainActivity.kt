package com.fm1.workbench

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
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
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.fm1.workbench.audio.AudioLooper
import com.fm1.workbench.core.DrumMacros
import com.fm1.workbench.core.Looper
import com.fm1.workbench.data.Assets
import java.io.File

internal val Accent = Color(0xFFF0A23B)
internal val Accent2 = Color(0xFF5CC8C2)
internal val Panel = Color(0xFF1A1D23)
internal val Panel2 = Color(0xFF22262E)
internal val Dim = Color(0xFF8B93A1)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Screen-off playback is handled by PlaybackService; its notification needs this permission on Android 13+.
        if (android.os.Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Accent, secondary = Accent2, background = Color(0xFF111317), surface = Panel)) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) { App(viewModel()) }
            }
        }
    }
}

@Composable
fun App(vm: AppModel) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val snack = remember { SnackbarHostState() }
    // Keep the screen awake while something plays with the app in front (power button still turns it off; playback
    // then continues in PlaybackService).
    val activity = LocalContext.current as? ComponentActivity
    val busy = vm.revision.let { vm.busy }
    LaunchedEffect(Unit) { while (true) { kotlinx.coroutines.delay(500); vm.touchIfBusyChanged() } }
    LaunchedEffect(busy) {
        if (busy) activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
    LaunchedEffect(vm.message) { vm.message?.let { snack.showSnackbar(it); vm.message = null } }
    Scaffold(
        snackbarHost = { SnackbarHost(snack) },
        topBar = {
            Row(Modifier.fillMaxWidth().background(Panel).statusBarsPadding().padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("◆ FM-1", fontWeight = FontWeight.Bold, color = Accent)
                val ok = vm.status != null
                Text(if (ok) vm.status!! else "FM-1 not connected (USB-C)", color = if (ok) Color(0xFF46A758) else Color(0xFFE5484D),
                    fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                if (!ok || vm.midi.usingSoft) TextButton(onClick = { vm.midi.scan() }) { Text("Scan") }
                SettingsMenu(vm)
                OutlinedButton(onClick = vm::panic) { Text("Panic") }
            }
        },
        bottomBar = {
            NavigationBar(containerColor = Panel) {
                listOf("🥁" to "Drums", "⟳" to "Looper", "✎" to "Editor", "▶" to "Player", "🗣" to "Speech", "☰" to "Library").forEachIndexed { i, (ic, label) ->
                    NavigationBarItem(selected = tab == i, onClick = { tab = i }, icon = { Text(ic, fontSize = 20.sp) }, label = { Text(label) })
                }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            val toEditor = { tab = 2 }
            when (tab) { 0 -> DrumsScreen(vm, toEditor); 1 -> LooperScreen(vm); 2 -> EditorScreen(vm); 3 -> PlayerScreen(vm); 4 -> SpeechScreen(vm); else -> LibraryScreen(vm, toEditor) }
        }
    }
}

/** Output (FM-1 / Software FM-1), firmware (M-VAVE / Baud Girl's FM-1+VA) and master volume (FM-1+VA, CC 7). */
@Composable
fun SettingsMenu(vm: AppModel) {
    var open by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { open = true }) { Text("⚙", fontSize = 18.sp) }
        DropdownMenu(open, { open = false }) {
            Text("Output", color = Dim, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 12.dp))
            for ((o, l) in listOf(com.fm1.workbench.midi.Fm1Midi.Output.AUTO to "Auto (FM-1 if connected)",
                    com.fm1.workbench.midi.Fm1Midi.Output.HARDWARE to "FM-1 only", com.fm1.workbench.midi.Fm1Midi.Output.SOFTWARE to "Software FM-1")) {
                DropdownMenuItem(text = { Text((if (vm.output == o) "● " else "○ ") + l) }, onClick = { vm.setOutputMode(o) })
            }
            HorizontalDivider()
            Text("Firmware", color = Dim, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 12.dp))
            for ((f, l) in listOf("stock" to "M-VAVE", "va" to "FM-1+VA (Baud Girl)")) {
                DropdownMenuItem(text = { Text((if (vm.firmware == f) "● " else "○ ") + l) }, onClick = { vm.setFirmwareMode(f) })
            }
            if (vm.firmware == "va") {
                HorizontalDivider()
                Text("Volume (CC 7): ${vm.volume}", color = Dim, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 12.dp))
                Slider(vm.volume.toFloat(), { vm.setMasterVolume(it.toInt()) }, valueRange = 0f..127f, modifier = Modifier.width(220.dp).padding(horizontal = 12.dp))
            }
        }
    }
}

// ---------------------------------------------------------------- Looper
@Composable
fun LooperScreen(vm: AppModel) {
    val ctx = LocalContext.current
    var lstate by remember { mutableStateOf(vm.looper.state) }
    var lpos by remember { mutableFloatStateOf(0f) }
    var astate by remember { mutableStateOf(vm.audioLooper.state) }
    var apos by remember { mutableFloatStateOf(0f) }
    var level by remember { mutableFloatStateOf(0f) }
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            withFrameMillis { }
            lstate = vm.looper.state; lpos = vm.looper.position().toFloat()
            astate = vm.audioLooper.state; apos = vm.audioLooper.position().toFloat(); level = vm.audioLooper.level
            tick++
        }
    }
    var hasMic by remember { mutableStateOf(ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { hasMic = it }
    var exported by remember { mutableStateOf<String?>(null) }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        // MIDI looper
        Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("MIDI looper", fontWeight = FontWeight.Bold, color = Accent)
                Text("Records notes from the FM-1's keys, the pads and library auditions — each with the voice it was played on — and replays them through the FM-1.",
                    fontSize = 12.sp, color = Dim)
                val lp = vm.looper
                Text("${lstate.name.lowercase()} · ${if (lp.lengthMs > 0) "%.2f s".format(lp.lengthMs / 1000) else "no loop"} · ${lp.layers} layer(s)",
                    fontFamily = FontFamily.Monospace)
                LinearProgressIndicator(progress = { lpos }, modifier = Modifier.fillMaxWidth().height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                    Button(onClick = vm::looperRecord, colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE5484D))) {
                        Text(when (lstate) { Looper.State.EMPTY -> "● Rec"; Looper.State.RECORDING -> "● Close loop"; Looper.State.OVERDUBBING -> "● End dub"; else -> "● Overdub" })
                    }
                    OutlinedButton(onClick = vm::looperPlay) { Text("▶") }
                    OutlinedButton(onClick = lp::stop) { Text("■") }
                    OutlinedButton(onClick = lp::undo) { Text("Undo") }
                    OutlinedButton(onClick = lp::clear) { Text("Clear") }
                }
                // one switch per row: side by side they overflow on phones with large display/text size
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(lp.syncBars, { lp.syncBars = it; tick++ }); Text("  Snap to bars (${vm.pattern.bpm} BPM)", Modifier.weight(1f))
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(lp.quantize, { lp.quantize = it; tick++ }); Text("  Quantize 16ths", Modifier.weight(1f))
                }
                // quick input pads
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    vm.pattern.tracks.take(6).forEachIndexed { ti, t ->
                        Box(Modifier.weight(1f).height(56.dp).clip(RoundedCornerShape(8.dp)).background(Panel2)
                            .pointerInput(ti) { detectTapGestures(onPress = { vm.hitPad(ti) }) }, contentAlignment = Alignment.Center) {
                            Text(t.name.take(5), fontSize = 11.sp)
                        }
                    }
                }
            }
        }
        // Audio looper
        Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Audio looper", fontWeight = FontWeight.Bold, color = Accent)
                Text("Records the FM-1's USB audio (effects included) and loops it on the phone, with overdub.", fontSize = 12.sp, color = Dim)
                if (!hasMic) {
                    Button(onClick = { ask.launch(Manifest.permission.RECORD_AUDIO) }) { Text("Allow audio recording") }
                } else {
                    val al = vm.audioLooper
                    Text("${astate.name.lowercase()} · ${if (al.lengthMs > 0) "%.2f s".format(al.lengthMs / 1000) else "no loop"} · input: ${al.inputName ?: "—"}",
                        fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                    LinearProgressIndicator(progress = { apos }, modifier = Modifier.fillMaxWidth().height(8.dp))
                    LinearProgressIndicator(progress = { level.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth().height(4.dp), color = Accent2)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                        Button(onClick = vm::audioRecord, colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE5484D))) {
                            Text(when (astate) { AudioLooper.State.EMPTY -> "● Rec"; AudioLooper.State.RECORDING -> "● Close loop"; AudioLooper.State.OVERDUBBING -> "● End dub"; else -> "● Overdub" })
                        }
                        OutlinedButton(onClick = vm::audioPlay) { Text("▶") }
                        OutlinedButton(onClick = al::stop) { Text("■") }
                        OutlinedButton(onClick = al::undo, enabled = al.canUndo) { Text("Undo") }
                        OutlinedButton(onClick = al::clear) { Text("Clear") }
                        OutlinedButton(onClick = {
                            val f = File(ctx.getExternalFilesDir(null), "fm1-loop-${System.currentTimeMillis()}.wav")
                            exported = al.exportWav(f).absolutePath
                        }, enabled = al.lengthMs > 0) { Text("Save WAV") }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Switch(al.syncBars, { al.syncBars = it; tick++ }); Text("  Snap to bars")
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Overdub latency", Modifier.width(120.dp), fontSize = 13.sp)
                        Slider(al.latencyMs.toFloat(), { al.latencyMs = it.toInt(); tick++ }, valueRange = 0f..200f, modifier = Modifier.weight(1f))
                        Text("${al.latencyMs} ms", fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                    }
                    exported?.let { Text("Saved: $it", fontSize = 11.sp, color = Dim) }
                }
            }
        }
        Text("tick $tick", fontSize = 1.sp, color = Color.Transparent)
    }
}

// ---------------------------------------------------------------- Library
@Composable
fun LibraryScreen(vm: AppModel, toEditor: () -> Unit) {
    var q by rememberSaveable { mutableStateOf("") }
    var cat by rememberSaveable { mutableStateOf("") }
    val cats = listOf("" to "All", "@drums" to "Drums", "kick" to "Kick", "snare" to "Snare", "hat" to "Hat", "tom" to "Tom", "perc" to "Perc",
        "bass" to "Bass", "keys" to "Keys", "pad" to "Pad", "bell" to "Bell", "brass" to "Brass", "strings" to "Strings", "lead" to "Lead")
    val filtered = remember(q, cat, vm.library, vm.myVoices) {
        val ql = q.trim().lowercase()
        (vm.myVoices + vm.library).filter { v ->
            (ql.isEmpty() || v.lname.contains(ql)) &&
                (cat.isEmpty() || (if (cat == "@drums") v.tags.any { it in Assets.DRUM_TAGS } else cat in v.tags))
        }
    }
    Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(q, { q = it }, label = { Text("Search ${vm.library.size} voices") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            cats.forEach { (id, label) -> FilterChip(cat == id, { cat = id }, label = { Text(label) }) }
        }
        val tr = vm.pattern.tracks.getOrNull(vm.activeTrack)
        Text(if (vm.loading) "Loading library…" else "${filtered.size} voices · tap = audition · ✎ = edit · + = add to ${tr?.name ?: "track"}", color = Dim, fontSize = 12.sp)
        LazyColumn(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            items(filtered.take(800), key = { it.id }) { v ->
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).background(Panel2).clickable { vm.audition(v.vced) }.padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(v.name, fontFamily = FontFamily.Monospace)
                        Text((v.tags.firstOrNull()?.let { "$it · " } ?: "") + v.bank.substringAfterLast('/'), fontSize = 11.sp, color = Dim,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    TextButton(onClick = { vm.setEdit(v.vced); toEditor() }) { Text("✎", fontSize = 18.sp, color = Accent) }
                    TextButton(onClick = { vm.addVoiceToTrack(v) }) { Text("+", fontSize = 18.sp, color = Accent2) }
                }
            }
        }
    }
}
