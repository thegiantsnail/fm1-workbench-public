package com.fm1.workbench.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Process
import com.fm1.workbench.core.Fm1Synth
import kotlin.math.max

/**
 * The Software FM-1 as an audio output: MIDI bytes from the Engine (which hands them over ~20 ms early with their
 * intended time) are placed at the matching sample of an [Fm1Synth] rendered into an AudioTrack.
 * Frame f is heard at [t0] + f/sr (engine clock, ms); t0 is set when the track starts and re-anchored after an underrun.
 * The render thread stops after a few seconds of silence and restarts on the next message.
 */
class SoftFm1Output(private val clock: () -> Double) {
    /** 32 kHz, not the device rate: the FM-1's output rolls off above ~5.6 kHz anyway, and FM rendering costs per sample
     *  (a single-core emulator could not keep up at 48 kHz). The mixer resamples. */
    val sr: Int = 32000
    val synth = Fm1Synth(sr)

    /** Run the render path once off the audio thread so the JIT has compiled it before the first real note. */
    fun warmUp() {
        val s = Fm1Synth(sr)
        for (k in 0 until 3) s.queue(intArrayOf(0x90, 50 + k * 4, 100), 0)
        val blk = FloatArray(256)
        var f = 0L
        while (f < sr / 2) { s.render(blk, f); f += 256 }
    }
    @Volatile private var thread: Thread? = null
    @Volatile private var framesWritten = 0L
    @Volatile private var t0 = 0.0
    @Volatile private var lastActivity = 0.0
    private val block = 256
    private var latencyMs = 40.0

    private var lastT = 0.0
    private var lastFrame = 0L

    /** Queue MIDI bytes to sound at engine time [atMs] (0 or past = as soon as possible). */
    @Synchronized fun send(bytes: IntArray, atMs: Double) {
        ensureRunning()
        lastActivity = clock()
        val t = max(atMs, clock() + 2.0)
        var frame = max(((t - t0) * sr / 1000.0).toLong(), framesWritten)
        // An underrun re-anchors t0 later, which maps later times to earlier frames than before. Never let a message
        // land before one that was sent earlier with an earlier-or-equal time (a note-off before its note-on hangs it).
        if (t >= lastT) frame = max(frame, lastFrame)
        lastT = t; lastFrame = frame
        synth.queue(bytes, frame)
    }

    fun reset() = synth.reset()

    @Synchronized private fun ensureRunning() {
        if (thread != null) return
        val minBuf = AudioTrack.getMinBufferSize(sr, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_FLOAT)
        val bytes = max(minBuf, block * 4 * 4)
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(sr).setEncoding(AudioFormat.ENCODING_PCM_FLOAT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setBufferSizeInBytes(bytes)
            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        latencyMs = bytes / 4.0 / sr * 1000 + 10
        framesWritten = 0; lastT = 0.0; lastFrame = 0L
        t0 = clock() + latencyMs
        lastActivity = clock()
        track.play()
        thread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            val out = FloatArray(block)
            try {
                while (true) {
                    // underrun (thread starved): frames written are already late - re-anchor so new events stay in the future
                    val due = t0 + framesWritten * 1000.0 / sr
                    if (due < clock()) t0 = clock() + latencyMs - framesWritten * 1000.0 / sr
                    synth.render(out, framesWritten)
                    track.write(out, 0, block, AudioTrack.WRITE_BLOCKING)
                    framesWritten += block
                    if (synth.activeVoices == 0 && clock() - lastActivity > 5000) break
                }
            } finally {
                track.stop(); track.release()
                synchronized(this) { thread = null }
            }
        }, "soft-fm1").apply { isDaemon = true; start() }
    }

    private companion object { const val AudioManagerStream = android.media.AudioManager.STREAM_MUSIC }
}
