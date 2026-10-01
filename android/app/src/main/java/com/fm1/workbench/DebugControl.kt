package com.fm1.workbench

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ApplicationInfo
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.File

/**
 * Debug-build-only remote control for hardware tests over adb (UI taps by coordinate are too fragile):
 *   adb shell am broadcast -a com.fm1.workbench.DEBUG -p com.fm1.workbench --es cmd "looper_rec"
 * Results and state go to logcat tag FM1CTL. Not registered in release builds.
 */
class DebugControl(private val vm: AppModel) {
    private val main = Handler(Looper.getMainLooper())

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            val cmd = i.getStringExtra("cmd") ?: return
            main.post { Log.i(TAG, "$cmd -> " + runCatching { run(c, cmd.trim().split(" ")) }.getOrElse { "error: $it" }) }
        }
    }

    private fun state(): String {
        val lp = vm.looper; val al = vm.audioLooper
        return "midi=${vm.status} seq=${vm.seq.playing} looper=${lp.state}/${"%.2f".format(lp.lengthMs / 1000)}s/${lp.layers}L" +
            " player=${vm.player.playing}/${"%.1f".format(vm.player.position() / 1000)}s audio=${al.state}/${"%.2f".format(al.lengthMs / 1000)}s in=${al.inputName} sent=${vm.engine.paramsSent}p/${vm.engine.switches}sw"
    }

    private fun run(c: Context, a: List<String>): String {
        val lp = vm.looper; val al = vm.audioLooper
        when (a[0]) {
            "status" -> {}
            "output" -> vm.setOutputMode(com.fm1.workbench.midi.Fm1Midi.Output.valueOf(a[1].uppercase()))
            "firmware" -> vm.setFirmwareMode(a[1])
            "speech_load" -> vm.loadSpeech()
            "speak" -> { if (a.size > 1) vm.speechText = a.drop(1).joinToString(" "); vm.speechSource = "text"; vm.speak() }
            "soft_bench" -> {                        // ms to render 1 s of audio with N voices (real time needs < 1000)
                val n = a.getOrNull(1)?.toInt() ?: 4
                val s = com.fm1.workbench.core.Fm1Synth(vm.midi.soft.sr)
                for (k in 0 until n) s.queue(intArrayOf(0x90, 48 + k * 3, 100), 0)
                val blk = FloatArray(256); val t0 = System.nanoTime(); var f = 0L
                while (f < vm.midi.soft.sr) { s.render(blk, f); f += 256 }
                return "soft_bench $n voices @${vm.midi.soft.sr} Hz: ${(System.nanoTime() - t0) / 1_000_000} ms per second of audio"
            }
            "speech_status" -> return "speech=${vm.speech != null} playing=${vm.speechPlayer.playing} ${vm.speechPlayer.progress} frames=${vm.speechResult?.frames?.size} " +
                "notes=${vm.speechResult?.notes?.size} soft=${vm.midi.usingSoft} softVoices=${if (vm.midi.usingSoft) vm.midi.soft.synth.activeVoices else -1}"
            "seq_play" -> if (!vm.seq.playing) vm.togglePlay()
            "foreground" -> return "app visible: " + (vm.revision >= 0)
            "seq_stop" -> if (vm.seq.playing) vm.togglePlay()
            "bpm" -> vm.setBpm(a[1].toInt())
            "kit" -> vm.kits.firstOrNull { it.name.equals(a.drop(1).joinToString(" "), true) }?.let(vm::applyKit) ?: return "no such kit"
            "pad" -> vm.hitPad(a[1].toInt(), a.getOrNull(2)?.toInt() ?: 110)
            "looper_rec" -> vm.looperRecord()
            "looper_play" -> vm.looperPlay()
            "looper_stop" -> lp.stop()
            "looper_undo" -> lp.undo()
            "looper_clear" -> lp.clear()
            "looper_quantize" -> lp.quantize = a[1] == "on"
            "audio_rec" -> vm.audioRecord()
            "audio_play" -> vm.audioPlay()
            "audio_stop" -> al.stop()
            "audio_clear" -> al.clear()
            "audio_save" -> return "saved " + al.exportWav(File(c.getExternalFilesDir(null), a.getOrElse(1) { "debug" } + ".wav")).absolutePath
            "player_demo" -> vm.loadDemo()
            "player_play" -> { if (!vm.player.playing) vm.togglePlayer() }
            "player_stop" -> { if (vm.player.playing) vm.togglePlayer() }
            "player_voice" -> {                     // player_voice <ch 1-16> <library name>
                val name = a.drop(2).joinToString(" ")
                val v = (vm.myVoices + vm.library).firstOrNull { it.name.equals(name, true) } ?: return "no voice '$name'"
                vm.player.chans[a[1].toInt() - 1]?.let { it.voice = v.vced.copyOf(); it.voiceName = v.name; it.kit = false } ?: return "no channel"
            }
            "player_tempo" -> vm.player.tempoPct = a[1].toInt()
            "edit_random" -> vm.randomize(a.getOrElse(1) { "any" })
            "edit_param" -> vm.setParam(a[1].toInt(), a[2].toInt())
            "panic" -> vm.panic()
            else -> return "unknown command"
        }
        return state()
    }

    fun register(ctx: Context) {
        if (ctx.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        ctx.registerReceiver(receiver, IntentFilter(ACTION), Context.RECEIVER_EXPORTED)
        Log.i(TAG, "debug control ready: " + state())
    }

    fun unregister(ctx: Context) = runCatching { ctx.unregisterReceiver(receiver) }

    companion object {
        const val TAG = "FM1CTL"
        const val ACTION = "com.fm1.workbench.DEBUG"
    }
}
